"""Chase protection for armed setups.

ICT's 'do not chase' means: do not enter once the draw on liquidity has already been
delivered. The engine implements it as "target pool taken -> INVALIDATED", applied from
the moment the setup arms.

Measured on real XAUUSD M15, that fires before the setup can ever enter: target() locks
the NEAREST untaken pool, which sits closer than the entry zone in 97% of armed setups
in a trending window. The setup must therefore complete a retracement before price
travels a shorter distance in the direction it is already going. Nothing is ever touched.

Deferring the guard until after the entry zone is touched preserves the intent (no entry
after the move is done) without pre-emptively killing every setup.
"""

from __future__ import annotations


def chase_guard_applies(*, target_taken: bool, zone_touched: bool, after_touch_only: bool) -> bool:
    """True when the setup must be invalidated for chasing.

    With `after_touch_only` the guard waits until the entry zone has been touched, so a
    setup is not killed before it has had any opportunity to enter.
    """
    if not target_taken:
        return False
    if after_touch_only:
        return zone_touched
    return True
