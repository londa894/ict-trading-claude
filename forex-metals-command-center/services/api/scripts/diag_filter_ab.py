"""Before/after comparison of a setup-spec flag on real history.

Runs the SAME windows with a boolean spec flag off and on, reporting the milestone
funnel both ways. Toggles the spec on disk and restores it byte-for-byte in a finally.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
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
    ("2024-03-04", "2024-03-18", "trend"),
    ("2023-06-05", "2023-06-19", "range"),
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


async def run(start: datetime, end: datetime, cfg: SetupConfig) -> tuple[int, Counter[int], Counter[str]]:
    settings = get_settings()
    clock = Clock(end)
    provider = default_registry(data_root=settings.data_root).create(settings.market_data_provider)
    candles = CandleService(provider, clock)
    svc = SetupService(candles, cfg=cfg)

    tf = cfg.setup_timeframe
    span = int((end - start) / tf.duration) + 2
    series = await candles.load_series(SYMBOL, tf, min(span, 1000), end)
    steps = [c.close_time for c in series.candles if c.is_closed and start < c.close_time <= end][::STRIDE]

    best: dict[str, int] = {}
    reasons: Counter[str] = Counter()
    for t in steps:
        clock.at = t
        analysis = await svc.analyze(SYMBOL, t)
        if not analysis.eligible_for_decision:
            continue
        for s in analysis.setups:
            best[s.id] = max(best.get(s.id, 0), depth(s))
            if s.mss is not None and s.terminal and s.reason:
                reasons[s.reason[:52]] += 1
    return len(best), Counter(best.values()), reasons


def show(total: int, counts: Counter[int], reasons: Counter[str]) -> None:
    running = total
    for i, (name, _) in enumerate(MILESTONES):
        n = counts.get(i, 0)
        share = f"{running / total:.0%}" if total else "-"
        print(f"    {name:<18}{running:>7}{share:>7}")
        running -= n
    if reasons:
        print("    armed deaths:")
        for reason, n in reasons.most_common(4):
            print(f"      {n:>4}  {reason}")


async def main(flag_key: str, raw_values: str | None = None) -> None:
    """Compare a spec key's values. Extra keys can be pinned via BASE_FLAGS=k=v,k=v."""
    values: list[object] = [False, True]
    if raw_values:
        values = [float(v) if "." in v or v.isdigit() else v for v in raw_values.split(",")]
    pinned: dict[str, object] = {}
    for pair in filter(None, os.environ.get("BASE_FLAGS", "").split(",")):
        k, _, v = pair.partition("=")
        pinned[k] = True if v == "true" else False if v == "false" else float(v)

    original = SPEC.read_text(encoding="utf-8")
    base = json.loads(original)
    try:
        for start_s, end_s, label in WINDOWS:
            start = datetime.fromisoformat(start_s).replace(tzinfo=UTC)
            end = datetime.fromisoformat(end_s).replace(tzinfo=UTC)
            print(f"\n{'=' * 58}\n{label}: {start_s} -> {end_s}   [{flag_key}]")
            if pinned:
                print(f"pinned: {pinned}")
            print("=" * 58)
            for value in values:
                SPEC.write_text(json.dumps({**base, **pinned, flag_key: value}), encoding="utf-8")
                load_spec.cache_clear()
                cfg = SetupConfig.from_spec()
                total, counts, reasons = await run(start, end, cfg)
                print(f"\n  --- {flag_key} = {value} ---   setups {total}")
                show(total, counts, reasons)
    finally:
        SPEC.write_text(original, encoding="utf-8")
        load_spec.cache_clear()
        print("\nspec restored")


if __name__ == "__main__":
    key = sys.argv[1] if len(sys.argv) > 1 else "chaseGuardAfterTouchOnly"
    asyncio.run(main(key, sys.argv[2] if len(sys.argv) > 2 else None))
