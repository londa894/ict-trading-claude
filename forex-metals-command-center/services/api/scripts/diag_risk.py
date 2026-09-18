"""Why is R:R failing? Measure risk and reward at confirmation.

With minTargetAtr pushing the target out, armed setups stop failing for "TP1 too close"
and start failing for "R:R below 2.0". That means reward is adequate and RISK is the
problem: stop = protective extreme -/+ stopBufferAtr x ATR, where the protective extreme
is the most adverse point from the liquidity-event candle through the break candle.

Reports the distribution of that risk in ATR so the stop can be judged against evidence.
"""

from __future__ import annotations

import asyncio
import itertools
import statistics
from datetime import UTC, datetime

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
]
STRIDE = 4


class Clock:
    def __init__(self, at: datetime) -> None:
        self.at = at

    def __call__(self) -> datetime:
        return self.at


def pct(vals: list[float], p: float) -> float:
    o = sorted(vals)
    return o[min(len(o) - 1, int(len(o) * p))]


async def measure(start_s: str, end_s: str, label: str) -> None:
    start = datetime.fromisoformat(start_s).replace(tzinfo=UTC)
    end = datetime.fromisoformat(end_s).replace(tzinfo=UTC)

    settings = get_settings()
    clock = Clock(end)
    provider = default_registry(data_root=settings.data_root).create(settings.market_data_provider)
    candles = CandleService(provider, clock)
    cfg = SetupConfig.from_spec()
    svc = SetupService(candles, cfg=cfg)

    tf = cfg.setup_timeframe
    span = int((end - start) / tf.duration) + 2
    series = await candles.load_series(SYMBOL, tf, min(span, 1000), end)
    bars = [c for c in series.candles if c.is_closed]
    close_by_time = {c.close_time: c.close for c in bars}
    steps = [c.close_time for c in bars if start < c.close_time <= end][::STRIDE]

    # ATR proxy: mean true range over the window, same series the engine uses.
    trs: list[float] = []
    for a, b in itertools.pairwise(bars):
        trs.append(max(b.high - b.low, abs(b.high - a.close), abs(b.low - a.close)))
    if len(trs) >= 14:
        atr = statistics.mean(trs[-14:])
    else:
        atr = statistics.mean(trs) if trs else 1.0

    risks: list[float] = []
    seen: set[str] = set()
    for t in steps:
        clock.at = t
        analysis = await svc.analyze(SYMBOL, t)
        if not analysis.eligible_for_decision:
            continue
        for s in analysis.setups:
            if s.mss is None or s.protective_level is None or s.id in seen:
                continue
            price = close_by_time.get(t)
            if price is None:
                continue
            seen.add(s.id)
            risks.append(abs(price - s.protective_level))

    ecfg = load_spec("entry")
    print(f"\n===== {label}: {start_s} -> {end_s} =====")
    print(
        f"ATR(14) ~ {atr:.2f}   stopBufferAtr {ecfg['stopBufferAtr']}"
        f"   minRr {ecfg['modes'][ecfg['mode']]['minRr']}"
    )
    print(f"armed setups: {len(risks)}")
    if not risks:
        return
    in_atr = [r / atr for r in risks]
    print("\nrisk (price -> protective extreme) in ATR:")
    print(
        f"  min {min(in_atr):.2f}   p25 {pct(in_atr, 0.25):.2f}   median {statistics.median(in_atr):.2f}"
        f"   p75 {pct(in_atr, 0.75):.2f}   max {max(in_atr):.2f}"
    )
    print("\nreward needed to clear minRr 2.0 at each risk level:")
    for q, name in ((0.25, "p25"), (0.5, "median"), (0.75, "p75")):
        r = pct(in_atr, q) if q != 0.5 else statistics.median(in_atr)
        print(f"  {name:<7} risk {r:.2f} ATR -> needs {2.0 * r:.2f} ATR of reward (${2.0 * r * atr:.2f})")


async def main() -> None:
    for start_s, end_s, label in WINDOWS:
        await measure(start_s, end_s, label)


if __name__ == "__main__":
    asyncio.run(main())
