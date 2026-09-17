"""Historical-file provider: serves the VALIDATED research copy (DATA_ROOT/cleaned) as market data.

Ported from V2. It is not a live feed: there are no quotes, and the newest bar is wherever the files
end, so every consumer sees STALE data and live decisions stay UNAVAILABLE. It refuses to serve a
symbol when:
- no validation manifest exists, or the dataset is NOT_APPROVED;
- a raw source file recorded in the manifest is missing or changed size since validation.

Only native intraday files are served (M1/M5/M15/M30/H1). H4/D1 are built by the candle engine on the
New York close; the provider-clock native H4/D1 files are deliberately not used.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path, PureWindowsPath
from typing import Any

import polars as pl

from app.domain.candle import RawBar
from app.domain.enums import MarketStatus, ProviderHealthStatus, ResearchStatus, Timeframe
from app.domain.instrument import Instrument, get_instrument, instrument_registry
from app.domain.quote import Quote
from app.providers.base import (
    ProviderError,
    ProviderHealth,
    ProviderUnavailableError,
    UnsupportedSymbolError,
)
from app.services.data_quality.market_hours import market_status_at

NATIVE = (Timeframe.M1, Timeframe.M5, Timeframe.M15, Timeframe.M30, Timeframe.H1)
SERVABLE = (ResearchStatus.APPROVED_FOR_RESEARCH, ResearchStatus.APPROVED_WITH_WARNINGS)


class HistoricalFileProvider:
    name = "historical_file"
    is_synthetic = False
    source_kind = "HISTORICAL"
    source = "historical_file"

    def __init__(self, data_root: str | Path) -> None:
        self._root = Path(data_root) if str(data_root) else None

    # --- dataset gate -------------------------------------------------------------------------

    def dataset(self, symbol: str) -> dict[str, Any] | None:
        if self._root is None:
            return None
        path = self._root / "manifests" / symbol.upper() / "dataset.json"
        try:
            payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return payload

    def _raw_path(self, recorded: str, symbol: str) -> Path:
        """Resolve a manifest path recorded on another host (validation runs on Windows)."""
        path = Path(recorded)
        if path.exists() or self._root is None:
            return path
        source = PureWindowsPath(recorded) if "\\" in recorded else path
        relative = Path(symbol.upper()) / source.parent.name / source.name
        candidates = (self._root / "raw" / relative, self._root / "Raw" / relative)
        return next((c for c in candidates if c.is_file()), candidates[0])

    def refusal(self, symbol: str) -> str | None:
        """Why this symbol cannot be served (None = servable)."""
        if self._root is None:
            return "DATA_ROOT is not configured"
        payload = self.dataset(symbol)
        if payload is None:
            return f"no validated dataset for {symbol.upper()} (run the historical validator)"
        status = payload.get("status")
        if status not in SERVABLE:
            return f"dataset for {symbol.upper()} is {status}: not served"
        for entry in payload.get("rawFiles", []):
            path = self._raw_path(str(entry.get("file", "")), symbol)
            if not path.is_file():
                return f"raw source {path.name} is missing since validation"
            if path.stat().st_size != entry.get("bytes"):
                return f"raw source {path.name} changed since validation: re-run the validator"
        return None

    # --- MarketDataProvider -------------------------------------------------------------------

    async def get_latest_quote(self, symbol: str) -> Quote:
        raise ProviderUnavailableError("historical files have no live quotes")

    async def get_historical_bars(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int | None = None,
    ) -> list[RawBar]:
        instrument = get_instrument(symbol)
        if instrument is None:
            raise UnsupportedSymbolError(symbol)
        if timeframe not in NATIVE:
            raise ProviderError(f"{timeframe} is not served natively (built by the candle engine)")
        if start is None and limit is None:
            raise ProviderError("a start or a limit is required (bulk history is never loaded whole)")
        reason = self.refusal(instrument.symbol)
        if reason:
            raise ProviderUnavailableError(reason)
        assert self._root is not None
        folder = self._root / "cleaned" / instrument.symbol / timeframe.value
        if not folder.is_dir():
            raise ProviderUnavailableError(f"no cleaned {timeframe} data for {instrument.symbol}")
        frame = pl.scan_parquet(folder / "**" / "*.parquet", hive_partitioning=True).select(
            "open_time", "open", "high", "low", "close", "volume"
        )
        if start is not None:
            frame = frame.filter(pl.col("open_time") >= start)
        if end is not None:
            frame = frame.filter(pl.col("open_time") < end)
        frame = frame.top_k(limit, by="open_time") if limit is not None else frame
        rows = frame.sort("open_time").collect()
        return [
            RawBar(
                symbol=instrument.symbol,
                timeframe=timeframe,
                open_time=open_time,
                open=o,
                high=h,
                low=lo,
                close=c,
                volume=v,
            )
            for open_time, o, h, lo, c, v in rows.iter_rows()
        ]

    async def subscribe_quotes(self, symbols: list[str]) -> AsyncIterator[Quote]:
        raise ProviderUnavailableError("historical files have no live quotes")
        yield  # pragma: no cover

    async def subscribe_bars(
        self, symbols: list[str], timeframes: list[Timeframe]
    ) -> AsyncIterator[RawBar]:
        raise ProviderUnavailableError("historical files do not stream")
        yield  # pragma: no cover

    async def get_instrument_metadata(self, symbol: str) -> Instrument:
        instrument = get_instrument(symbol)
        if instrument is None:
            raise UnsupportedSymbolError(symbol)
        return instrument

    async def get_market_status(self, symbol: str) -> MarketStatus:
        instrument = await self.get_instrument_metadata(symbol)
        return market_status_at(instrument.asset_class, datetime.now(UTC))

    async def health_check(self) -> ProviderHealth:
        now = datetime.now(UTC)
        if self._root is None:
            return ProviderHealth(
                provider=self.name,
                status=ProviderHealthStatus.DOWN,
                checked_at=now,
                is_synthetic=False,
                message="DATA_ROOT is not configured",
            )
        servable = {s: self.dataset(s) for s in instrument_registry() if self.refusal(s) is None}
        if not servable:
            return ProviderHealth(
                provider=self.name,
                status=ProviderHealthStatus.DOWN,
                checked_at=now,
                is_synthetic=False,
                message="no validated dataset can be served",
            )
        warned = [
            s
            for s, d in servable.items()
            if d and d.get("status") == ResearchStatus.APPROVED_WITH_WARNINGS
        ]
        return ProviderHealth(
            provider=self.name,
            status=ProviderHealthStatus.DEGRADED if warned else ProviderHealthStatus.HEALTHY,
            checked_at=now,
            is_synthetic=False,
            message=(
                f"historical files (not live) for {sorted(servable)}"
                + (f"; approved with warnings: {sorted(warned)}" if warned else "")
            ),
        )
