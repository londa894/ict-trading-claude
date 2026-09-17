"""Instrument identity + optional contract spec (spec STEP 6)."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field

from app.contracts import load_spec
from app.domain.base import ApiModel
from app.domain.enums import AssetClass, PositionSizeStatus


class InstrumentSpec(ApiModel):
    """Contract details. `tick_value` is the value of one tick for 1.0 volume, in `quote_currency`."""

    symbol: str
    contract_size: float = Field(gt=0)
    tick_size: float = Field(gt=0)
    tick_value: float = Field(gt=0)
    min_volume: float = Field(gt=0)
    volume_step: float = Field(gt=0)
    quote_currency: str = Field(pattern=r"^[A-Z]{3}$")
    typical_spread: float | None = Field(default=None, ge=0)
    platform_pip_size: float | None = Field(default=None, gt=0)


class Instrument(ApiModel):
    symbol: str
    asset_class: AssetClass
    base: str
    quote: str
    price_precision: int = Field(ge=0)
    priority: int = Field(ge=1)
    deeply_validated: bool
    spec: InstrumentSpec | None = None

    @property
    def position_size_status(self) -> PositionSizeStatus:
        """Unknown contract spec -> POSITION_SIZE_UNVERIFIED. Never guessed."""
        return PositionSizeStatus.VERIFIED if self.spec else PositionSizeStatus.POSITION_SIZE_UNVERIFIED


@lru_cache(maxsize=1)
def instrument_registry() -> dict[str, Instrument]:
    raw = load_spec("instruments")["instruments"]
    instruments = [Instrument.model_validate(item) for item in raw]
    return {i.symbol: i for i in instruments}


def get_instrument(symbol: str) -> Instrument | None:
    return instrument_registry().get(symbol.upper())
