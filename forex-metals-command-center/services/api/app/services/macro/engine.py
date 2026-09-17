"""Basic macro assessment (pure).

Series trend: change over `lookback` observations; z = change / (stdev of daily changes over the
volatility window x sqrt(lookback)). UP when z > flatZ, DOWN when z < -flatZ, else FLAT. Stale when the
last observation is older than maxAgeDays.
Drivers (macro.json per market): contribution = direction sign x relationship x weight; a missing or stale
primary uses its fallback; required series missing or stale -> UNAVAILABLE.
USD data surprise: released HIGH+ USD events in the look-back whose name maps to a USD sign (beat of an
inflation/growth print = USD up; beat of an unemployment/claims print = USD down); counted like DXY.
Correlation regime: Pearson correlation of the market's D1 close returns and the correlation series'
changes on matching trading days; INVERTED (sign opposite to the relationship) halves that driver's weight.
score = sum(contributions) / sum(evaluated weights) in [-1, 1].
Bias: BULLISH >= supportive, BEARISH <= -supportive, else NEUTRAL.
State versus a setup direction D uses s = score (BULLISH) or -score (BEARISH):
  >= stronglySupportive STRONGLY_SUPPORTIVE, >= supportive SUPPORTIVE, > -supportive NEUTRAL,
  > -stronglySupportive CONFLICT, else STRONG_CONFLICT.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import date, datetime
from itertools import pairwise
from statistics import pstdev

from app.contracts import strategy_version
from app.domain.enums import (
    CorrelationRegime,
    Direction,
    EventImportance,
    EventStatus,
    MacroBias,
    MacroSeriesId,
    MacroState,
    SeriesDirection,
)
from app.services.macro.models import (
    CorrelationInfo,
    DriverContribution,
    MacroAssessment,
    MacroConfig,
    MacroSeries,
    MacroSnapshot,
    SeriesTrend,
)
from app.services.news.models import NewsAssessment

SURPRISE = "USD_DATA_SURPRISE"
SIGN = {SeriesDirection.UP: 1, SeriesDirection.DOWN: -1, SeriesDirection.FLAT: 0}
IMPORTANCE_ORDER = list(EventImportance)
RELEASED = frozenset({EventStatus.RELEASED, EventStatus.REVISED, EventStatus.COMPLETED})


def trend(series: MacroSeries, today: date, cfg: MacroConfig) -> SeriesTrend | None:
    obs = series.observations
    if len(obs) < cfg.min_observations:
        return None
    values = [o.value for o in obs]
    change = values[-1] - values[-1 - cfg.lookback]
    diffs = [b - a for a, b in pairwise(values)][-cfg.volatility_window :]
    sd = pstdev(diffs) if len(diffs) > 1 else 0.0
    z = change / (sd * math.sqrt(cfg.lookback)) if sd > 0 else None
    if z is None:
        direction = (
            SeriesDirection.FLAT
            if change == 0
            else (SeriesDirection.UP if change > 0 else SeriesDirection.DOWN)
        )
    elif z > cfg.flat_z:
        direction = SeriesDirection.UP
    elif z < -cfg.flat_z:
        direction = SeriesDirection.DOWN
    else:
        direction = SeriesDirection.FLAT
    return SeriesTrend(
        id=series.id,
        last_date=obs[-1].date,
        last_value=values[-1],
        change=round(change, 6),
        z_score=round(z, 3) if z is not None else None,
        direction=direction,
        stale=(today - obs[-1].date).days > cfg.max_age_days,
    )


def pearson(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if sx == 0 or sy == 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True)) / (sx * sy)


def correlation(
    market_closes: Sequence[tuple[date, float]],
    series: MacroSeries | None,
    relationship: int,
    cfg: MacroConfig,
) -> CorrelationInfo:
    sid = cfg.correlation_series
    if series is None:
        return CorrelationInfo(
            series=sid,
            observations=0,
            coefficient=None,
            regime=CorrelationRegime.UNAVAILABLE,
            detail="series missing",
        )
    market = {d: c for d, c in market_closes}
    macro = {o.date: o.value for o in series.observations}
    days = sorted(set(market) & set(macro))
    pairs: list[tuple[float, float]] = []
    for prev, cur in pairwise(days):
        if market[prev] > 0 and macro[prev] != 0:
            pairs.append((market[cur] / market[prev] - 1, macro[cur] / macro[prev] - 1))
    pairs = pairs[-cfg.correlation_observations :]
    if len(pairs) < cfg.correlation_observations:
        return CorrelationInfo(
            series=sid,
            observations=len(pairs),
            coefficient=None,
            regime=CorrelationRegime.UNAVAILABLE,
            detail=f"{len(pairs)} matching daily changes (need {cfg.correlation_observations})",
        )
    coef = pearson([p[0] for p in pairs], [p[1] for p in pairs])
    if coef is None:
        return CorrelationInfo(
            series=sid,
            observations=len(pairs),
            coefficient=None,
            regime=CorrelationRegime.UNAVAILABLE,
            detail="no variance",
        )
    if abs(coef) < cfg.correlation_min_abs:
        regime, detail = CorrelationRegime.WEAK, "the usual relationship is weak right now"
    elif coef * relationship > 0:
        regime, detail = CorrelationRegime.ALIGNED, "moving with the configured relationship"
    else:
        regime, detail = (
            CorrelationRegime.INVERTED,
            f"{sid.value} weight reduced: the usual relationship is inverted",
        )
    return CorrelationInfo(
        series=sid, observations=len(pairs), coefficient=round(coef, 3), regime=regime, detail=detail
    )


def surprise_sign(news: NewsAssessment | None, cfg: MacroConfig) -> tuple[int, list[str]]:
    """Net USD move implied by released surprises (+1 USD up, -1 USD down, 0 none) and the events used."""
    if news is None or news.calendar.is_synthetic or not news.calendar.available:
        return 0, []  # a synthetic or unusable calendar never supplies market facts
    total, used = 0, []
    floor = IMPORTANCE_ORDER.index(cfg.surprise_min_importance)
    for e in news.events:
        if e.currency != "USD" or e.surprise is None or e.surprise == 0 or e.status not in RELEASED:
            continue
        if (
            IMPORTANCE_ORDER.index(e.importance) < floor
            or not -cfg.surprise_lookback_hours * 60 <= e.minutes_to_event <= 0
        ):
            continue
        name = e.name.lower()
        keyword = (
            1
            if any(k in name for k in cfg.usd_positive)
            else -1
            if any(k in name for k in cfg.usd_negative)
            else 0
        )
        if keyword == 0:
            continue
        move = keyword * (1 if e.surprise > 0 else -1)
        total += move
        used.append(f"{e.name} surprise {e.surprise} -> USD {'up' if move > 0 else 'down'}")
    return (1 if total > 0 else -1 if total < 0 else 0), used


def state_for(score: float, direction: Direction, cfg: MacroConfig) -> MacroState:
    s = score if direction is Direction.BULLISH else -score
    if s >= cfg.strongly_supportive:
        return MacroState.STRONGLY_SUPPORTIVE
    if s >= cfg.supportive:
        return MacroState.SUPPORTIVE
    if s > -cfg.supportive:
        return MacroState.NEUTRAL
    if s > -cfg.strongly_supportive:
        return MacroState.CONFLICT
    return MacroState.STRONG_CONFLICT


def _unavailable(
    symbol: str,
    now: datetime,
    direction: Direction | None,
    reason: str,
    cfg: MacroConfig,
    snapshot: MacroSnapshot | None,
    provider: str,
) -> MacroAssessment:
    return MacroAssessment(
        symbol=symbol,
        bias=MacroBias.UNAVAILABLE,
        score=None,
        state=MacroState.UNAVAILABLE if direction is not None else None,
        direction=direction,
        drivers=[],
        series=[],
        correlation=None,
        provider=snapshot.provider if snapshot else provider,
        source=snapshot.source if snapshot else None,
        is_synthetic=snapshot.is_synthetic if snapshot else False,
        available=False,
        fetched_at=snapshot.fetched_at if snapshot else None,
        reason=reason,
        warnings=[],
        thresholds={"supportive": cfg.supportive, "stronglySupportive": cfg.strongly_supportive},
        strategy_version=strategy_version(),
        generated_at=now,
    )


def assess(
    symbol: str,
    now: datetime,
    today: date,
    snapshot: MacroSnapshot | None,
    unavailable_reason: str | None,
    cfg: MacroConfig,
    provider: str,
    direction: Direction | None = None,
    market_closes: Sequence[tuple[date, float]] = (),
    news: NewsAssessment | None = None,
) -> MacroAssessment:
    if snapshot is None:
        return _unavailable(
            symbol, now, direction, unavailable_reason or "macro data unavailable", cfg, None, provider
        )
    drivers = cfg.drivers.get(symbol)
    if not drivers:
        return _unavailable(
            symbol, now, direction, f"no macro drivers configured for {symbol}", cfg, snapshot, provider
        )
    trends = {sid: t for sid, s in snapshot.series.items() if (t := trend(s, today, cfg)) is not None}
    usable = {sid: t for sid, t in trends.items() if not t.stale}
    missing = [sid.value for sid in cfg.required if sid not in usable]
    if missing:
        why = "stale" if any(MacroSeriesId(m) in trends for m in missing) else "missing or too short"
        return _unavailable(
            symbol,
            now,
            direction,
            f"required macro series {', '.join(missing)} {why}",
            cfg,
            snapshot,
            provider,
        )

    warnings: list[str] = []
    corr_driver = next((d for d in drivers if d.series is cfg.correlation_series), None)
    corr = (
        correlation(market_closes, snapshot.series.get(cfg.correlation_series), corr_driver.relationship, cfg)
        if corr_driver
        else None
    )
    if corr is not None and corr.regime is CorrelationRegime.INVERTED:
        warnings.append(corr.detail)

    contributions: list[DriverContribution] = []
    for d in drivers:
        used = d.series if d.series in usable else d.fallback if d.fallback in usable else None
        if used is None:
            contributions.append(
                DriverContribution(
                    series=d.series.value,
                    configured=d.series.value,
                    relationship=d.relationship,
                    weight=d.weight,
                    direction=None,
                    contribution=None,
                    detail="missing or stale: not evaluated",
                )
            )
            warnings.append(f"{d.series.value} not evaluated")
            continue
        weight = d.weight
        if corr is not None and used is cfg.correlation_series and corr.regime is CorrelationRegime.INVERTED:
            weight *= cfg.inverted_weight_factor
        t = usable[used]
        value = SIGN[t.direction] * d.relationship * weight
        detail = f"{used.value} {t.direction.value} (z {t.z_score})"
        if used is not d.series:
            detail += f"; fallback for {d.series.value}"
            warnings.append(f"{used.value} used instead of {d.series.value}")
        contributions.append(
            DriverContribution(
                series=used.value,
                configured=d.series.value,
                relationship=d.relationship,
                weight=weight,
                direction=t.direction,
                contribution=round(value, 4),
                detail=detail,
            )
        )

    usd_move, events = surprise_sign(news, cfg)
    dxy = next((d for d in drivers if d.series is MacroSeriesId.DXY), None)
    if dxy is not None and usd_move != 0:
        contributions.append(
            DriverContribution(
                series=SURPRISE,
                configured=SURPRISE,
                relationship=dxy.relationship,
                weight=cfg.surprise_weight,
                direction=SeriesDirection.UP if usd_move > 0 else SeriesDirection.DOWN,
                contribution=float(usd_move * dxy.relationship * cfg.surprise_weight),
                detail="; ".join(events),
            )
        )

    evaluated = [c for c in contributions if c.contribution is not None]
    total_weight = sum(c.weight for c in evaluated)
    score = round(sum(c.contribution or 0.0 for c in evaluated) / total_weight, 3) if total_weight else 0.0
    bias = (
        MacroBias.BULLISH
        if score >= cfg.supportive
        else MacroBias.BEARISH
        if score <= -cfg.supportive
        else MacroBias.NEUTRAL
    )
    if snapshot.is_synthetic:
        warnings.append("Synthetic macro data: not scored")
    return MacroAssessment(
        symbol=symbol,
        bias=bias,
        score=score,
        state=state_for(score, direction, cfg) if direction is not None else None,
        direction=direction,
        drivers=contributions,
        series=sorted(trends.values(), key=lambda t: t.id.value),
        correlation=corr,
        provider=snapshot.provider,
        source=snapshot.source,
        is_synthetic=snapshot.is_synthetic,
        available=True,
        fetched_at=snapshot.fetched_at,
        reason=None,
        warnings=list(dict.fromkeys(warnings)),
        thresholds={"supportive": cfg.supportive, "stronglySupportive": cfg.strongly_supportive},
        strategy_version=strategy_version(),
        generated_at=now,
    )
