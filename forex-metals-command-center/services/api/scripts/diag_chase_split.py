"""Of the do-not-chase deaths, how many TOUCHED the zone first?

70% of armed setups die "target liquidity reached before a confirmed entry". This
splits that bucket by whether the entry zone was ever touched:

  - died WITHOUT touching  -> price ran to target without retracing: legitimately no trade
  - died AFTER touching     -> price reached the zone, then target was taken during the
                               confirmation wait: THIS is the recoverable frequency

If most are the former, the strategy structurally rarely gets a retracement entry and
frequency needs a different setup model, not a looser gate. If the latter dominates,
shortening the confirmation window or entering on touch would recover trades.
"""

from __future__ import annotations

import asyncio
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from app.config import get_settings
from app.contracts import load_spec
from app.providers.registry import default_registry
from app.services.candles.service import CandleService
from app.services.setup_state.models import SetupConfig
from app.services.setup_state.service import SetupService

SYMBOL = "XAUUSD"
WINDOWS = [
    ("2024-03-04", "2024-03-18"),
    ("2023-06-05", "2023-06-19"),
    ("2024-07-01", "2024-07-15"),
    ("2024-12-02", "2024-12-16"),
    ("2022-03-07", "2022-03-21"),
    ("2025-06-02", "2025-06-16"),
]
SPEC = Path(__file__).resolve().parents[3] / "packages" / "strategy-spec" / "setup.json"
STRIDE = 4
CHASE = "target liquidity reached"


class Clock:
    def __init__(self, at: datetime) -> None:
        self.at = at

    def __call__(self) -> datetime:
        return self.at


async def run_window(start_s: str, end_s: str, cfg: SetupConfig) -> Counter:
    start = datetime.fromisoformat(start_s).replace(tzinfo=UTC)
    end = datetime.fromisoformat(end_s).replace(tzinfo=UTC)
    settings = get_settings()
    clock = Clock(end)
    provider = default_registry(data_root=settings.data_root).create(settings.market_data_provider)
    candles = CandleService(provider, clock)
    svc = SetupService(candles)

    tf = cfg.setup_timeframe
    span = int((end - start) / tf.duration) + 2
    series = await candles.load_series(SYMBOL, tf, min(span, 1000), end)
    steps = [c.close_time for c in series.candles if c.is_closed and start < c.close_time <= end][::STRIDE]

    # Track the furthest 'touched' flag ever seen per setup, then classify at terminal.
    ever_touched: dict[str, bool] = {}
    classified: dict[str, str] = {}
    counts: Counter = Counter()
    for t in steps:
        clock.at = t
        analysis = await svc.analyze(SYMBOL, t)
        if not analysis.eligible_for_decision:
            continue
        for s in analysis.setups:
            if s.touched_zone_id is not None:
                ever_touched[s.id] = True
            if s.id in classified:
                continue
            if s.mss is not None and s.terminal and s.reason and CHASE in s.reason:
                touched = ever_touched.get(s.id, False)
                classified[s.id] = "chase_after_touch" if touched else "chase_no_touch"
                counts[classified[s.id]] += 1
    return counts


async def main() -> None:
    original = SPEC.read_text(encoding="utf-8")
    base = json.loads(original)
    pinned = {"chaseGuardAfterTouchOnly": True, "minTargetAtr": 1.0}
    total: Counter = Counter()
    try:
        SPEC.write_text(json.dumps({**base, **pinned}), encoding="utf-8")
        load_spec.cache_clear()
        cfg = SetupConfig.from_spec()
        print(f"pinned: {pinned}", flush=True)
        for start_s, end_s in WINDOWS:
            c = await run_window(start_s, end_s, cfg)
            total += c
            print(f"  done {start_s}: after_touch={c.get('chase_after_touch', 0)} "
                  f"no_touch={c.get('chase_no_touch', 0)}", flush=True)
    finally:
        SPEC.write_text(original, encoding="utf-8")
        load_spec.cache_clear()

    a = total.get("chase_after_touch", 0)
    n = total.get("chase_no_touch", 0)
    tot = a + n or 1
    print("\n===== DO-NOT-CHASE DEATHS, SPLIT BY TOUCH =====")
    print(f"  chase AFTER touching zone : {a:>5}  {a / tot:5.1%}  <- potentially recoverable")
    print(f"  chase WITHOUT touching    : {n:>5}  {n / tot:5.1%}  <- structurally no trade")


if __name__ == "__main__":
    asyncio.run(main())
