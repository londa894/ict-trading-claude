"""EXPIRED_NO_MSS must distinguish 'no break happened' from 'break rejected for weak displacement'.

Both currently produce the same reason string, so the funnel cannot say which gate
(mss.windowBars vs mss.requireDisplacement) is actually binding.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from app.domain.enums import Direction, SetupState, SetupType
from app.services.setup_state.funnel import TerminalReason, classify_terminal
from app.services.setup_state.models import Setup

T = datetime(2024, 3, 7, 14, 0, tzinfo=UTC)


def expired(reason: str) -> Setup:
    return Setup(
        id="s1",
        setup_type=SetupType.LIQUIDITY_SWEEP_MSS,
        direction=Direction.BULLISH,
        state=SetupState.EXPIRED,
        terminal=True,
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


def test_no_structure_break_at_all_is_a_window_problem():
    """Nothing broke structure in time -> mss.windowBars is the binding gate."""
    setup = expired("no confirmation break within 12 bars")
    assert classify_terminal(setup) is TerminalReason.EXPIRED_NO_BREAK


def test_break_rejected_for_displacement_is_a_quality_problem():
    """Structure broke but failed the displacement qualifier -> requireDisplacement is binding."""
    setup = expired("no confirmation break within 12 bars (3 break(s) lacked displacement)")
    assert classify_terminal(setup) is TerminalReason.EXPIRED_MSS_NO_DISPLACEMENT


def test_the_two_causes_are_distinguishable():
    """The whole point: these must not collapse into one bucket."""
    no_break = classify_terminal(expired("no confirmation break within 12 bars"))
    weak = classify_terminal(expired("no confirmation break within 12 bars (2 break(s) lacked displacement)"))
    assert no_break is not weak
