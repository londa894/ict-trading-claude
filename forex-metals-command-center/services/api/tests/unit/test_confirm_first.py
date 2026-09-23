"""confirmFirst: an entry available the same candle the target is taken should win.

Frequency diagnosis (2026-09-18): 70% of armed setups die "target reached before a
confirmed entry", and 100% of those had TOUCHED the entry zone first. The setup reaches
its FVG, then during the confirmation wait price runs to target on some candle; the
chase guard (which runs before confirm()) kills it even when an entry model would have
confirmed on that very candle.

`confirmBeforeChaseGuard` lets an ALREADY-TOUCHED setup attempt confirmation before the
chase guard fires. A setup that touched the zone on a PRIOR candle has genuinely had its
retracement; if a model confirms this candle, the entry existed and should not lose to
the same-candle target take. Default OFF so the shipped strategy is unchanged. The
touch-this-candle path stays behind the guard (intrabar-ambiguous, fail safe).
"""

from __future__ import annotations

import dataclasses

from app.domain.enums import EntryModel, SetupState
from app.services.setup_state.models import SetupConfig
from tests.setup_helpers import happy

S = SetupState
# Touch on candle 28 (default zone touch), confirm + target-take both on candle 29.
BULL_CONFIRM = (2031.8, 2032.3, 2031.3, 2032.0)  # closes above the FVG top 2031.5


def _cfg(flag: bool) -> SetupConfig:
    return dataclasses.replace(SetupConfig.from_spec(), confirm_before_chase_guard=flag)


def test_flag_is_enabled_in_the_shipped_spec():
    # Enabled in setup.json as of the Playbook Revamp: confirmation is checked before the chase guard.
    assert SetupConfig.from_spec().confirm_before_chase_guard is True


def test_legacy_target_take_on_confirm_candle_kills_the_setup():
    """Default behaviour: chase guard pre-empts the confirmation, setup is INVALIDATED."""
    _, result = happy(
        rows={29: BULL_CONFIRM},
        target_taken_at=29,
        cfg=_cfg(False),
    ).run()
    setup = result.setups[0]
    assert setup.terminal and setup.entry_plan is None
    assert "do not chase" in (setup.reason or "")


def test_confirm_first_recovers_the_entry_when_touched_on_a_prior_candle():
    """Flag on: the already-touched setup confirms on candle 29 despite the target take."""
    _, result = happy(
        rows={29: BULL_CONFIRM},
        target_taken_at=29,
        cfg=_cfg(True),
    ).run()
    setup = result.setups[0]
    assert setup.entry_plan is not None, f"expected a plan, got reason={setup.reason!r}"
    assert setup.entry_plan.model in (EntryModel.M15_CLOSE, EntryModel.IFVG)
    assert setup.state is S.BLOCKED and not setup.terminal


def test_confirm_first_still_chases_when_no_model_confirms():
    """Flag on but no confirmation this candle: the guard still fires, no free pass."""
    _, result = happy(
        rows={29: (2031.4, 2031.45, 2031.0, 2031.2)},  # stays below the FVG top: no M15_CLOSE
        target_taken_at=29,
        cfg=_cfg(True),
    ).run()
    setup = result.setups[0]
    assert setup.entry_plan is None
    assert setup.terminal and "do not chase" in (setup.reason or "")
