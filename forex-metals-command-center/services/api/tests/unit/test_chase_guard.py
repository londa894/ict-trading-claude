"""Do-not-chase must not kill a setup before it can ever enter.

target() locks the NEAREST untaken pool at the sweep; armed() then INVALIDATES the
setup the moment that pool is taken. Measured on real XAUUSD M15, the target is nearer
than the entry zone in 97% of armed setups in a trending window, so the setup is
required to complete a retracement before price covers a SHORTER distance it is already
moving toward. The target wins by construction and nothing is ever touched.

`chaseGuardAfterTouchOnly` defers the guard until the entry zone has been touched:
before the touch the setup has not had its chance, after the touch a taken target
genuinely means the move is done. Default OFF so the shipped strategy is unchanged.
"""

from __future__ import annotations

from app.services.setup_state.chase import chase_guard_applies
from app.services.setup_state.models import SetupConfig


def test_flag_is_off_by_default_so_behaviour_is_unchanged():
    assert SetupConfig.from_spec().chase_guard_after_touch_only is False


def test_legacy_mode_invalidates_even_before_the_zone_is_touched():
    """Current shipped behaviour: the guard fires regardless of the touch."""
    assert chase_guard_applies(target_taken=True, zone_touched=False, after_touch_only=False)
    assert chase_guard_applies(target_taken=True, zone_touched=True, after_touch_only=False)


def test_deferred_mode_does_not_invalidate_before_the_touch():
    """The 97% case: the setup keeps its chance to retrace."""
    assert not chase_guard_applies(target_taken=True, zone_touched=False, after_touch_only=True)


def test_deferred_mode_still_invalidates_after_the_touch():
    """Once touched, a taken target means the draw is complete: do not chase."""
    assert chase_guard_applies(target_taken=True, zone_touched=True, after_touch_only=True)


def test_an_untaken_target_never_triggers_the_guard():
    for after_touch_only in (False, True):
        for touched in (False, True):
            assert not chase_guard_applies(
                target_taken=False, zone_touched=touched, after_touch_only=after_touch_only
            )
