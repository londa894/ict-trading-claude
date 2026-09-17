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

# TradeLocker resolution tokens, with each bucket's duration for windowing.
_TF_TO_RESOLUTION: dict[Timeframe, str] = {
    Timeframe.M1: "1m",
    Timeframe.M5: "5m",
    Timeframe.M15: "15m",
    Timeframe.M30: "30m",
    Timeframe.H1: "1h",
    Timeframe.H4: "4h",
    Timeframe.D1: "1D",
}

_TF_SECONDS: dict[Timeframe, int] = {
    Timeframe.M1: 60,
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
        self._instrument_cache: dict[str, tuple[int, int]] = {}
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
        acc = accounts[0]
        self._account_id = str(acc.get("id", ""))
        self._acc_num = str(acc.get("accNum", ""))
        self._instrument_cache.clear()  # ids are account-scoped
        if not self._account_id or not self._acc_num:
            raise ProviderUnavailableError("TradeLocker account is missing id or accNum")
        logger.info("TradeLocker: using account %s (accNum=%s)", self._account_id, self._acc_num)

    # ------------------------------------------------------------------
    # Symbol resolution
    # ------------------------------------------------------------------

    async def _instrument_route(self, symbol: str, token: str) -> tuple[int, int]:
        """Return (tradableInstrumentId, infoRouteId), cached per symbol.

        Instruments are account-scoped on TradeLocker. Each instrument carries routes;
        market data uses the INFO route, which is distinct from the TRADE route.
        """
        key = symbol.upper()
        cached = self._instrument_cache.get(key)
        if cached is not None:
            return cached
        if not self._account_id:
            raise ProviderUnavailableError("TradeLocker account not resolved")
        resp = await self._client.get(
            f"{self._base}/trade/accounts/{self._account_id}/instruments",
            headers=self._headers(token),
            params={"locale": "en"},
        )
        resp.raise_for_status()
        payload = resp.json().get("d", {})
        instruments = payload.get("instruments", payload if isinstance(payload, list) else [])
        tl_symbol = _SYMBOL_MAP.get(key, key)
        for inst in instruments:
            name = str(inst.get("name", "")).upper()
            if name != tl_symbol:
                continue
            tradable_id = int(inst.get("tradableInstrumentId", inst.get("id", 0)))
            routes = inst.get("routes", [])
            info_route = next(
                (r for r in routes if str(r.get("type", "")).upper() == "INFO"),
                routes[0] if routes else None,
            )
            if info_route is None or not tradable_id:
                raise ProviderError(f"{symbol}: TradeLocker returned no usable route")
            resolved = (tradable_id, int(info_route["id"]))
            self._instrument_cache[key] = resolved
            logger.info(
                "TradeLocker: %s -> tradableInstrumentId=%s routeId=%s", key, resolved[0], resolved[1]
            )
            return resolved
        available = sorted({str(i.get("name", "")) for i in instruments})[:15]
        raise UnsupportedSymbolError(
            f"{symbol} not offered on this TradeLocker account; sample of available: {available}"
        )

    # ------------------------------------------------------------------
    # MarketDataProvider interface
    # ------------------------------------------------------------------

    async def get_latest_quote(self, symbol: str) -> Quote:
        instrument = get_instrument(symbol)
        if instrument is None:
            raise UnsupportedSymbolError(symbol)
        token = await self._ensure_token()
        tradable_id, route_id = await self._instrument_route(symbol, token)
        resp = await self._client.get(
            f"{self._base}/trade/quotes",
            headers=self._headers(token),
            params={"routeId": route_id, "tradableInstrumentId": tradable_id},
        )
        resp.raise_for_status()
        data = resp.json().get("d", {})
        bid = float(data.get("bp", data.get("bid", 0)) or 0)
        ask = float(data.get("ap", data.get("ask", 0)) or 0)
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
        bucket_seconds = _TF_SECONDS.get(timeframe)
        if resolution is None or bucket_seconds is None:
            raise ProviderError(f"Timeframe {timeframe} not supported by TradeLocker provider")
        token = await self._ensure_token()
        tradable_id, route_id = await self._instrument_route(symbol, token)
        now = datetime.now(UTC)
        end_ts = int((end or now).timestamp()) * 1000
        if start is not None:
            start_ts = int(start.timestamp()) * 1000
        elif limit is not None:
            # Request extra span so weekends and market closures still yield `limit` bars.
            start_ts = int((now - timedelta(seconds=bucket_seconds * limit * 3)).timestamp()) * 1000
        else:
            raise ProviderError("Either start or limit is required")

        resp = await self._client.get(
            f"{self._base}/trade/history",
            headers=self._headers(token),
            params={
                "routeId": route_id,
                "tradableInstrumentId": tradable_id,
                "resolution": resolution,
                "from": start_ts,
                "to": end_ts,
            },
        )
        resp.raise_for_status()
        payload: dict[str, Any] = resp.json().get("d", {})
        bar_details = payload.get("barDetails", [])

        bars: list[RawBar] = []
        for entry in bar_details:
            open_time = datetime.fromtimestamp(int(entry["t"]) / 1000, tz=UTC)
            if start is not None and open_time < start:
                continue
            if end is not None and open_time >= end:
                continue
            bars.append(
                RawBar(
                    symbol=instrument.symbol,
                    timeframe=timeframe,
                    open_time=open_time,
                    open=float(entry["o"]),
                    high=float(entry["h"]),
                    low=float(entry["l"]),
                    close=float(entry["c"]),
                    volume=float(entry["v"]) if entry.get("v") is not None else None,
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
        except ProviderError as exc:
            return ProviderHealth(
                provider=self.name,
                status=ProviderHealthStatus.DOWN,
                checked_at=now,
                is_synthetic=False,
                message=str(exc),
            )
        except Exception as exc:
            logger.exception("TradeLocker health check failed")
            detail = ""
            if isinstance(exc, httpx.HTTPStatusError):
                detail = f" ({exc.response.status_code} on {exc.request.url.path})"
            return ProviderHealth(
                provider=self.name,
                status=ProviderHealthStatus.DOWN,
                checked_at=now,
                is_synthetic=False,
                message=f"unexpected error: {type(exc).__name__}{detail}",
            )
