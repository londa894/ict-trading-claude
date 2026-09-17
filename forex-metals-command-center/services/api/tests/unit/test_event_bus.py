from datetime import UTC, datetime

from app.services.events.bus import DomainEvent, InMemoryEventBus

NOW = datetime(2024, 1, 9, 10, 0, tzinfo=UTC)


async def test_publish_delivers_in_order_and_wildcard():
    bus = InMemoryEventBus()
    seen: list[str] = []

    async def h1(e):
        seen.append(f"h1:{e.type}")

    async def h2(e):
        seen.append(f"h2:{e.type}")

    async def star(e):
        seen.append(f"*:{e.type}")

    bus.subscribe("a", h1)
    bus.subscribe("a", h2)
    bus.subscribe("*", star)
    await bus.publish(DomainEvent(type="a", occurred_at=NOW))
    await bus.publish(DomainEvent(type="b", occurred_at=NOW))
    assert seen == ["h1:a", "h2:a", "*:a", "*:b"]


async def test_failing_handler_is_isolated():
    bus = InMemoryEventBus()
    seen = []

    async def boom(e):
        raise RuntimeError("x")

    async def ok(e):
        seen.append(e.type)

    bus.subscribe("a", boom)
    bus.subscribe("a", ok)
    await bus.publish(DomainEvent(type="a", occurred_at=NOW))
    assert seen == ["a"]
    assert len(bus.failures) == 1
