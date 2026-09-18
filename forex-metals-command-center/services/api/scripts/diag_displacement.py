"""What does the displacement qualifier actually demand, and what does M15 deliver?

The qualifier needs a same-direction displacement of grade >= qualifierMinGrade
(MODERATE = 1.5 ATR) within qualifierLookbackBars (3) of the structure break. This
reports the real distribution of displacement grades and magnitudes so the threshold
can be judged against evidence.
"""

from __future__ import annotations

import asyncio
import statistics
from collections import Counter
from datetime import UTC, datetime

from app.config import get_settings
from app.contracts import load_spec
from app.domain.enums import Timeframe
from app.providers.registry import default_registry
from app.services.candles.service import CandleService
from app.services.pd_arrays.service import PdArrayService
from app.services.structure.service import StructureService

SYMBOL = "XAUUSD"
WINDOWS = [
    ("2024-03-04", "2024-03-18", "trend"),
    ("2023-06-05", "2023-06-19", "range"),
]


class Clock:
    def __init__(self, at: datetime) -> None:
        self.at = at

    def __call__(self) -> datetime:
        return self.at


async def measure(start_s: str, end_s: str, label: str) -> None:
    start = datetime.fromisoformat(start_s).replace(tzinfo=UTC)
    end = datetime.fromisoformat(end_s).replace(tzinfo=UTC)

    settings = get_settings()
    provider = default_registry(data_root=settings.data_root).create(settings.market_data_provider)
    candles = CandleService(provider, Clock(end))

    pd_svc = PdArrayService(candles)
    analysis = await pd_svc.analyze(SYMBOL, Timeframe.M15, 1000)

    in_window = [d for d in analysis.displacements if start <= d.time < end]
    grades: Counter[str] = Counter(str(d.grade.value) for d in in_window)
    atrs = [d.magnitude_atr for d in in_window if d.magnitude_atr is not None]

    print(f"\n===== {label}: {start_s} -> {end_s} =====")
    print(f"displacements in window: {len(in_window)}")
    print("\ngrade distribution:")
    for g in ("WEAK", "MODERATE", "STRONG", "EXCEPTIONAL"):
        n = grades.get(g, 0)
        share = f"{n / len(in_window):.0%}" if in_window else "-"
        print(f"  {g:<13} {n:>4}  {share}")

    if atrs:
        o = sorted(atrs)
        print("\nmagnitude in ATR:")
        print(
            f"  min {o[0]:.2f}   p25 {o[len(o) // 4]:.2f}   median {statistics.median(o):.2f}"
            f"   p75 {o[3 * len(o) // 4]:.2f}   max {o[-1]:.2f}"
        )
        for t in (0.75, 1.0, 1.25, 1.5, 2.0):
            kept = sum(1 for a in atrs if a >= t)
            print(f"  >= {t:.2f} ATR : {kept:>4}/{len(atrs)} ({kept / len(atrs):.0%})")

    # How many structure breaks carry a PRESENT qualifier?
    st_svc = StructureService(candles)
    st = await st_svc.analyze(SYMBOL, Timeframe.M15, 1000)
    q: Counter[str] = Counter()
    for lvl in (st.internal, st.external):
        if lvl is None:
            continue
        for e in lvl.events:
            if start <= e.time < end:
                q[str(e.displacement_qualifier.value)] += 1
    total = sum(q.values())
    print(f"\nstructure events in window: {total}")
    for name, n in q.most_common():
        print(f"  {name:<16} {n:>4}  {n / total:.0%}" if total else f"  {name}: {n}")


async def main() -> None:
    cfg = load_spec("pd_arrays")["displacement"]
    print("qualifier config:")
    print(f"  qualifierMinGrade     {cfg['qualifierMinGrade']}")
    print(f"  qualifierLookbackBars {cfg['qualifierLookbackBars']}")
    print(f"  gradesAtr             {cfg['gradesAtr']}")
    print(f"  minBodyPct            {cfg['minBodyPct']}   maxLegCandles {cfg['maxLegCandles']}")
    for start_s, end_s, label in WINDOWS:
        await measure(start_s, end_s, label)


if __name__ == "__main__":
    asyncio.run(main())
