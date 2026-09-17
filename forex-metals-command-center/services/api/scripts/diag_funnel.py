"""Setup-funnel diagnostic runner (measurement only; changes no gate, threshold or strategyVersion).

Replays the REAL setup engine across a historical range at every closed setup-timeframe candle,
exactly as the backtester does, and reports WHY setups terminated instead of only how many did.

Usage:
    uv run python -m scripts.diag_funnel XAUUSD 2024-03-04 2024-03-18 [--stride N]

Requires the historical provider: set MARKET_DATA_PROVIDER=historical_file and DATA_ROOT in .env.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import UTC, datetime

from app.config import get_settings
from app.domain.enums import SetupState
from app.providers.registry import default_registry
from app.services.candles.service import CandleService
from app.services.setup_state.funnel import TerminalReason, summarize
from app.services.setup_state.models import SetupConfig
from app.services.setup_state.service import SetupService


def _utc(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=UTC)


class Clock:
    def __init__(self, at: datetime) -> None:
        self.at = at

    def __call__(self) -> datetime:
        return self.at


async def run(symbol: str, start: datetime, end: datetime, stride: int) -> int:
    settings = get_settings()
    clock = Clock(end)
    provider = default_registry(
        tradelocker_server=settings.tradelocker_server,
        tradelocker_email=(
            settings.tradelocker_email.get_secret_value() if settings.tradelocker_email else ""
        ),
        tradelocker_password=(
            settings.tradelocker_password.get_secret_value() if settings.tradelocker_password else ""
        ),
        tradelocker_account_server=settings.tradelocker_account_server,
        data_root=settings.data_root,
    ).create(settings.market_data_provider)
    candles = CandleService(provider, clock)
    setups_svc = SetupService(candles)

    tf = SetupConfig.from_spec().setup_timeframe
    span_bars = int((end - start) / tf.duration) + 2
    series = await candles.load_series(symbol, tf, min(span_bars, 1000), end)
    steps = [c.close_time for c in series.candles if c.is_closed and start < c.close_time <= end]
    if not steps:
        print("no closed bars in that range (check DATA_ROOT and the dates)")
        return 1
    steps = steps[::stride]

    print(f"{symbol} {start:%Y-%m-%d} -> {end:%Y-%m-%d}: {len(steps)} step(s) on {tf.value}")

    seen: dict[str, object] = {}
    reached: Counter[str] = Counter()
    ineligible = 0

    for n, t in enumerate(steps, 1):
        clock.at = t
        analysis = await setups_svc.analyze(symbol, t)
        if not analysis.eligible_for_decision:
            ineligible += 1
            continue
        for s in analysis.setups:
            seen[s.id] = s
            reached[s.state.value] += 1
        if n % 50 == 0:
            print(f"  ...{n}/{len(steps)} steps, {len(seen)} setups so far")

    diagnosis = summarize(list(seen.values()))  # type: ignore[arg-type]
    print()
    print(f"steps evaluated : {len(steps)}  ({ineligible} ineligible)")
    print(diagnosis.report())

    armed = sum(
        1
        for s in seen.values()
        if getattr(s, "state", None)
        in (SetupState.SETUP_ARMED, SetupState.ENTRY_ZONE_TOUCHED, SetupState.WAITING_FOR_CONFIRMATION)
        or getattr(s, "entry_plan", None) is not None
    )
    confirmed = sum(1 for s in seen.values() if getattr(s, "entry_plan", None) is not None)
    print()
    print(f"ever armed or beyond : {armed}")
    print(f"confirmed plans      : {confirmed}")
    if confirmed == 0 and diagnosis.dominant is not None:
        print()
        print(f"NOTHING CONFIRMED. Dominant terminal cause: {diagnosis.dominant.value}")
        print(_hint(diagnosis.dominant))
    return 0


def _hint(code: TerminalReason) -> str:
    hints = {
        TerminalReason.EXPIRED_TRADING_DAY: (
            "Setups are being killed at the trading-day boundary. Check expireAtTradingDayEnd "
            "and whether the retracement/confirmation windows can even fit inside one session."
        ),
        TerminalReason.EXPIRED_NO_MSS: (
            "Sweeps happen but no qualifying market-structure shift follows within the window. "
            "Check mss_window_bars and the displacement qualifier."
        ),
        TerminalReason.EXPIRED_NO_FVG: (
            "MSS occurs but the displacement leg leaves no acceptable FVG. Check the minimum "
            "gap size / quality threshold."
        ),
        TerminalReason.EXPIRED_NO_RETRACEMENT: (
            "Entry zones form but price never returns. Check retracement_window_bars."
        ),
        TerminalReason.EXPIRED_NO_CONFIRMATION: (
            "Price reaches the zone but the LTF confirmation never arrives. Check the "
            "confirmation window and the execution timeframe."
        ),
        TerminalReason.INVALIDATED_HTF_FLIP: (
            "HTF bias flips before setups mature. This is the D1/H4 dual-alignment suspect: "
            "check whether both timeframes must agree and how often that agreement holds."
        ),
        TerminalReason.INVALIDATED_LIQUIDITY_RAN: (
            "Price closes beyond the swept extreme: sweeps are being read as reversals when "
            "they continue. Check the sweep qualifier."
        ),
        TerminalReason.INVALIDATED_DO_NOT_CHASE: (
            "Targets are hit before entry confirms. The confirmation path is too slow relative "
            "to the target distance."
        ),
    }
    return hints.get(code, "Inspect the setups with this reason to see which gate is responsible.")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("symbol")
    p.add_argument("start", type=_utc)
    p.add_argument("end", type=_utc)
    p.add_argument("--stride", type=int, default=1, help="evaluate every Nth step (speed vs detail)")
    args = p.parse_args()
    if args.start >= args.end:
        p.error("start must be before end")
    raise SystemExit(asyncio.run(run(args.symbol, args.start, args.end, max(1, args.stride))))


if __name__ == "__main__":
    main()
