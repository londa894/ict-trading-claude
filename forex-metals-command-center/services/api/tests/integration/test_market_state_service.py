from app.services.events.bus import InMemoryEventBus
from app.services.market_state.service import DECISION_CHANGED, MarketStateService
from tests.helpers import TUE_10_UTC, StaticProvider, after_last, consecutive_bars


async def test_decision_changed_published_only_on_change():
    raw = consecutive_bars(TUE_10_UTC, 30)
    clock_value = [after_last(raw)]
    bus = InMemoryEventBus()
    events = []

    async def collect(e):
        events.append(e)

    bus.subscribe(DECISION_CHANGED, collect)
    svc = MarketStateService(StaticProvider(raw), bus, clock=lambda: clock_value[0])

    await svc.evaluate("XAUUSD")
    await svc.evaluate("XAUUSD")
    assert len(events) == 1
    assert events[0].payload["verdict"] == "WAIT"

    # Time passes without new bars -> STALE -> UNAVAILABLE -> one new event.
    from datetime import timedelta

    clock_value[0] = clock_value[0] + timedelta(hours=1)
    r = await svc.evaluate("XAUUSD")
    assert r.decision.verdict == "UNAVAILABLE"
    assert len(events) == 2
    assert events[1].payload["dataQuality"] == "STALE"


async def test_same_decision_object_shape_for_every_symbol():
    raw = consecutive_bars(TUE_10_UTC, 10)
    svc = MarketStateService(StaticProvider(raw), InMemoryEventBus(), clock=lambda: after_last(raw))
    a = await svc.evaluate("XAUUSD")
    b = await svc.evaluate("EURUSD")  # no EURUSD bars in provider -> EMPTY_SERIES
    assert set(a.decision.model_dump()) == set(b.decision.model_dump())
    assert b.decision.verdict == "UNAVAILABLE"
    assert "NO_DATA" in b.decision.blockers
