"""Fail-safe decision gate (pure function).

Phase 0 verdict authority is FAIL_SAFE_ONLY: the gate can only emit WAIT or UNAVAILABLE.
    Unusable data            -> UNAVAILABLE
    Usable data, incomplete  -> WAIT (analysis gates do not exist yet)
LONG / SHORT / NO_TRADE require engines from later phases. If anything ever produces them while the
strategy spec says FAIL_SAFE_ONLY, the decision is converted to UNAVAILABLE + SYSTEM_INTEGRITY_FAILURE.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.contracts import load_spec, strategy_version
from app.domain.decision import NOT_EVALUATED, UNKNOWN, MasterDecision
from app.domain.enums import (
    AUTHORITY_SETUP_STATES,
    Blocker,
    DataQuality,
    DecisionConfidence,
    MarketStatus,
    SetupState,
    ValidationIssueCode,
    Verdict,
)
from app.domain.instrument import Instrument
from app.domain.issues import ValidationIssue

# Any of these makes the data unusable -> UNAVAILABLE.
UNAVAILABLE_BLOCKERS = frozenset(
    {
        Blocker.SYSTEM_INTEGRITY_FAILURE,
        Blocker.UNKNOWN_SYMBOL,
        Blocker.PROVIDER_UNAVAILABLE,
        Blocker.NO_DATA,
        Blocker.DATA_INVALID,
        Blocker.DATA_STALE,
        Blocker.DATA_DISCONNECTED,
        Blocker.DATA_SYNTHETIC,
    }
)

FAIL_SAFE_VERDICTS = frozenset({Verdict.WAIT, Verdict.UNAVAILABLE})

_QUALITY_BLOCKER = {
    DataQuality.INVALID: Blocker.DATA_INVALID,
    DataQuality.STALE: Blocker.DATA_STALE,
    DataQuality.DISCONNECTED: Blocker.DATA_DISCONNECTED,
    DataQuality.DELAYED: Blocker.DATA_DELAYED,
}

_ISSUE_BLOCKER = {
    ValidationIssueCode.MISSING_BARS: Blocker.DATA_GAP,
    ValidationIssueCode.SUSPECT_BAD_TICK: Blocker.DATA_SUSPECT_BAD_TICK,
    ValidationIssueCode.EMPTY_SERIES: Blocker.NO_DATA,
}


@dataclass(frozen=True)
class GateInput:
    symbol: str
    now: datetime
    instrument: Instrument | None
    provider_available: bool
    is_synthetic: bool
    data_quality: DataQuality
    issues: tuple[ValidationIssue, ...] = ()
    market_status: MarketStatus = MarketStatus.UNKNOWN


def ordered_blockers(blockers: set[Blocker]) -> list[Blocker]:
    order = list(Blocker)
    return sorted(blockers, key=order.index)


def _next_required_event(blockers: set[Blocker]) -> str:
    if Blocker.UNKNOWN_SYMBOL in blockers:
        return "Select a supported instrument"
    if Blocker.PROVIDER_UNAVAILABLE in blockers:
        return "Configure and connect an independent market-data provider"
    if Blocker.DATA_SYNTHETIC in blockers:
        return "Replace synthetic fixture data with a real independent provider"
    if blockers & {Blocker.DATA_INVALID, Blocker.NO_DATA, Blocker.DATA_STALE, Blocker.DATA_DISCONNECTED}:
        return "Restore valid, current market data"
    return "Directional verdicts are disabled: verdict authority is FAIL_SAFE_ONLY (an explicit decision)"


def evaluate(gate: GateInput) -> MasterDecision:
    blockers: set[Blocker] = {Blocker.ANALYSIS_GATES_NOT_IMPLEMENTED}
    quality = gate.data_quality

    if gate.instrument is None:
        blockers.add(Blocker.UNKNOWN_SYMBOL)
    else:
        if gate.instrument.spec is None:
            blockers.add(Blocker.INSTRUMENT_SPEC_MISSING)
        if not gate.instrument.deeply_validated:
            # Strategy parameters are validated on XAUUSD first; other markets are research-only (WAIT-class).
            blockers.add(Blocker.MARKET_NOT_VALIDATED)

    if not gate.provider_available:
        blockers |= {Blocker.PROVIDER_UNAVAILABLE, Blocker.DATA_DISCONNECTED}
        quality = DataQuality.DISCONNECTED
    if gate.is_synthetic:
        blockers.add(Blocker.DATA_SYNTHETIC)
    if quality in _QUALITY_BLOCKER:
        blockers.add(_QUALITY_BLOCKER[quality])
    for issue in gate.issues:
        if issue.code in _ISSUE_BLOCKER:
            blockers.add(_ISSUE_BLOCKER[issue.code])
    if gate.market_status in (MarketStatus.CLOSED, MarketStatus.DAILY_BREAK):
        blockers.add(Blocker.MARKET_CLOSED)

    verdict = Verdict.UNAVAILABLE if blockers & UNAVAILABLE_BLOCKERS else Verdict.WAIT
    return enforce_verdict_authority(_decision(gate.symbol, gate.now, verdict, quality, blockers))


def _decision(
    symbol: str, now: datetime, verdict: Verdict, quality: DataQuality, blockers: set[Blocker]
) -> MasterDecision:
    return MasterDecision(
        symbol=symbol.upper(),
        verdict=verdict,
        setup_state=NOT_EVALUATED,
        decision_confidence=DecisionConfidence.LOW,
        htf_bias=UNKNOWN,
        risk_status=NOT_EVALUATED,
        blockers=ordered_blockers(blockers),
        next_required_event=_next_required_event(blockers),
        data_quality=quality,
        strategy_version=strategy_version(),
        updated_at=now,
    )


def enforce_verdict_authority(decision: MasterDecision) -> MasterDecision:
    authority = load_spec("strategy_version")["verdictAuthority"]
    # A READY/ACTIVE setup state would claim trade authority the system does not have yet.
    authority_state = decision.setup_state in {s.value for s in AUTHORITY_SETUP_STATES}
    if authority == "FAIL_SAFE_ONLY" and (decision.verdict not in FAIL_SAFE_VERDICTS or authority_state):
        blockers = set(decision.blockers) | {Blocker.SYSTEM_INTEGRITY_FAILURE}
        return decision.model_copy(
            update={
                "verdict": Verdict.UNAVAILABLE,
                "direction": None,
                "decision_confidence": DecisionConfidence.LOW,
                "blockers": ordered_blockers(blockers),
                "next_required_event": "Investigate system integrity failure",
                "setup_state": SetupState.BLOCKED.value if authority_state else decision.setup_state,
            }
        )
    return decision
