"""Alert monitor, feed and ready-watch API (Phase 11) on real engine decisions."""

import json
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.domain.enums import AlertType, ReadyWatchState, Timeframe, Verdict
from app.main import create_app
from app.providers.fixture import SERIES_END, SyntheticFixtureProvider
from app.services.alerts.models import (
    Alert,
    AlertConfig,
    AlertFeed,
    MonitorStatus,
    ReadyCondition,
    ReadyWatch,
    ReadyWatchRequest,
)
from app.services.alerts.service import AlertService, ReadyWatchLimitError
from app.services.candles.service import CandleService
from app.services.events.bus import InMemoryEventBus
from app.services.market_state.service import MarketStateService
from app.services.scoring.service import EvaluationService
from app.services.setup_state.service import SetupService

START = SERIES_END - timedelta(hours=30, minutes=-1)
CONTRACT = Path(__file__).resolve().parents[4] / "packages" / "shared-types" / "contract" / "api_fields.json"
CFG = AlertConfig.from_spec()


class NonSyntheticStub(SyntheticFixtureProvider):
    name = "stub-real"
    is_synthetic = False
    source = "stub-real"


class Clock:
    def __init__(self, t):
        self.t = t
        self.mono = 0.0

    def __call__(self):
        return self.t

    def advance(self, delta: timedelta):
        self.t += delta
        self.mono += delta.total_seconds()


def service(clock: Clock, provider=None, **kw) -> AlertService:
    provider = provider or NonSyntheticStub()
    candles = CandleService(provider, clock)
    setups = SetupService(candles)
    evaluation = EvaluationService(setups)
    market = MarketStateService(provider, InMemoryEventBus(), Timeframe.M5, clock, evaluation=evaluation)
    return AlertService(candles, market, evaluation, clock, monotonic=lambda: clock.mono, **kw)


async def test_monitor_baselines_then_alerts_on_new_engine_events():
    clock = Clock(START)
    svc = service(clock)
    assert await svc.cycle() == []  # silent baseline
    assert svc.status().baselined == ["XAUUSD"] and svc.status().cycles == 1

    emitted: list[Alert] = []
    for _ in range(12):  # six hours of fixture data, one cycle per 30 minutes
        clock.advance(timedelta(minutes=30))
        emitted += await svc.cycle()
    assert emitted, "six hours of engine events should raise alerts"
    for a in emitted:
        assert (a.category, a.priority) == CFG.types[a.type]
        assert a.symbol == "XAUUSD" and a.seq > 0 and a.created_at <= clock.t
        assert "LONG" not in a.title + a.message and "SHORT" not in a.title + a.message
        assert a.type not in (AlertType.READY, AlertType.MONITOR_FAILURE, AlertType.DATA_UNAVAILABLE)
    assert len({a.dedupe_key for a in emitted}) == len(emitted)  # no duplicate within the cooldown
    feed = svc.feed()
    assert [a.seq for a in feed.alerts] == sorted((a.seq for a in emitted), reverse=True)[:100]
    assert feed.authority == "NOT_AUTHORIZED" and feed.next_cursor == max(a.seq for a in emitted)
    assert svc.feed(since=feed.next_cursor).alerts == []


async def test_monitor_skips_symbols_without_a_new_bar_until_the_quiet_period():
    clock = Clock(START)
    svc = service(clock)
    await svc.cycle()
    calls = {"n": 0}
    real = svc._evaluate_symbol

    async def counting(symbol):
        calls["n"] += 1
        return await real(symbol)

    svc._evaluate_symbol = counting  # type: ignore[method-assign]
    clock.mono += 60  # same bar, one minute later
    await svc.cycle()
    assert calls["n"] == 0
    clock.mono += CFG.max_quiet_seconds  # quiet period elapsed -> re-evaluate (detects dead feeds)
    await svc.cycle()
    assert calls["n"] == 1


class FeedStopsAt(NonSyntheticStub):
    """A mid-week feed outage: no bars after `cutoff`."""

    def __init__(self, cutoff):
        super().__init__()
        self.cutoff = cutoff

    async def get_historical_bars(self, symbol, timeframe, start=None, end=None, limit=None):
        capped = min(end, self.cutoff) if end is not None else self.cutoff
        return await super().get_historical_bars(symbol, timeframe, start, capped, limit)


async def test_stale_data_raises_one_critical_alert():
    clock = Clock(START)
    svc = service(clock, provider=FeedStopsAt(START))
    await svc.cycle()
    clock.advance(timedelta(hours=2))  # Thursday: the market is open but no new bars arrive
    alerts = await svc.cycle()
    assert [a.type for a in alerts] == [AlertType.DATA_UNAVAILABLE]
    assert alerts[0].priority.value == "CRITICAL" and "DATA_STALE" in alerts[0].message
    clock.advance(timedelta(minutes=10))
    assert await svc.cycle(force=True) == []  # still unavailable: no repeat


async def test_a_failing_symbol_is_isolated():
    clock = Clock(START)
    svc = service(clock)
    svc.add_watch("EURUSD", None)
    real = svc._market_state.evaluate

    async def flaky(symbol):
        if symbol == "EURUSD":
            raise RuntimeError("feed exploded")
        return await real(symbol)

    svc._market_state.evaluate = flaky  # type: ignore[method-assign]
    first = await svc.cycle()
    assert [(a.type, a.symbol) for a in first] == [(AlertType.MONITOR_FAILURE, "EURUSD")]
    assert svc.status().last_error == "EURUSD: RuntimeError" and "XAUUSD" in svc.status().baselined
    assert await svc.cycle(force=True) == []  # cooldown: the failure is not repeated every cycle
    assert svc.store.suppressed["MONITOR_FAILURE"] == 1


async def test_ready_watch_tracks_conditions_but_never_fires_under_fail_safe():
    clock = Clock(START)
    svc = service(clock)
    watch = svc.add_watch("xauusd", "BULLISH")
    assert svc.add_watch("XAUUSD", "BULLISH").id == watch.id  # idempotent
    for _ in range(8):
        await svc.cycle(force=True)
        clock.advance(timedelta(minutes=45))
    [checked] = svc.watches()
    assert checked.state in (ReadyWatchState.WAITING, ReadyWatchState.GATES_PENDING)
    names = [c.name for c in checked.conditions]
    assert names[:2] == ["DATA_USABLE", "OPEN_SETUP"] and names[-3:] == [
        "NEWS_GATE",
        "VERDICT_AUTHORITY",
        "VERDICT",
    ]
    assert checked.fired_at is None and checked.next_required_event
    assert all(a.type is not AlertType.READY for a in svc.store.list())
    assert svc.remove_watch(watch.id) and not svc.remove_watch(watch.id)


async def test_ready_watch_fires_once_when_the_decision_is_directional_with_full_authority(monkeypatch):
    clock = Clock(START)
    svc = service(clock)
    watch = svc.add_watch("XAUUSD", None)
    real = svc._market_state.evaluate

    async def directional(symbol):
        response = await real(symbol)
        return response.model_copy(
            update={"decision": response.decision.model_copy(update={"verdict": Verdict.LONG})}
        )

    svc._market_state.evaluate = directional  # type: ignore[method-assign]
    from app.services.alerts import service as alert_service

    monkeypatch.setattr(alert_service, "verdict_authority", lambda: "FULL")
    await svc.cycle(force=True)
    await svc.cycle(force=True)
    ready = [a for a in svc.store.list() if a.type is AlertType.READY]
    assert len(ready) == 1 and ready[0].priority.value == "CRITICAL"
    assert svc.watches()[0].state is ReadyWatchState.FIRED and svc.watches()[0].id == watch.id


def test_watch_limits():
    clock = Clock(START)
    svc = service(clock)
    svc.add_watch("XAGUSD", None)
    svc.add_watch("EURUSD", None)
    with pytest.raises(ReadyWatchLimitError, match="monitored symbols"):
        svc.add_watch("GBPUSD", None)
    assert svc.monitored_symbols() == ["XAUUSD", "XAGUSD", "EURUSD"]


def client(provider=None) -> TestClient:
    settings = Settings(_env_file=None, alert_monitor_enabled=False)  # type: ignore[call-arg]
    return TestClient(
        create_app(settings=settings, provider=provider or NonSyntheticStub(), clock=lambda: START)
    )


def test_alert_api_endpoints():
    c = client()
    feed = c.get("/api/v1/alerts").json()
    assert (
        feed["alerts"] == [] and feed["authority"] == "NOT_AUTHORIZED" and feed["monitor"]["enabled"] is False
    )
    assert feed["monitor"]["symbols"] == ["XAUUSD"]
    assert c.get("/api/v1/alerts", params={"category": "BUY"}).status_code == 422
    assert (
        c.get("/api/v1/alerts", params={"minPriority": "HIGH", "symbol": "XAUUSD", "since": 3}).status_code
        == 200
    )

    r = c.post("/api/v1/alerts/ready-watches", json={"symbol": "XAUUSD", "direction": "BEARISH"})
    assert r.status_code == 201 and r.json()["state"] == "WAITING" and r.json()["firedAt"] is None
    watch_id = r.json()["id"]
    assert [w["id"] for w in c.get("/api/v1/alerts/ready-watches").json()] == [watch_id]
    assert c.post("/api/v1/alerts/ready-watches", json={"symbol": "BTCUSD"}).status_code == 404
    assert (
        c.post("/api/v1/alerts/ready-watches", json={"symbol": "XAUUSD", "direction": "LONG"}).status_code
        == 422
    )
    assert c.delete(f"/api/v1/alerts/ready-watches/{watch_id}").status_code == 204
    assert c.delete(f"/api/v1/alerts/ready-watches/{watch_id}").status_code == 404
    assert c.delete("/api/v1/alerts/ready-watches/nope").status_code == 422


def test_lifespan_starts_and_stops_the_monitor():
    settings = Settings(_env_file=None, alert_monitor_enabled=True)  # type: ignore[call-arg]
    app = create_app(
        settings=settings,
        provider=SyntheticFixtureProvider(),
        clock=lambda: SERIES_END + timedelta(seconds=30),
    )
    with TestClient(app) as c:
        for _ in range(200):
            status = c.get("/api/v1/alerts").json()["monitor"]
            if status["cycles"] >= 1:
                break
        assert status["enabled"] is True and status["cycles"] >= 1 and status["lastError"] is None


@pytest.mark.parametrize(
    "model", [Alert, MonitorStatus, AlertFeed, ReadyCondition, ReadyWatch, ReadyWatchRequest]
)
def test_alert_wire_fields_match_shared_contract(model):
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    assert sorted(f.alias or n for n, f in model.model_fields.items()) == sorted(contract[model.__name__])


async def test_a_new_watch_is_checked_on_the_next_cycle_without_a_new_bar():
    clock = Clock(START)
    svc = service(clock)
    await svc.cycle()
    watch = svc.add_watch("XAUUSD", None)
    clock.mono += 60  # same bar, not quiet
    await svc.cycle()
    [checked] = svc.watches()
    assert checked.id == watch.id and checked.last_checked_at is not None and checked.conditions
