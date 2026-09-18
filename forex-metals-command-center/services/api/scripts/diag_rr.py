"""What R:R does the engine actually compute at confirmation?

Earlier diag_risk measured close->protective distance, a PROXY: the true entry is the
FVG retracement, nearer the protective than the sampled close, so that proxy overstates
risk. This instead harvests the engine's OWN rejection reason, "R:R X.XX below N.N",
which is computed from the real entry and real protective in build_plan. That number is
authoritative.

Run with chaseGuardAfterTouchOnly on (so setups reach confirmation) and minTargetAtr set
(so reward is not the binding gate), to isolate whether RISK is what fails R:R.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import statistics
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
    ("2024-03-04", "2024-03-18", "trend"),
    ("2023-06-05", "2023-06-19", "range"),
    ("2022-03-07", "2022-03-21", "volatility"),
    ("2024-12-02", "2024-12-16", "consolidation"),
    ("2025-06-02", "2025-06-16", "recent"),
    ("2024-07-01", "2024-07-15", "summer"),
    ("2023-11-06", "2023-11-20", "autumn"),
]
SPEC = Path(__file__).resolve().parents[3] / "packages" / "strategy-spec" / "setup.json"
STRIDE = 4
RR = re.compile(r"R:R (\d+\.\d+) below")


class Clock:
    def __init__(self, at: datetime) -> None:
        self.at = at

    def __call__(self) -> datetime:
        return self.at


def pct(vals: list[float], p: float) -> float:
    o = sorted(vals)
    return o[min(len(o) - 1, int(len(o) * p))]


async def measure(start_s: str, end_s: str, label: str, cfg: SetupConfig) -> list[float]:
    start = datetime.fromisoformat(start_s).replace(tzinfo=UTC)
    end = datetime.fromisoformat(end_s).replace(tzinfo=UTC)

    settings = get_settings()
    clock = Clock(end)
    provider = default_registry(data_root=settings.data_root).create(settings.market_data_provider)
    candles = CandleService(provider, clock)
    svc = SetupService(candles, cfg=cfg)

    tf = cfg.setup_timeframe
    span = int((end - start) / tf.duration) + 2
    series = await candles.load_series(SYMBOL, tf, min(span, 1000), end)
    steps = [c.close_time for c in series.candles if c.is_closed and start < c.close_time <= end][::STRIDE]

    rrs: list[float] = []
    plan_ids: set[str] = set()
    deaths: dict[str, int] = {}
    seen: set[str] = set()
    for t in steps:
        clock.at = t
        analysis = await svc.analyze(SYMBOL, t)
        if not analysis.eligible_for_decision:
            continue
        for s in analysis.setups:
            if s.entry_plan is not None:
                plan_ids.add(s.id)  # dedup: the same plan persists across sampling steps
            if s.id in seen or not s.reason:
                continue
            m = RR.search(s.reason)
            if m:
                seen.add(s.id)
                rrs.append(float(m.group(1)))
            elif s.mss is not None and s.terminal:
                seen.add(s.id)
                key = s.reason[:44]
                deaths[key] = deaths.get(key, 0) + 1

    print(f"\n===== {label}: {start_s} -> {end_s} =====")
    print(f"distinct confirmed plans: {len(plan_ids)}   R:R rejections captured: {len(rrs)}")
    if rrs:
        print(
            f"  actual R:R at confirmation:  min {min(rrs):.2f}   median {statistics.median(rrs):.2f}"
            f"   p75 {pct(rrs, 0.75):.2f}   max {max(rrs):.2f}"
        )
        need = load_spec("entry")
        target_rr = need["modes"][need["mode"]]["minRr"]
        passing = sum(1 for r in rrs if r >= target_rr)
        print(f"  would pass minRr {target_rr}: {passing}/{len(rrs)}")
    if deaths:
        print("  armed deaths (non-R:R):")
        for reason, n in sorted(deaths.items(), key=lambda kv: -kv[1])[:4]:
            print(f"    {n:>4}  {reason}")
    return rrs


async def main() -> None:
    original = SPEC.read_text(encoding="utf-8")
    base = json.loads(original)
    pinned = {
        "chaseGuardAfterTouchOnly": True,
        "minTargetAtr": float(os.environ.get("MIN_TARGET_ATR", "1.0")),
    }
    try:
        SPEC.write_text(json.dumps({**base, **pinned}), encoding="utf-8")
        load_spec.cache_clear()
        cfg = SetupConfig.from_spec()
        print(f"pinned: {pinned}")
        allrr: list[float] = []
        for start_s, end_s, label in WINDOWS:
            allrr += await measure(start_s, end_s, label, cfg)
        print(f"\n===== POOLED across {len(WINDOWS)} windows =====")
        if allrr:
            need = load_spec("entry")
            target_rr = need["modes"][need["mode"]]["minRr"]
            passing = sum(1 for r in allrr if r >= target_rr)
            print(
                f"  R:R rejections: {len(allrr)}   median {statistics.median(allrr):.2f}"
                f"   p75 {pct(allrr, 0.75):.2f}   max {max(allrr):.2f}   would pass {target_rr}: {passing}"
            )
        else:
            print("  no R:R rejections captured across any window")
    finally:
        SPEC.write_text(original, encoding="utf-8")
        load_spec.cache_clear()
        print("\nspec restored")


if __name__ == "__main__":
    asyncio.run(main())
