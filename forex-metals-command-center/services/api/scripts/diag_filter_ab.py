"""Before/after comparison of the significant-sweep filter on real history.

Runs the SAME windows with the filter off and on, and reports the funnel both ways.
Toggles the spec in-memory so nothing on disk changes.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from app.config import get_settings
from app.contracts import load_spec
from app.providers.registry import default_registry
from app.services.candles.service import CandleService
from app.services.setup_state.funnel import summarize
from app.services.setup_state.models import SetupConfig
from app.services.setup_state.service import SetupService

SYMBOL = "XAUUSD"
WINDOWS = [
    ("2024-03-04", "2024-03-18", "trend"),
    ("2023-06-05", "2023-06-19", "range"),
]
SPEC = Path(__file__).resolve().parents[3] / "packages" / "strategy-spec" / "setup.json"
STRIDE = 4


class Clock:
    def __init__(self, at: datetime) -> None:
        self.at = at

    def __call__(self) -> datetime:
        return self.at


async def funnel(start: datetime, end: datetime, cfg: SetupConfig) -> tuple[int, int, int, str]:
    settings = get_settings()
    clock = Clock(end)
    provider = default_registry(data_root=settings.data_root).create(settings.market_data_provider)
    candles = CandleService(provider, clock)
    svc = SetupService(candles, cfg=cfg)

    tf = cfg.setup_timeframe
    span = int((end - start) / tf.duration) + 2
    series = await candles.load_series(SYMBOL, tf, min(span, 1000), end)
    steps = [c.close_time for c in series.candles if c.is_closed and start < c.close_time <= end]
    steps = steps[::STRIDE]

    seen: dict[str, object] = {}
    confirmed = 0
    for t in steps:
        clock.at = t
        analysis = await svc.analyze(SYMBOL, t)
        if not analysis.eligible_for_decision:
            continue
        for s in analysis.setups:
            seen[s.id] = s
            if s.entry_plan is not None:
                confirmed += 1
    diag = summarize(list(seen.values()))  # type: ignore[arg-type]
    return len(steps), len(seen), confirmed, diag.report()


async def main() -> None:
    original = SPEC.read_text(encoding="utf-8")
    base = json.loads(original)
    try:
        await _compare(base)
    finally:
        SPEC.write_text(original, encoding="utf-8")  # byte-for-byte, even on failure
        load_spec.cache_clear()
        print("\nspec restored")


async def _compare(base: dict) -> None:
    for start_s, end_s, label in WINDOWS:
        start = datetime.fromisoformat(start_s).replace(tzinfo=UTC)
        end = datetime.fromisoformat(end_s).replace(tzinfo=UTC)
        print(f"\n{'=' * 62}")
        print(f"{label}: {start_s} -> {end_s}")
        print("=" * 62)

        for flag in (False, True):
            SPEC.write_text(json.dumps({**base, "significantSweepsOnly": flag}), encoding="utf-8")
            load_spec.cache_clear()
            cfg = SetupConfig.from_spec()
            assert cfg.significant_sweeps_only is flag
            steps, setups, confirmed, report = await funnel(start, end, cfg)
            state = "ON " if flag else "OFF"
            print(f"\n--- filter {state} ---")
            print(f"steps {steps}   setups {setups}   confirmed plans {confirmed}")
            print(report)


if __name__ == "__main__":
    asyncio.run(main())
