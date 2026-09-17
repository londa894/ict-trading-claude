"""Terminal-reason classification for setup-funnel diagnosis.

Buckets the engine's own `Setup.reason` wording into stable codes so a run that reports
"200 EXPIRED" becomes a named, actionable cause.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from app.domain.enums import Direction, SetupState, SetupType
from app.services.setup_state.funnel import TerminalReason, classify_terminal, summarize
from app.services.setup_state.models import Setup

T = datetime(2024, 3, 7, 14, 0, tzinfo=UTC)


def setup(state: SetupState, reason: str | None, *, sid: str = "s1", terminal: bool = True) -> Setup:
    return Setup(
        id=sid,
        setup_type=SetupType.LIQUIDITY_SWEEP_MSS,
        direction=Direction.BULLISH,
        state=state,
        terminal=terminal,
        trading_day=date(2024, 3, 7),
        discovered_at=T,
        state_changed_at=T,
        target=None,
        liquidity_event=None,
        mss=None,
        protective_level=None,
        zone_ids=[],
        touched_zone_id=None,
        reason=reason,
        next_required_event=None,
        steps=[],
        entry_plan=None,
    )


@pytest.mark.parametrize(
    "state,reason,expected",
    [
        # EXPIRED family — each maps to a distinct stage of the funnel
        (SetupState.EXPIRED, "trading day ended", TerminalReason.EXPIRED_TRADING_DAY),
        (
            SetupState.EXPIRED,
            "no confirmation break within 12 bars",
            TerminalReason.EXPIRED_NO_BREAK,
        ),
        (
            SetupState.EXPIRED,
            "no FVG formed in the displacement leg",
            TerminalReason.EXPIRED_NO_FVG,
        ),
        (
            SetupState.EXPIRED,
            "no retracement within 20 bars",
            TerminalReason.EXPIRED_NO_RETRACEMENT,
        ),
        (
            SetupState.EXPIRED,
            "no entry confirmation within 8 bars of the touch",
            TerminalReason.EXPIRED_NO_CONFIRMATION,
        ),
        # INVALIDATED family
        (
            SetupState.INVALIDATED,
            "HTF bias changed to BEARISH",
            TerminalReason.INVALIDATED_HTF_FLIP,
        ),
        (
            SetupState.INVALIDATED,
            "closed beyond the swept extreme (liquidity ran)",
            TerminalReason.INVALIDATED_LIQUIDITY_RAN,
        ),
        (
            SetupState.INVALIDATED,
            "closed beyond the protective extreme",
            TerminalReason.INVALIDATED_PROTECTIVE,
        ),
        (
            SetupState.INVALIDATED,
            "target liquidity reached before a confirmed entry (do not chase)",
            TerminalReason.INVALIDATED_DO_NOT_CHASE,
        ),
        (
            SetupState.INVALIDATED,
            "every retracement zone was invalidated",
            TerminalReason.INVALIDATED_ZONES_GONE,
        ),
        (SetupState.INVALIDATED, "plan stop traded", TerminalReason.INVALIDATED_STOP_TRADED),
    ],
)
def test_each_engine_reason_maps_to_its_own_code(state, reason, expected):
    assert classify_terminal(setup(state, reason)) is expected


def test_unrecognised_reason_is_bucketed_not_dropped():
    assert classify_terminal(setup(SetupState.EXPIRED, "something new")) is TerminalReason.EXPIRED_OTHER
    assert (
        classify_terminal(setup(SetupState.INVALIDATED, None))
        is TerminalReason.INVALIDATED_OTHER
    )


def test_live_setups_are_reported_by_state_not_as_terminal():
    live = setup(SetupState.SETUP_ARMED, None, terminal=False)
    assert classify_terminal(live) is TerminalReason.LIVE


def test_summarize_counts_unique_setups_and_orders_by_frequency():
    setups = [
        setup(SetupState.EXPIRED, "trading day ended", sid="a"),
        setup(SetupState.EXPIRED, "trading day ended", sid="b"),
        setup(SetupState.EXPIRED, "trading day ended", sid="a"),  # duplicate id, counted once
        setup(SetupState.INVALIDATED, "HTF bias changed to BEARISH", sid="c"),
    ]

    result = summarize(setups)

    assert result.total == 3
    assert result.counts[TerminalReason.EXPIRED_TRADING_DAY] == 2
    assert result.counts[TerminalReason.INVALIDATED_HTF_FLIP] == 1
    assert next(iter(result.counts)) is TerminalReason.EXPIRED_TRADING_DAY  # most frequent first


def test_summarize_reports_the_dominant_blocker():
    setups = [setup(SetupState.EXPIRED, "trading day ended", sid=str(i)) for i in range(7)]
    setups += [setup(SetupState.INVALIDATED, "plan stop traded", sid="z")]

    result = summarize(setups)

    assert result.dominant is TerminalReason.EXPIRED_TRADING_DAY
    assert result.dominant_share == pytest.approx(7 / 8)


def test_summarize_of_nothing_is_empty_not_an_error():
    result = summarize([])
    assert result.total == 0 and result.dominant is None and result.dominant_share == 0.0
