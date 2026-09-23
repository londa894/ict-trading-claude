import itertools
from datetime import UTC, datetime

import pytest

from app.domain.enums import Blocker as B
from app.domain.enums import DataQuality, IssueSeverity, MarketStatus, ValidationIssueCode, Verdict
from app.domain.instrument import get_instrument
from app.domain.issues import ValidationIssue
from app.services.market_state.gate import GateInput, enforce_verdict_authority, evaluate

NOW = datetime(2024, 1, 9, 10, 0, tzinfo=UTC)
XAU = get_instrument("XAUUSD")


def gate(**kw):
    base = dict(
        symbol="XAUUSD",
        now=NOW,
        instrument=XAU,
        provider_available=True,
        is_synthetic=False,
        data_quality=DataQuality.CURRENT,
        issues=(),
        market_status=MarketStatus.OPEN,
    )
    base.update(kw)
    return evaluate(GateInput(**base))


def test_best_case_is_wait_with_gates_not_implemented():
    d = gate()
    assert d.verdict is Verdict.WAIT
    assert B.ANALYSIS_GATES_NOT_IMPLEMENTED in d.blockers
    assert B.INSTRUMENT_SPEC_MISSING in d.blockers
    assert d.direction is None and d.entry_zone is None and d.stop is None
    assert d.strategy_version == "0.20.0-phase20"
    assert d.setup_state == "NOT_EVALUATED" and d.htf_bias == "UNKNOWN"


@pytest.mark.parametrize(
    ("kw", "blocker"),
    [
        (dict(instrument=None), B.UNKNOWN_SYMBOL),
        (dict(provider_available=False), B.PROVIDER_UNAVAILABLE),
        (dict(is_synthetic=True), B.DATA_SYNTHETIC),
        (dict(data_quality=DataQuality.INVALID), B.DATA_INVALID),
        (dict(data_quality=DataQuality.STALE), B.DATA_STALE),
        (dict(data_quality=DataQuality.DISCONNECTED), B.DATA_DISCONNECTED),
    ],
)
def test_unusable_data_is_unavailable(kw, blocker):
    d = gate(**kw)
    assert d.verdict is Verdict.UNAVAILABLE
    assert blocker in d.blockers


def test_provider_unavailable_forces_disconnected_quality():
    d = gate(provider_available=False, data_quality=DataQuality.CURRENT)
    assert d.data_quality is DataQuality.DISCONNECTED


@pytest.mark.parametrize(
    ("kw", "blocker"),
    [
        (dict(data_quality=DataQuality.DELAYED), B.DATA_DELAYED),
        (dict(market_status=MarketStatus.CLOSED), B.MARKET_CLOSED),
        (dict(market_status=MarketStatus.DAILY_BREAK), B.MARKET_CLOSED),
        (
            dict(
                issues=(
                    ValidationIssue(
                        code=ValidationIssueCode.MISSING_BARS, severity=IssueSeverity.WARNING, message="g"
                    ),
                )
            ),
            B.DATA_GAP,
        ),
        (
            dict(
                issues=(
                    ValidationIssue(
                        code=ValidationIssueCode.SUSPECT_BAD_TICK, severity=IssueSeverity.WARNING, message="s"
                    ),
                )
            ),
            B.DATA_SUSPECT_BAD_TICK,
        ),
    ],
)
def test_degraded_but_usable_is_wait(kw, blocker):
    d = gate(**kw)
    assert d.verdict is Verdict.WAIT
    assert blocker in d.blockers


def test_no_input_combination_can_produce_directional_verdict():
    combos = itertools.product(
        [XAU, None],
        [True, False],
        [True, False],
        list(DataQuality),
        list(MarketStatus),
    )
    for instrument, provider_ok, synthetic, quality, status in combos:
        d = gate(
            instrument=instrument,
            provider_available=provider_ok,
            is_synthetic=synthetic,
            data_quality=quality,
            market_status=status,
        )
        assert d.verdict in (Verdict.WAIT, Verdict.UNAVAILABLE)
        assert d.direction is None
        assert d.blockers, "a fail-safe decision must always explain itself"


@pytest.mark.parametrize("forbidden", [Verdict.LONG, Verdict.SHORT, Verdict.NO_TRADE])
def test_authority_guard_converts_directional_verdict_to_integrity_failure(forbidden):
    tampered = gate().model_copy(update={"verdict": forbidden, "direction": "LONG"})
    guarded = enforce_verdict_authority(tampered)
    assert guarded.verdict is Verdict.UNAVAILABLE
    assert guarded.direction is None
    assert B.SYSTEM_INTEGRITY_FAILURE in guarded.blockers


def test_blockers_are_deterministically_ordered():
    a = gate(is_synthetic=True, data_quality=DataQuality.STALE, market_status=MarketStatus.CLOSED)
    b = gate(market_status=MarketStatus.CLOSED, data_quality=DataQuality.STALE, is_synthetic=True)
    assert a.blockers == b.blockers
    order = list(B)
    assert a.blockers == sorted(a.blockers, key=order.index)


@pytest.mark.parametrize("state", ["LONG_READY", "SHORT_READY", "ACTIVE", "CLOSED"])
def test_authority_guard_rejects_ready_setup_states(state):
    tampered = gate().model_copy(update={"setup_state": state})
    guarded = enforce_verdict_authority(tampered)
    assert guarded.verdict is Verdict.UNAVAILABLE and guarded.setup_state == "BLOCKED"
    assert B.SYSTEM_INTEGRITY_FAILURE in guarded.blockers


@pytest.mark.parametrize("state", ["WAITING_FOR_CONFIRMATION", "SETUP_ARMED", "NO_SETUP", "NOT_EVALUATED"])
def test_authority_guard_allows_progress_setup_states(state):
    decision = gate().model_copy(update={"setup_state": state})
    assert enforce_verdict_authority(decision) == decision
