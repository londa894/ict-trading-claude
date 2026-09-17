"""Journal V1 engine, models and config (Phase 15)."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

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
from app.services.journal import models as journal_models
from app.services.journal.engine import (
    Extremes,
    OutcomeInputError,
    candle_extremes,
    canonical_hash,
    check_outcome,
    compute_outcome,
    detect_violations,
    snapshot_timing,
    summarize,
)
from app.services.journal.models import (
    CreateJournalEntryRequest,
    JournalConfig,
    JournalTradeFill,
    RecordOutcomeRequest,
)

CFG = JournalConfig.from_spec()
NOW = datetime(2024, 4, 18, 15, 0, tzinfo=UTC)  # Thursday
V = RuleViolation
NONE = Extremes(None, None, ExtremeSource.UNAVAILABLE, False, None)


def fill(direction=Direction.BULLISH, entry=2000.0, stop=1990.0, targets=(2030.0,), **kw) -> JournalTradeFill:
    base = dict(
        direction=direction, entry=entry, stop=stop, targets=list(targets), opened_at=NOW - timedelta(hours=2)
    )
    base.update(kw)
    return JournalTradeFill(**base)


def outcome(exit_price, reason=ExitReason.TARGET, **kw) -> RecordOutcomeRequest:
    return RecordOutcomeRequest(
        exit_price=exit_price, exited_at=NOW - timedelta(minutes=30), exit_reason=reason, **kw
    )


def decision(**over):
    base = {
        "verdict": "WAIT",
        "dataQuality": "CURRENT",
        "riskStatus": "WITHIN_LIMITS",
        "newsState": {"state": "CLEAR"},
        "macroState": {"bias": "BULLISH", "state": "SUPPORTIVE"},
        "sessionState": {
            "activeSessions": ["NEW_YORK"],
            "activeKillZones": ["NY_AM"],
            "timeQuality": "IDEAL",
        },
        "htfBias": "BULLISH",
        "primaryDol": "H1 BSL PDH @ 2040",
        "setupType": "LIQUIDITY_SWEEP_MSS",
        "setupState": "BLOCKED",
        "setupScore": 85.0,
        "setupGrade": "A",
        "decisionConfidence": "MODERATE",
        "noWickState": {"classification": "BULLISH_NO_WICK", "strength": "STRONG", "direction": "BULLISH"},
        "blockers": ["ANALYSIS_GATES_NOT_IMPLEMENTED"],
        "strategyVersion": "0.19.0-phase19",
    }
    base.update(over)
    return base


def evaluation(plan=True, direction="BULLISH", limit=1.0):
    return {
        "authority": "NOT_AUTHORIZED",
        "plan": {
            "direction": direction,
            "entry": 2000.0,
            "stop": 1990.0,
            "risk": 10.0,
            "tp1": 2030.0,
            "tp2": None,
            "tp3": None,
            "rr1": 3.0,
        }
        if plan
        else None,
        "risk": {"limits": {"riskPerTradePct": limit}},
    }


# --- config and request models ------------------------------------------------------------------------


def test_config_loads_and_validates(monkeypatch):
    assert CFG.chase_tolerance_r == 0.25 and CFG.extremes_timeframes[0].value == "M5"
    real = journal_models.load_spec

    def patched(name):
        data = json.loads(json.dumps(real(name)))
        data["breakEvenToleranceR"] = 1.5
        return data

    monkeypatch.setattr(journal_models, "load_spec", patched)
    with pytest.raises(ValueError):
        JournalConfig.from_spec()


def test_fill_sides_and_utc_are_enforced():
    with pytest.raises(ValidationError):
        fill(stop=2010.0)  # stop above a long entry
    with pytest.raises(ValidationError):
        fill(targets=(1995.0,))
    with pytest.raises(ValidationError):
        fill(opened_at=datetime(2024, 4, 18, 13, 0))  # noqa: DTZ001 - naive on purpose
    short = fill(Direction.BEARISH, entry=2000.0, stop=2010.0, targets=(1970.0,))
    assert short.stop == 2010.0 and fill(stop=None).stop is None


def test_trade_details_only_for_trade_entries():
    with pytest.raises(ValidationError):
        CreateJournalEntryRequest(symbol="XAUUSD", kind=JournalEntryKind.TRADE)
    with pytest.raises(ValidationError):
        CreateJournalEntryRequest(symbol="XAUUSD", kind=JournalEntryKind.NO_TRADE, trade=fill())
    with pytest.raises(ValidationError):
        CreateJournalEntryRequest(
            symbol="XAUUSD", kind=JournalEntryKind.NO_TRADE, notes="x" * (CFG.max_notes_length + 1)
        )
    assert CreateJournalEntryRequest(symbol="XAUUSD", kind=JournalEntryKind.MISSED_ENTRY).trade is None


# --- snapshot ------------------------------------------------------------------------


def test_canonical_hash_is_order_independent_and_sensitive():
    assert canonical_hash({"a": 1, "b": [1, 2]}) == canonical_hash({"b": [1, 2], "a": 1})
    assert canonical_hash({"a": 1}) != canonical_hash({"a": 1.5})


def test_snapshot_timing():
    assert (
        snapshot_timing(JournalEntryKind.TRADE, fill(opened_at=NOW - timedelta(minutes=3)), NOW, CFG)
        is SnapshotTiming.PRE_ENTRY
    )
    assert snapshot_timing(JournalEntryKind.TRADE, fill(), NOW, CFG) is SnapshotTiming.POST_ENTRY
    assert snapshot_timing(JournalEntryKind.NO_TRADE, None, NOW, CFG) is SnapshotTiming.NOT_APPLICABLE


def test_summary_reads_spec_fields_from_the_snapshot():
    s = summarize(decision(), evaluation(), {"timeframe": "M5", "isSynthetic": True}, NOW)
    assert s.day_of_week == "Thursday" and s.active_sessions == ["NEW_YORK"] and s.time_quality == "IDEAL"
    assert (
        s.news_state == "CLEAR"
        and s.macro_state == "SUPPORTIVE"
        and s.no_wick == "BULLISH_NO_WICK STRONG BULLISH"
    )
    assert s.plan_entry == 2000.0 and s.plan_targets == [2030.0] and s.plan_rr == 3.0
    assert s.engine_authorization == "NOT_AUTHORIZED" and s.is_synthetic and s.execution_timeframe == "M5"
    empty = summarize({"verdict": "UNAVAILABLE"}, None, None, NOW)
    assert (
        empty.plan_entry is None
        and empty.active_sessions == []
        and empty.engine_authorization == "NOT_AUTHORIZED"
    )


# --- detected violations ------------------------------------------------------------------------


def test_clean_trade_on_the_plan_has_no_violations():
    assert detect_violations(fill(risk_pct=1.0), decision(), evaluation(), CFG) == []


@pytest.mark.parametrize(
    ("kw", "dec", "ev", "expected"),
    [
        ({}, {"verdict": "UNAVAILABLE"}, None, V.TRADED_ON_UNAVAILABLE_DECISION),
        ({}, {"newsState": {"state": "BLACKOUT"}}, None, V.TRADED_DURING_NEWS_BLACKOUT),
        ({}, {"riskStatus": "LOCKED"}, None, V.TRADED_WHILE_RISK_LOCKED),
        ({}, {}, {"plan": False}, V.NO_CONFIRMED_PLAN),
        ({}, {}, {"direction": "BEARISH"}, V.AGAINST_PLAN_DIRECTION),
        ({"entry": 2003.0}, {}, None, V.CHASED_ENTRY),  # 0.3 R beyond the plan entry
        ({"stop": None}, {}, None, V.NO_STOP_PLACED),
        ({"risk_pct": 2.0}, {}, None, V.RISK_ABOVE_LIMIT),
    ],
)
def test_each_detected_violation(kw, dec, ev, expected):
    found = detect_violations(fill(**kw), decision(**dec), evaluation(**(ev or {})), CFG)
    assert expected in found


def test_chase_tolerance_and_short_side():
    assert V.CHASED_ENTRY not in detect_violations(fill(entry=2002.0), decision(), evaluation(), CFG)  # 0.2 R
    short_plan = evaluation(direction="BEARISH")
    short_plan["plan"].update(entry=2000.0, risk=10.0)
    chased = fill(Direction.BEARISH, entry=1996.0, stop=2010.0, targets=())
    assert V.CHASED_ENTRY in detect_violations(chased, decision(), short_plan, CFG)
    assert detect_violations(fill(), decision(), None, CFG) == [V.NO_CONFIRMED_PLAN]  # unknown plan = no plan


# --- outcomes ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("exit_price", "reason", "result", "r"),
    [
        (2030.0, ExitReason.TARGET, TradeResult.FULL_WIN, 3.0),
        (2028.0, ExitReason.TARGET, TradeResult.FULL_WIN, 2.8),  # within 10% of planned 3R
        (2015.0, ExitReason.TARGET, TradeResult.PARTIAL_WIN, 1.5),
        (2000.5, ExitReason.BREAK_EVEN_STOP, TradeResult.BREAK_EVEN, 0.05),
        (1990.0, ExitReason.STOP, TradeResult.FULL_LOSS, -1.0),
        (1995.0, ExitReason.STOP, TradeResult.PARTIAL_LOSS, -0.5),
        (2010.0, ExitReason.MANUAL, TradeResult.MANUAL_EXIT, 1.0),
        (1996.0, ExitReason.INVALIDATION, TradeResult.INVALIDATION_EXIT, -0.4),
        (2005.0, ExitReason.NEWS, TradeResult.NEWS_EXIT, 0.5),
        (2020.0, ExitReason.TRAILING_STOP, TradeResult.TRAILING_STOP_EXIT, 2.0),
    ],
)
def test_result_states_and_r(exit_price, reason, result, r):
    m = compute_outcome(fill(), outcome(exit_price, reason), NONE, [], CFG)
    assert m.result is result and m.r_multiple == pytest.approx(r)
    assert m.planned_r == 3.0 and m.duration_minutes == 90.0


def test_short_trade_r_and_extremes():
    f = fill(Direction.BEARISH, entry=2000.0, stop=2010.0, targets=(1980.0, 1970.0))
    ext = Extremes(1965.0, 2004.0, ExtremeSource.MANUAL, False, None)
    m = compute_outcome(f, outcome(1975.0), ext, [], CFG)
    assert m.r_multiple == 2.5 and m.planned_r == 3.0 and m.result is TradeResult.PARTIAL_WIN  # 2.5 < 3 x 0.9
    assert m.mfe_r == 3.5 and m.mae_r == -0.4
    assert m.entry_efficiency == pytest.approx(35 / 39, abs=1e-3) and m.exit_efficiency == pytest.approx(
        29 / 39, abs=1e-3
    )


def test_no_stop_uses_the_sign_of_the_pl_only():
    f = fill(stop=None)
    assert (
        compute_outcome(f, outcome(2010.0), NONE, [V.NO_STOP_PLACED], CFG).result is TradeResult.PARTIAL_WIN
    )
    loss = compute_outcome(f, outcome(1990.0, ExitReason.STOP), NONE, [V.NO_STOP_PLACED], CFG)
    assert loss.result is TradeResult.PARTIAL_LOSS and loss.r_multiple is None and loss.planned_r is None
    assert loss.classification is ProcessClassification.PROCESS_ERROR


@pytest.mark.parametrize(
    ("exit_price", "detected", "reported", "classification"),
    [
        (2030.0, [], [], ProcessClassification.VALID_WIN),
        (2030.0, [V.CHASED_ENTRY], [], ProcessClassification.BAD_PROCESS_WIN),
        (1990.0, [], [], ProcessClassification.VALID_LOSS),
        (1990.0, [], [V.MOVED_STOP], ProcessClassification.PROCESS_ERROR),
        (2000.5, [], [], ProcessClassification.VALID_LOSS),  # break-even is not a win
    ],
)
def test_process_classification(exit_price, detected, reported, classification):
    m = compute_outcome(fill(), outcome(exit_price, reported_violations=reported), NONE, detected, CFG)
    assert m.classification is classification
    assert m.violations == list(dict.fromkeys([*detected, *reported]))


def test_outcome_checks_contradictions_without_values():
    f = fill()
    with pytest.raises(OutcomeInputError, match="after"):
        check_outcome(
            f,
            RecordOutcomeRequest(exit_price=2010.0, exited_at=f.opened_at, exit_reason=ExitReason.MANUAL),
            NOW,
        )
    with pytest.raises(OutcomeInputError, match="future"):
        check_outcome(
            f,
            RecordOutcomeRequest(
                exit_price=2010.0, exited_at=NOW + timedelta(hours=1), exit_reason=ExitReason.MANUAL
            ),
            NOW,
        )
    with pytest.raises(OutcomeInputError, match="mfePrice") as exc:
        check_outcome(f, outcome(2010.0, mfe_price=2005.0), NOW)
    assert "2005" not in str(exc.value)
    with pytest.raises(OutcomeInputError, match="maePrice"):
        check_outcome(f, outcome(2010.0, mae_price=2001.0), NOW)
    check_outcome(f, outcome(2010.0, mfe_price=2012.0, mae_price=1995.0), NOW)


def test_candle_extremes_widen_to_the_fills():
    f = fill()
    ext = candle_extremes(f, 2031.0, [(2020.0, 1996.0), (2025.0, 2001.0)], True, "M5 candles")
    assert ext.mfe == 2031.0 and ext.mae == 1996.0 and ext.source is ExtremeSource.CANDLES and ext.synthetic
    short = candle_extremes(
        fill(Direction.BEARISH, stop=2010.0, targets=()), 1990.0, [(2004.0, 1985.0)], False, "M5"
    )
    assert short.mfe == 1985.0 and short.mae == 2004.0
    assert candle_extremes(f, 2010.0, [], False, "M5").source is ExtremeSource.UNAVAILABLE
