"""Journal V1 computations (pure).

Pre-trade record: the engine snapshot (Master Decision, evaluation, data report) captured by the server at
logging time, plus the user's fill, is hashed (SHA-256 of canonical JSON) and never updated; every read
re-verifies it. Snapshot timing: POST_ENTRY when the trade opened more than postEntrySnapshotMinutes before
the capture (it is then not what the trader saw before entering).
Detected violations (TRADE entries, from the snapshot): decision UNAVAILABLE, news BLACKOUT, risk LOCKED, no
confirmed plan, direction against the plan, entry beyond the plan entry by more than chaseToleranceR x plan
risk, no stop, risk % above the profile's per-trade limit.
Outcome (append-only revisions): R = s x (exit - entry) / |entry - stop| (s = +1 BULLISH, -1 BEARISH);
planned R to the farthest target; MFE/MAE R from the best/worst price;
entry efficiency = s(mfe - entry) / s(mfe - mae), exit efficiency = s(exit - mae) / s(mfe - mae).
Result: MANUAL / INVALIDATION / NEWS / TRAILING_STOP exits keep their exit state; otherwise
|R| <= breakEven tol BREAK_EVEN, R >= planned R x (1 - fullWin tol) FULL_WIN, R > 0 PARTIAL_WIN,
R <= -1 + fullLoss tol FULL_LOSS, else PARTIAL_LOSS (without a stop only the sign of the P/L is known:
PARTIAL_WIN / BREAK_EVEN / PARTIAL_LOSS).
Process: a win (R > breakEven tol, or positive P/L without a stop) is VALID_WIN without violations, else
BAD_PROCESS_WIN; anything else is VALID_LOSS without violations, else PROCESS_ERROR.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from app.domain.enums import (
    Direction,
    ExitReason,
    ExtremeSource,
    JournalEntryKind,
    ProcessClassification,
    RuleViolation,
    SnapshotTiming,
    TradeResult,
)
from app.services.journal.models import (
    JournalConfig,
    JournalTradeFill,
    RecordOutcomeRequest,
    SnapshotSummary,
)
from app.services.timeframes.core import NEW_YORK

V = RuleViolation
EXIT_STATES = {
    ExitReason.MANUAL: TradeResult.MANUAL_EXIT,
    ExitReason.INVALIDATION: TradeResult.INVALIDATION_EXIT,
    ExitReason.NEWS: TradeResult.NEWS_EXIT,
    ExitReason.TRAILING_STOP: TradeResult.TRAILING_STOP_EXIT,
}


class OutcomeInputError(ValueError):
    """An outcome that contradicts the recorded trade (message never contains submitted values)."""


def canonical_hash(payload: object) -> str:
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sign_of(direction: Direction) -> int:
    return 1 if direction is Direction.BULLISH else -1


def snapshot_timing(
    kind: JournalEntryKind, fill: JournalTradeFill | None, captured_at: datetime, cfg: JournalConfig
) -> SnapshotTiming:
    if kind is not JournalEntryKind.TRADE or fill is None:
        return SnapshotTiming.NOT_APPLICABLE
    late = captured_at - fill.opened_at > timedelta(minutes=cfg.post_entry_snapshot_minutes)
    return SnapshotTiming.POST_ENTRY if late else SnapshotTiming.PRE_ENTRY


def _get(d: Mapping[str, Any] | None, *path: str) -> Any:
    node: Any = d
    for key in path:
        if not isinstance(node, Mapping):
            return None
        node = node.get(key)
    return node


def detect_violations(
    fill: JournalTradeFill,
    decision: Mapping[str, Any],
    evaluation: Mapping[str, Any] | None,
    cfg: JournalConfig,
) -> list[RuleViolation]:
    out: list[RuleViolation] = []
    if decision.get("verdict") == "UNAVAILABLE":
        out.append(V.TRADED_ON_UNAVAILABLE_DECISION)
    if _get(decision, "newsState", "state") == "BLACKOUT":
        out.append(V.TRADED_DURING_NEWS_BLACKOUT)
    if decision.get("riskStatus") == "LOCKED":
        out.append(V.TRADED_WHILE_RISK_LOCKED)
    plan = _get(evaluation, "plan")
    if not isinstance(plan, Mapping):
        out.append(V.NO_CONFIRMED_PLAN)  # an unknown plan is treated as no plan (fail safe)
    elif plan.get("direction") != fill.direction.value:
        out.append(V.AGAINST_PLAN_DIRECTION)
    else:
        s = sign_of(fill.direction)
        if s * (fill.entry - float(plan["entry"])) > cfg.chase_tolerance_r * float(plan["risk"]):
            out.append(V.CHASED_ENTRY)
    if fill.stop is None:
        out.append(V.NO_STOP_PLACED)
    limit = _get(evaluation, "risk", "limits", "riskPerTradePct")
    if fill.risk_pct is not None and isinstance(limit, int | float) and fill.risk_pct > float(limit) + 1e-9:
        out.append(V.RISK_ABOVE_LIMIT)
    return out


def _compact(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, Mapping):
        keys = ("classification", "strength", "type", "state", "direction", "timeframe")
        picked = [str(value[k]) for k in keys if value.get(k) is not None]
        return " ".join(picked) if picked else json.dumps(value, sort_keys=True, default=str)[:160]
    return str(value)


def summarize(
    decision: Mapping[str, Any],
    evaluation: Mapping[str, Any] | None,
    data: Mapping[str, Any] | None,
    captured_at: datetime,
) -> SnapshotSummary:
    plan = _get(evaluation, "plan")
    plan = plan if isinstance(plan, Mapping) else None
    raw_session = decision.get("sessionState")
    session: Mapping[str, Any] = raw_session if isinstance(raw_session, Mapping) else {}
    targets = [float(plan[k]) for k in ("tp1", "tp2", "tp3") if plan and plan.get(k) is not None]
    return SnapshotSummary(
        captured_at=captured_at,
        day_of_week=captured_at.astimezone(NEW_YORK).strftime("%A"),
        active_sessions=list(session.get("activeSessions") or []),
        active_kill_zones=list(session.get("activeKillZones") or []),
        time_quality=session.get("timeQuality"),
        verdict=str(decision.get("verdict")),
        data_quality=str(decision.get("dataQuality")),
        engine_authorization=str(_get(evaluation, "authority") or "NOT_AUTHORIZED"),
        execution_timeframe=_get(data, "timeframe"),
        htf_bias=decision.get("htfBias"),
        primary_dol=decision.get("primaryDol"),
        liquidity_event=decision.get("liquidityEvent"),
        structure_event=decision.get("structureEvent"),
        displacement=decision.get("displacement"),
        pd_array=_compact(decision.get("pdArray")),
        no_wick=_compact(decision.get("noWickState")),
        news_state=_get(decision, "newsState", "state"),
        macro_bias=_get(decision, "macroState", "bias"),
        macro_state=_get(decision, "macroState", "state"),
        setup_type=decision.get("setupType"),
        setup_state=decision.get("setupState"),
        setup_score=decision.get("setupScore"),
        setup_grade=decision.get("setupGrade"),
        confidence=decision.get("decisionConfidence"),
        plan_entry=float(plan["entry"]) if plan else None,
        plan_stop=float(plan["stop"]) if plan else None,
        plan_targets=targets,
        plan_rr=float(plan["rr1"]) if plan and plan.get("rr1") is not None else None,
        risk_status=decision.get("riskStatus"),
        blockers=[str(b) for b in decision.get("blockers") or []],
        is_synthetic=bool(_get(data, "isSynthetic")),
        strategy_version=str(decision.get("strategyVersion")),
    )


@dataclass(frozen=True)
class Extremes:
    mfe: float | None
    mae: float | None
    source: ExtremeSource
    synthetic: bool
    detail: str | None


@dataclass(frozen=True)
class OutcomeMetrics:
    result: TradeResult
    r_multiple: float | None
    planned_r: float | None
    mfe_r: float | None
    mae_r: float | None
    entry_efficiency: float | None
    exit_efficiency: float | None
    duration_minutes: float
    violations: list[RuleViolation]
    classification: ProcessClassification


def check_outcome(fill: JournalTradeFill, req: RecordOutcomeRequest, now: datetime) -> None:
    if req.exited_at <= fill.opened_at:
        raise OutcomeInputError("exitedAt must be after the trade's openedAt")
    if req.exited_at > now + timedelta(minutes=1):
        raise OutcomeInputError("exitedAt cannot be in the future")
    s = sign_of(fill.direction)
    high = max(fill.entry, req.exit_price)
    low = min(fill.entry, req.exit_price)
    best, worst = (high, low) if s > 0 else (low, high)
    if req.mfe_price is not None and s * (req.mfe_price - best) < -1e-9:
        raise OutcomeInputError("mfePrice must be at least as favourable as the entry and the exit")
    if req.mae_price is not None and s * (req.mae_price - worst) > 1e-9:
        raise OutcomeInputError("maePrice must be at least as adverse as the entry and the exit")


def _round(v: float | None, n: int = 3) -> float | None:
    return None if v is None else round(v, n)


def compute_outcome(
    fill: JournalTradeFill,
    req: RecordOutcomeRequest,
    extremes: Extremes,
    detected: list[RuleViolation],
    cfg: JournalConfig,
) -> OutcomeMetrics:
    s = sign_of(fill.direction)
    risk = abs(fill.entry - fill.stop) if fill.stop is not None else None
    pnl = s * (req.exit_price - fill.entry)
    r = pnl / risk if risk else None
    planned = max((s * (t - fill.entry) / risk for t in fill.targets), default=None) if risk else None
    mfe_r = s * (extremes.mfe - fill.entry) / risk if risk and extremes.mfe is not None else None
    mae_r = s * (extremes.mae - fill.entry) / risk if risk and extremes.mae is not None else None
    entry_eff = exit_eff = None
    if extremes.mfe is not None and extremes.mae is not None:
        span = s * (extremes.mfe - extremes.mae)
        if span > 0:
            entry_eff = s * (extremes.mfe - fill.entry) / span
            exit_eff = s * (req.exit_price - extremes.mae) / span

    if req.exit_reason in EXIT_STATES:
        result = EXIT_STATES[req.exit_reason]
    elif r is None:
        result = (
            TradeResult.PARTIAL_WIN
            if pnl > 0
            else TradeResult.PARTIAL_LOSS
            if pnl < 0
            else TradeResult.BREAK_EVEN
        )
    elif abs(r) <= cfg.break_even_tolerance_r:
        result = TradeResult.BREAK_EVEN
    elif r > 0:
        full = planned is not None and r >= planned * (1 - cfg.full_win_tolerance_fraction)
        result = TradeResult.FULL_WIN if full else TradeResult.PARTIAL_WIN
    else:
        result = TradeResult.FULL_LOSS if r <= -1 + cfg.full_loss_tolerance_r else TradeResult.PARTIAL_LOSS

    win = r > cfg.break_even_tolerance_r if r is not None else pnl > 0
    violations = list(dict.fromkeys([*detected, *req.reported_violations]))
    if win:
        classification = (
            ProcessClassification.BAD_PROCESS_WIN if violations else ProcessClassification.VALID_WIN
        )
    else:
        classification = (
            ProcessClassification.PROCESS_ERROR if violations else ProcessClassification.VALID_LOSS
        )
    return OutcomeMetrics(
        result=result,
        r_multiple=_round(r),
        planned_r=_round(planned),
        mfe_r=_round(mfe_r),
        mae_r=_round(mae_r),
        entry_efficiency=_round(entry_eff),
        exit_efficiency=_round(exit_eff),
        duration_minutes=round((req.exited_at - fill.opened_at).total_seconds() / 60, 2),
        violations=violations,
        classification=classification,
    )


def candle_extremes(
    fill: JournalTradeFill,
    exit_price: float,
    highs_lows: list[tuple[float, float]],
    synthetic: bool,
    detail: str,
) -> Extremes:
    """Best/worst price from candles overlapping the trade, widened to the fills themselves."""
    if not highs_lows:
        return Extremes(None, None, ExtremeSource.UNAVAILABLE, False, "no candles cover the trade")
    high = max([h for h, _ in highs_lows] + [fill.entry, exit_price])
    low = min([lo for _, lo in highs_lows] + [fill.entry, exit_price])
    best, worst = (high, low) if fill.direction is Direction.BULLISH else (low, high)
    return Extremes(best, worst, ExtremeSource.CANDLES, synthetic, detail)
