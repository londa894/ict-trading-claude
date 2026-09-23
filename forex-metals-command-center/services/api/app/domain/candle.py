"""Normalized candle and raw provider bar models."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from app.domain.base import ApiModel, require_utc
from app.domain.enums import DataQuality, Timeframe


class RawBar(BaseModel):
    """Loosely-typed bar exactly as a provider delivered it. Never consumed by analysis engines."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    timeframe: Timeframe
    open_time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float | None = None


class Candle(ApiModel):
    """Normalized candle (spec STEP 12).

    Structural invariants enforced here: UTC timestamps and close_time == open_time + timeframe.
    Price sanity (impossible OHLC etc.) is NOT enforced at construction so that invalid data can be
    represented and reported; engines must only consume candles from a validated series.
    """

    symbol: str
    timeframe: Timeframe
    open_time: datetime
    close_time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float | None
    source: str
    is_closed: bool
    data_quality: DataQuality

    @field_validator("open_time", "close_time")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return require_utc(value, "candle timestamp")

    @model_validator(mode="after")
    def _close_time_matches_timeframe(self) -> Candle:
        # W1 (Sunday-anchored trading week) and MN1 have calendar-variable UTC lengths — a week that
        # spans a DST change is 7d +/- 1h — so only ordering is enforced. Fixed-duration timeframes
        # (intraday, H4, D1) must match their duration exactly.
        if self.timeframe.is_trading_week_anchored or self.timeframe is Timeframe.MN1:
            if self.close_time <= self.open_time:
                raise ValueError("close_time must be after open_time")
        elif self.close_time - self.open_time != self.timeframe.duration:
            raise ValueError("close_time must equal open_time + timeframe duration")
        return self

    def same_prices(self, other: Candle) -> bool:
        return (self.open, self.high, self.low, self.close, self.volume) == (
            other.open,
            other.high,
            other.low,
            other.close,
            other.volume,
        )
