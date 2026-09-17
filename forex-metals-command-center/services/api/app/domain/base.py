from __future__ import annotations

from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class ApiModel(BaseModel):
    """Immutable model serialised with camelCase keys for the web client."""

    model_config = ConfigDict(
        frozen=True,
        populate_by_name=True,
        alias_generator=to_camel,
        extra="forbid",
    )


def is_utc(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() == timedelta(0)


def require_utc(value: datetime, field: str) -> datetime:
    if not is_utc(value):
        raise ValueError(f"{field} must be timezone-aware UTC, got {value!r}")
    return value.astimezone(UTC)
