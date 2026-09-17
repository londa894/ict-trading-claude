"""Basic macro engine, models and providers (Phase 14)."""

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

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
from app.services.macro import models as macro_models
from app.services.macro.engine import SURPRISE, assess, correlation, pearson, state_for, surprise_sign, trend
from app.services.macro.models import MacroConfig, MacroObservation, MacroSeries, MacroSnapshot
from app.services.macro.providers import (
    FileMacro,
    FixtureMacro,
    MacroUnavailableError,
    UnconfiguredMacro,
    create_macro,
)
from app.services.news.engine import assess as news_assess
from app.services.news.models import CalendarSnapshot, EconomicEvent, NewsConfig

CFG = MacroConfig.from_spec()
NOW = datetime(2024, 4, 18, 15, 1, tzinfo=UTC)  # Thursday 11:01 New York
TODAY = date(2024, 4, 18)
S = MacroSeriesId


def weekdays(end: date, n: int) -> list[date]:
    out, d = [], end
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return out[::-1]


def values(step: float, n: int = 30, base: float = 100.0) -> list[float]:
    """Alternating +-0.1 noise; the last five observations move `step` each (0 = flat)."""
    return [base + 0.1 * (-1) ** i + (step * (i - (n - 6)) if i > n - 6 else 0.0) for i in range(n)]


def series(sid: MacroSeriesId, step: float, end: date = TODAY, n: int = 30) -> MacroSeries:
    obs = [
        MacroObservation(date=d, value=round(v, 6))
        for d, v in zip(weekdays(end, n), values(step, n), strict=True)
    ]
    return MacroSeries(id=sid, name=sid.value, unit="u", observations=obs)


def snapshot(*items: MacroSeries, synthetic: bool = False) -> MacroSnapshot:
    return MacroSnapshot("file", "test", synthetic, NOW, {s.id: s for s in items})


def gold_snapshot(dxy=-1.0, real=-1.0, us2y=0.0, vix=0.0, **kw) -> MacroSnapshot:
    return snapshot(
        series(S.DXY, dxy), series(S.US10Y_REAL, real), series(S.US2Y, us2y), series(S.VIX, vix), **kw
    )


def run(snap, direction=None, symbol="XAUUSD", closes=(), news=None, reason=None):
    return assess(symbol, NOW, TODAY, snap, reason, CFG, "file", direction, closes, news)


# --- series trend ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("step", "direction"),
    [(1.0, SeriesDirection.UP), (-1.0, SeriesDirection.DOWN), (0.0, SeriesDirection.FLAT)],
)
def test_trend_direction_from_z_score(step, direction):
    t = trend(series(S.DXY, step), TODAY, CFG)
    assert t is not None and t.direction is direction and not t.stale
    assert t.last_date == TODAY


def test_trend_needs_min_observations_and_detects_stale():
    assert trend(series(S.DXY, 1.0, n=CFG.min_observations - 1), TODAY, CFG) is None
    old = series(S.DXY, 1.0, end=TODAY - timedelta(days=CFG.max_age_days + 1))
    assert trend(old, TODAY, CFG).stale


def test_trend_without_variance_uses_the_sign_of_the_change():
    flat = MacroSeries(
        id=S.DXY,
        name="x",
        unit="u",
        observations=[MacroObservation(date=d, value=100.0) for d in weekdays(TODAY, 30)],
    )
    t = trend(flat, TODAY, CFG)
    assert t.direction is SeriesDirection.FLAT and t.z_score is None


# --- assessment ----------------------------------------------------------------------


def test_dollar_and_real_yields_down_is_bullish_gold():
    m = run(gold_snapshot(), Direction.BULLISH)
    assert m.available and m.bias is MacroBias.BULLISH and m.score == 0.75  # (3 + 3) / 8
    assert m.state is MacroState.STRONGLY_SUPPORTIVE
    by = {c.series: c for c in m.drivers}
    assert (
        by["DXY"].contribution == 3
        and by["US2Y"].contribution == 0
        and by["VIX"].direction is SeriesDirection.FLAT
    )
    assert run(gold_snapshot(), Direction.BEARISH).state is MacroState.STRONG_CONFLICT
    assert run(gold_snapshot()).state is None  # no setup direction: bias only


def test_mixed_drivers_are_neutral_and_fx_relationships_flip():
    m = run(gold_snapshot(dxy=-1.0, real=1.0), Direction.BULLISH)
    assert m.score == 0.0 and m.bias is MacroBias.NEUTRAL and m.state is MacroState.NEUTRAL
    usdjpy = run(snapshot(series(S.DXY, 1.0), series(S.US10Y, 1.0), series(S.VIX, 0.0)), symbol="USDJPY")
    assert usdjpy.bias is MacroBias.BULLISH and usdjpy.score == pytest.approx(5 / 6, abs=1e-3)


def test_missing_or_stale_required_series_is_unavailable():
    missing = run(snapshot(series(S.US10Y_REAL, -1.0)), Direction.BULLISH)
    assert (
        not missing.available
        and missing.bias is MacroBias.UNAVAILABLE
        and missing.state is MacroState.UNAVAILABLE
    )
    assert "DXY" in missing.reason and missing.score is None
    stale = run(snapshot(series(S.DXY, -1.0, end=TODAY - timedelta(days=10))))
    assert not stale.available and "stale" in stale.reason
    none = run(None, reason="no macro data provider is configured (MACRO_PROVIDER)")
    assert not none.available and "MACRO_PROVIDER" in none.reason and none.provider == "file"


def test_fallback_series_and_unevaluated_optional_drivers():
    m = run(snapshot(series(S.DXY, -1.0), series(S.US10Y, -1.0)), Direction.BULLISH)
    real = next(c for c in m.drivers if c.configured == "US10Y_REAL")
    assert real.series == "US10Y" and real.contribution == 3 and "fallback" in real.detail
    unevaluated = [c.configured for c in m.drivers if c.contribution is None]
    assert unevaluated == ["US2Y", "VIX"] and m.score == 1.0  # missing drivers leave the weight out
    assert "US10Y used instead of US10Y_REAL" in m.warnings


def test_no_drivers_for_an_unknown_market_is_unavailable():
    m = run(gold_snapshot(), symbol="BTCUSD")
    assert not m.available and "no macro drivers" in m.reason


def test_synthetic_snapshot_is_flagged():
    m = run(gold_snapshot(synthetic=True), Direction.BULLISH)
    assert m.is_synthetic and "Synthetic macro data: not scored" in m.warnings


@pytest.mark.parametrize(
    ("score", "bull", "bear"),
    [
        (0.6, MacroState.STRONGLY_SUPPORTIVE, MacroState.STRONG_CONFLICT),
        (0.2, MacroState.SUPPORTIVE, MacroState.CONFLICT),
        (0.19, MacroState.NEUTRAL, MacroState.NEUTRAL),
        (-0.2, MacroState.CONFLICT, MacroState.SUPPORTIVE),
        (-0.59, MacroState.CONFLICT, MacroState.SUPPORTIVE),
        (-1.0, MacroState.STRONG_CONFLICT, MacroState.STRONGLY_SUPPORTIVE),
    ],
)
def test_state_thresholds(score, bull, bear):
    assert state_for(score, Direction.BULLISH, CFG) is bull
    assert state_for(score, Direction.BEARISH, CFG) is bear


# --- correlation regime ----------------------------------------------------------------------


def _closes_following(s: MacroSeries, sign: float):
    first = s.observations[0].value
    return [(o.date, 2000.0 * (1 + sign * (o.value / first - 1) * 5)) for o in s.observations]


def test_pearson():
    assert pearson([1, 2, 3], [2, 4, 6]) == pytest.approx(1.0)
    assert pearson([1, 2, 3], [3, 2, 1]) == pytest.approx(-1.0)
    assert pearson([1, 1, 1], [1, 2, 3]) is None and pearson([1, 2], [1, 2]) is None


def test_correlation_regimes_and_inverted_weight():
    dxy = series(S.DXY, -1.0)
    aligned = correlation(_closes_following(dxy, -1), dxy, -1, CFG)
    assert aligned.regime is CorrelationRegime.ALIGNED and aligned.coefficient < 0
    inverted = correlation(_closes_following(dxy, 1), dxy, -1, CFG)
    assert inverted.regime is CorrelationRegime.INVERTED
    short = correlation(_closes_following(dxy, 1)[:10], dxy, -1, CFG)
    assert short.regime is CorrelationRegime.UNAVAILABLE and "need 20" in short.detail
    assert correlation([], None, -1, CFG).regime is CorrelationRegime.UNAVAILABLE

    m = run(gold_snapshot(), Direction.BULLISH, closes=_closes_following(dxy, 1))
    by = {c.series: c for c in m.drivers}
    assert m.correlation.regime is CorrelationRegime.INVERTED and by["DXY"].weight == 1.5
    assert m.score == round((1.5 + 3) / 6.5, 3) and any("inverted" in w for w in m.warnings)


# --- USD data surprise ----------------------------------------------------------------------


def _news(events, synthetic=False):
    from app.domain.instrument import get_instrument

    snap = CalendarSnapshot(
        "file",
        "t",
        synthetic,
        NOW - timedelta(hours=1),
        NOW - timedelta(days=1),
        NOW + timedelta(days=2),
        tuple(events),
    )
    return news_assess(get_instrument("XAUUSD"), NOW, snap, None, NewsConfig.from_spec(), "file")


def _released(name, actual, forecast, minutes=-90, importance="HIGH"):
    return EconomicEvent(
        id=f"{name}{minutes}",
        country="United States",
        currency="USD",
        name=name,
        scheduled_time=NOW + timedelta(minutes=minutes),
        importance=importance,
        status=EventStatus.RELEASED,
        actual=actual,
        forecast=forecast,
    )


def test_usd_surprise_maps_keywords_to_a_usd_move():
    assert surprise_sign(_news([_released("US CPI m/m", "0.5%", "0.3%")]), CFG)[0] == 1
    assert surprise_sign(_news([_released("Unemployment Rate", "4.1%", "3.9%")]), CFG)[0] == -1
    assert surprise_sign(_news([_released("Fed Chair Speaks", "1", "0")]), CFG)[0] == 0  # no keyword
    assert surprise_sign(_news([_released("US CPI m/m", "0.5%", "0.3%", importance="MEDIUM")]), CFG)[0] == 0
    assert surprise_sign(_news([_released("US CPI m/m", "0.5%", "0.3%")], synthetic=True), CFG) == (0, [])
    assert surprise_sign(None, CFG) == (0, [])
    assert EventImportance(CFG.surprise_min_importance) is EventImportance.HIGH


def test_a_hot_cpi_weighs_against_gold():
    base = run(gold_snapshot(), Direction.BULLISH)
    hot = run(gold_snapshot(), Direction.BULLISH, news=_news([_released("US CPI m/m", "0.5%", "0.3%")]))
    s = next(c for c in hot.drivers if c.series == SURPRISE)
    assert s.contribution == -1 and "USD up" in s.detail
    assert hot.score == round(5 / 9, 3) < base.score and hot.state is MacroState.SUPPORTIVE


# --- config and providers ----------------------------------------------------------------------


def test_config_validation(monkeypatch):
    real = macro_models.load_spec

    def patched(name, key, value):
        def inner(spec):
            data = json.loads(json.dumps(real(spec)))
            if spec == "macro":
                node = data
                *path, last = key
                for k in path:
                    node = node[k]
                node[last] = value
            return data

        return inner

    for key, value in [
        (("thresholds", "supportive"), 0.7),
        (("minObservations",), 5),
        (("surprise", "lookbackHours"), 48),
    ]:
        monkeypatch.setattr(macro_models, "load_spec", patched("macro", key, value))
        with pytest.raises(ValueError):
            MacroConfig.from_spec()
    monkeypatch.setattr(
        macro_models, "load_spec", patched("macro", ("drivers", "XAUUSD", 0, "relationship"), 2)
    )
    with pytest.raises(ValueError):
        MacroConfig.from_spec()


def test_series_must_be_ordered_and_unique():
    d = TODAY
    with pytest.raises(ValidationError):
        MacroSeries(
            id=S.DXY, name="x", unit="u", observations=[{"date": d, "value": 1}, {"date": d, "value": 2}]
        )
    with pytest.raises(ValidationError):
        MacroSeries(
            id=S.DXY,
            name="x",
            unit="u",
            observations=[{"date": d, "value": 1}, {"date": d - timedelta(days=1), "value": 2}],
        )


async def test_unconfigured_and_fixture_providers():
    with pytest.raises(MacroUnavailableError, match="MACRO_PROVIDER"):
        await UnconfiguredMacro().snapshot(NOW)
    a, b = await FixtureMacro().snapshot(NOW), await FixtureMacro().snapshot(NOW)
    assert a.is_synthetic and set(a.series) == set(MacroSeriesId)
    assert a.series[S.DXY].observations == b.series[S.DXY].observations  # deterministic
    assert a.series[S.DXY].observations[-1].date == TODAY and "SYNTHETIC" in a.series[S.DXY].name
    assert isinstance(create_macro("fixture", ""), FixtureMacro)
    assert isinstance(create_macro("unconfigured", ""), UnconfiguredMacro)
    assert isinstance(create_macro("file", "x.json"), FileMacro)
    m = run(a, Direction.BULLISH)
    assert m.available and m.is_synthetic


async def test_file_provider_reads_validates_and_never_echoes_values(tmp_path: Path):
    with pytest.raises(MacroUnavailableError, match="MACRO_FILE_PATH"):
        await FileMacro(None).snapshot(NOW)
    path = tmp_path / "macro.json"
    with pytest.raises(MacroUnavailableError, match="does not exist"):
        await FileMacro(path).snapshot(NOW)
    body = {
        "source": "test",
        "fetchedAt": NOW.isoformat(),
        "series": [json.loads(series(S.DXY, -1.0).model_dump_json(by_alias=True))],
    }
    path.write_text(json.dumps(body), encoding="utf-8")
    provider = FileMacro(path)
    snap = await provider.snapshot(NOW)
    assert not snap.is_synthetic and snap.series[S.DXY].observations[-1].date == TODAY
    body["series"][0]["observations"][0]["value"] = "SECRET-VALUE-123"
    path.write_text(json.dumps(body) + " ", encoding="utf-8")
    with pytest.raises(MacroUnavailableError) as exc:
        await provider.snapshot(NOW)
    assert "series.0.observations.0.value" in str(exc.value) and "SECRET-VALUE-123" not in str(exc.value)
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(MacroUnavailableError, match="unreadable"):
        await provider.snapshot(NOW)
