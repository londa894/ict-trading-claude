"""TradeLocker live market-data provider (LivvFX / bsa.tradelocker.com).

Authenticates with email + password + server name, auto-refreshes the JWT,
and serves live quotes and historical bars via the TradeLocker REST API.
No order execution or account access: market data only.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.domain.candle import RawBar
from app.domain.enums import MarketStatus, ProviderHealthStatus, Timeframe
from app.domain.instrument import Instrument, get_instrument
from app.domain.quote import Quote, DataQuality
from app.providers.base import (
    ProviderError,
    ProviderHealth,
    ProviderUnavailableError,
    UnsupportedSymbolError,
)
from app.services.data_quality.market_hours import market_status_at

logger = logging.getLogger("fmcc.tradelocker")

# TradeLocker timeframe IDs (resolution in seconds)
_TF_TO_RESOLUTION: dict[Timeframe, int] = {
    Timeframe.M1: 60,
    Timeframe.M3: 180,
    Timeframe.M5: 300,
    Timeframe.M15: 900,
    Timeframe.M30: 1800,
    Timeframe.H1: 3600,
    Timeframe.H4: 14400,
    Timeframe.D1: 86400,
}

# XAUUSD symbol mapping on TradeLocker (LivvFX)
_SYMBOL_MAP: dict[str, str] = {
    "XAUUSD": "XAUUSD",
    "XAGUSD": "XAGUSD",
}

_TOKEN_MARGIN = timedelta(minutes=5)  # refresh this early before expiry
_REQUEST_TIMEOUT = 15.0


class TradeLockerProvider:
    """Live market-data provider backed by TradeLocker REST API."""

    name = "tradelocker"
    is_synthetic = False
    source = "tradelocker:live"

    def __init__(
        self,
        base_url: str,
        email: str,
        password: str,
        server: str,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._email = email
        self._password = password
        self._server = server
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._token_expiry: datetime = datetime.min.replace(tzinfo=UTC)
        self._account_id: str | None = None
        self._acc_num: str | None = None
        self._route_id: str | None = None
        self._lock = asyncio.Lock()
        self._client = httpx.AsyncClient(timeout=_REQUEST_TIMEOUT)

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    async def _authenticate(self) -> None:
        """Obtain a fresh JWT via email + password."""
        resp = await self._client.post(
            f"{self._base}/auth/jwt/token",
            json={"email": self._email, "password": self._password, "server": self._server},
        )
        if resp.status_code == 400:
            raise ProviderUnavailableError("TradeLocker auth failed: incorrect credentials or server name")
        resp.raise_for_status()
        data = resp.json()
        self._access_token = data["accessToken"]
        self._refresh_token = data.get("refreshToken")
        expires_in = int(data.get("expiresIn", 3600))
        self._token_expiry = datetime.now(UTC) + timedelta(seconds=expires_in)
        await self._resolve_account()

    async def _refresh(self) -> None:
        """Use the refresh token to get a new access token."""
        if not self._refresh_token:
            await self._authenticate()
            return
        try:
            resp = await self._client.post(
                f"{self._base}/auth/jwt/refresh",
                json={"refreshToken": self._refresh_token},
            )
            resp.raise_for_status()
            data = resp.json()
            self._access_token = data["accessToken"]
            self._refresh_token = data.get("refreshToken", self._refresh_token)
            expires_in = int(data.get("expiresIn", 3600))
            self._token_expiry = datetime.now(UTC) + timedelta(seconds=expires_in)
        except httpx.HTTPStatusError:
            await self._authenticate()

    async def _ensure_token(self) -> str:
        async with self._lock:
            if self._access_token is None:
                await self._authenticate()
            elif datetime.now(UTC) >= self._token_expiry - _TOKEN_MARGIN:
                await self._refresh()
        assert self._access_token is not None
        return self._access_token

    def _headers(self, token: str) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {token}"}
        if self._acc_num:
            headers["accNum"] = self._acc_num
        return headers

    # ------------------------------------------------------------------
    # Account resolution
    # ------------------------------------------------------------------

    async def _resolve_account(self) -> None:
        """Find the first live account id and route instrument id for XAUUSD."""
        token = self._access_token
        assert token
        resp = await self._client.get(
            f"{self._base}/auth/jwt/all-accounts",
            headers={"Authorization": f"Bearer {token}"},
        )
        resp.raise_for_status()
        accounts = resp.json().get("accounts", [])
        if not accounts:
            raise ProviderUnavailableError("No TradeLocker accounts found")
        # Pick first account
        acc = accounts[0]
        self._account_id = str(acc.get("id", ""))
        self._acc_num = str(acc.get("accNum", ""))
        logger.info("TradeLocker: using account %s (accNum=%s)", self._account_id, self._acc_num)

    # ------------------------------------------------------------------
    # Symbol resolution
    # ------------------------------------------------------------------

    async def _route_instrument_id(self, symbol: str, token: str) -> int:
        """Look up the routeInstrumentId for a symbol."""
        resp = await self._client.get(
            f"{self._base}/trade/instruments",
            headers=self._headers(token),
            params={"locale": "en"},
        )
        resp.raise_for_status()
        tl_symbol = _SYMBOL_MAP.get(symbol.upper(), symbol.upper())
        for inst in resp.json().get("d", {}).get("instruments", []):
            if inst.get("name", "").upper() == tl_symbol:
                return int(inst["routeId"])
        raise UnsupportedSymbolError(f"{symbol} not found on TradeLocker account")

    # ------------------------------------------------------------------
    # MarketDataProvider interface
    # ------------------------------------------------------------------

    async def get_latest_quote(self, symbol: str) -> Quote:
        instrument = get_instrument(symbol)
        if instrument is None:
            raise UnsupportedSymbolError(symbol)
        token = await self._ensure_token()
        route_id = await self._route_instrument_id(symbol, token)
        resp = await self._client.get(
            f"{self._base}/trade/quotes",
            headers=self._headers(token),
            params={"routeInstrumentId": route_id},
        )
        resp.raise_for_status()
        data = resp.json().get("d", {})
        bid = float(data.get("bid", 0))
        ask = float(data.get("ask", 0))
        if bid <= 0 or ask <= 0:
            raise ProviderError(f"Invalid quote for {symbol}: bid={bid} ask={ask}")
        return Quote(
            symbol=instrument.symbol,
            bid=round(bid, instrument.price_precision),
            ask=round(ask, instrument.price_precision),
            timestamp=datetime.now(UTC),
            source=self.source,
            data_quality=DataQuality.LIVE,
        )

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
        resolution = _TF_TO_RESOLUTION.get(timeframe)
        if resolution is None:
            raise ProviderError(f"Timeframe {timeframe} not supported by TradeLocker provider")
        token = await self._ensure_token()
        route_id = await self._route_instrument_id(symbol, token)
        now = datetime.now(UTC)
        end_ts = int((end or now).timestamp()) * 1000
        if start is not None:
            start_ts = int(start.timestamp()) * 1000
        elif limit is not None:
            start_ts = int((now - timedelta(seconds=resolution * limit * 2)).timestamp()) * 1000
        else:
            raise ProviderError("Either start or limit is required")

        resp = await self._client.get(
            f"{self._base}/trade/history",
            headers=self._headers(token),
            params={
                "routeInstrumentId": route_id,
                "resolution": resolution,
                "from": start_ts,
                "to": end_ts,
                "countBack": limit or 5000,
            },
        )
        resp.raise_for_status()
        raw: dict[str, Any] = resp.json().get("d", {})
        times = raw.get("t", [])
        opens = raw.get("o", [])
        highs = raw.get("h", [])
        lows = raw.get("l", [])
        closes = raw.get("c", [])
        volumes = raw.get("v", [])

        bars: list[RawBar] = []
        for i, ts in enumerate(times):
            open_time = datetime.fromtimestamp(ts / 1000, tz=UTC)
            if start is not None and open_time < start:
                continue
            if end is not None and open_time >= end:
                continue
            bars.append(
                RawBar(
                    symbol=instrument.symbol,
                    timeframe=timeframe,
                    open_time=open_time,
                    open=float(opens[i]),
                    high=float(highs[i]),
                    low=float(lows[i]),
                    close=float(closes[i]),
                    volume=float(volumes[i]) if volumes else None,
                )
            )
        bars.sort(key=lambda b: b.open_time)
        if limit is not None:
            bars = bars[-limit:]
        return bars

    async def subscribe_quotes(self, symbols: list[str]) -> AsyncIterator[Quote]:
        """Polling-based quote stream (1 second interval)."""
        while True:
            for symbol in symbols:
                try:
                    quote = await self.get_latest_quote(symbol)
                    yield quote
                except Exception as exc:
                    logger.warning("quote poll failed for %s: %s", symbol, exc)
            await asyncio.sleep(1.0)

    async def subscribe_bars(
        self, symbols: list[str], timeframes: list[Timeframe]
    ) -> AsyncIterator[RawBar]:
        """Polling-based bar stream — yields the latest closed bar each poll cycle."""
        seen: dict[tuple[str, Timeframe], datetime] = {}
        while True:
            for symbol in symbols:
                for tf in timeframes:
                    try:
                        bars = await self.get_historical_bars(symbol, tf, limit=2)
                        if bars:
                            bar = bars[-1]
                            key = (symbol, tf)
                            if seen.get(key) != bar.open_time:
                                seen[key] = bar.open_time
                                yield bar
                    except Exception as exc:
                        logger.warning("bar poll failed for %s %s: %s", symbol, tf, exc)
            await asyncio.sleep(5.0)

    async def get_instrument_metadata(self, symbol: str) -> Instrument:
        instrument = get_instrument(symbol)
        if instrument is None:
            raise UnsupportedSymbolError(symbol)
        return instrument

    async def get_market_status(self, symbol: str) -> MarketStatus:
        instrument = get_instrument(symbol)
        if instrument is None:
            raise UnsupportedSymbolError(symbol)
        return market_status_at(instrument.asset_class, datetime.now(UTC))

    async def health_check(self) -> ProviderHealth:
        now = datetime.now(UTC)
        try:
            await self._ensure_token()
            quote = await self.get_latest_quote("XAUUSD")
            return ProviderHealth(
                provider=self.name,
                status=ProviderHealthStatus.HEALTHY,
                checked_at=now,
                is_synthetic=False,
                message=f"TradeLocker live (LivvFX) — XAUUSD bid={quote.bid} ask={quote.ask}",
            )
        except ProviderUnavailableError as exc:
            return ProviderHealth(
                provider=self.name,
                status=ProviderHealthStatus.DOWN,
                checked_at=now,
                is_synthetic=False,
                message=str(exc),
            )
        except Exception as exc:
            logger.exception("TradeLocker health check failed")
            return ProviderHealth(
                provider=self.name,
                status=ProviderHealthStatus.DOWN,
                checked_at=now,
                is_synthetic=False,
                message=f"unexpected error: {type(exc).__name__}",
            )
