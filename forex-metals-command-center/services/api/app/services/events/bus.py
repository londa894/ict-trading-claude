"""Event bus boundary. Phase 0 ships an in-process implementation; a Redis-backed bus replaces it
when live streaming arrives, without changing publishers or subscribers."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Protocol

from pydantic import Field

from app.domain.base import ApiModel


class DomainEvent(ApiModel):
    type: str
    symbol: str | None = None
    occurred_at: datetime
    payload: dict[str, object] = Field(default_factory=dict)


Handler = Callable[[DomainEvent], Awaitable[None]]


class EventBus(Protocol):
    async def publish(self, event: DomainEvent) -> None: ...

    def subscribe(self, event_type: str, handler: Handler) -> None: ...


class InMemoryEventBus:
    """Delivers events to handlers in subscription order. A failing handler never blocks others."""

    def __init__(self) -> None:
        self._handlers: dict[str, list[Handler]] = defaultdict(list)
        self.failures: list[tuple[DomainEvent, BaseException]] = []

    def subscribe(self, event_type: str, handler: Handler) -> None:
        self._handlers[event_type].append(handler)

    async def publish(self, event: DomainEvent) -> None:
        for handler in [*self._handlers[event.type], *self._handlers["*"]]:
            try:
                await handler(event)
            except Exception as exc:  # isolate subscribers from each other
                self.failures.append((event, exc))
