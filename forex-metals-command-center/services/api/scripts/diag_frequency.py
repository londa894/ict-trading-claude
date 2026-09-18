"""Which gate throttles trade FREQUENCY (flags on)?

The batch sweep found ~1 tradeable setup per ~21 months. This pools the milestone
funnel AND the terminal reason of every armed setup across several windows, with both
fix-flags pinned on, to name the dominant killer of frequency.

  discovered -> target -> liquidity swept -> ARMED -> zone formed -> zone touched -> entry plan
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
from app.services.setup_state.models import Setup, SetupConfig
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

MILESTONES = [
    ("discovered", lambda s: True),
    ("target chosen", lambda s: s.target is not None),
    ("liquidity swept", lambda s: s.liquidity_event is not None),
    ("ARMED (mss)", lambda s: s.mss is not None),
    ("zone formed", lambda s: bool(s.zone_ids)),
    ("zone touched", lambda s: s.touched_zone_id is not None),
    ("entry plan", lambda s: s.entry_plan is not None),
]


class Clock:
    def __init__(self, at: datetime) -> None:
        self.at = at

    def __call__(self) -> datetime:
        return self.at


def depth(s: Setup) -> int:
    reached = 0
    for i, (_, test) in enumerate(MILESTONES):
        if test(s):
            reached = i
    return reached


async def run_window(start_s: str, end_s: str, cfg: SetupConfig) -> tuple[Counter, Counter]:
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

    best: dict[str, int] = {}
    armed_deaths: Counter = Counter()
    for t in steps:
        clock.at = t
        analysis = await svc.analyze(SYMBOL, t)
        if not analysis.eligible_for_decision:
            continue
        for s in analysis.setups:
            best[s.id] = max(best.get(s.id, 0), depth(s))
            if s.mss is not None and s.terminal and s.reason and s.entry_plan is None:
                armed_deaths[s.reason[:48]] += 1

    milestones: Counter = Counter()
    for d in best.values():
        for i in range(d + 1):
            milestones[i] += 1
    return milestones, armed_deaths


async def main() -> None:
    original = SPEC.read_text(encoding="utf-8")
    base = json.loads(original)
    pinned = {"chaseGuardAfterTouchOnly": True, "minTargetAtr": 1.0}
    total_ms: Counter = Counter()
    total_deaths: Counter = Counter()
    seen_windows = 0
    try:
        SPEC.write_text(json.dumps({**base, **pinned}), encoding="utf-8")
        load_spec.cache_clear()
        cfg = SetupConfig.from_spec()
        print(f"pinned: {pinned}   windows: {len(WINDOWS)}", flush=True)
        for start_s, end_s in WINDOWS:
            ms, deaths = await run_window(start_s, end_s, cfg)
            total_ms += ms
            total_deaths += deaths
            seen_windows += 1
            print(f"  done {start_s} (armed={ms.get(3, 0)}, plans={ms.get(6, 0)})", flush=True)
    finally:
        SPEC.write_text(original, encoding="utf-8")
        load_spec.cache_clear()

    print(f"\n===== POOLED MILESTONE FUNNEL ({seen_windows} windows) =====")
    disc = total_ms.get(0, 0) or 1
    for i, (name, _) in enumerate(MILESTONES):
        n = total_ms.get(i, 0)
        print(f"  {name:<18} {n:>5}   {n / disc:5.1%}")

    print("\n===== WHY ARMED SETUPS DIE (no entry plan) =====")
    tot = sum(total_deaths.values()) or 1
    for reason, n in total_deaths.most_common(12):
        print(f"  {n:>5}  {n / tot:5.1%}  {reason}")


if __name__ == "__main__":
    asyncio.run(main())
