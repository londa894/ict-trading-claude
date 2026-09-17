from __future__ import annotations

from datetime import datetime

from pydantic import computed_field, field_validator

from app.domain.base import ApiModel, require_utc
from app.domain.enums import DataQuality


class Quote(ApiModel):
    symbol: str
    bid: float
    ask: float
    timestamp: datetime
    source: str
    data_quality: DataQuality

    @field_validator("timestamp")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return require_utc(value, "quote timestamp")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2

    @computed_field  # type: ignore[prop-decorator]
    @property
    def spread(self) -> float:
        return self.ask - self.bid
