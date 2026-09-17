import re
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.config import Settings
from app.main import create_app
from app.providers.base import ProviderUnavailableError
from app.providers.fixture import SERIES_END, SyntheticFixtureProvider
from app.providers.unconfigured import UnconfiguredProvider
from tests.helpers import TUE_10_UTC, StaticProvider, after_last, consecutive_bars

SECRET = "sk-test-provider-secret-DO-NOT-LEAK"  # noqa: S105 - fake value used to prove non-leakage


def client(provider=None, clock=None, **settings):
    s = Settings(_env_file=None, **settings)  # type: ignore[call-arg]
    return TestClient(create_app(settings=s, provider=provider or UnconfiguredProvider(), clock=clock))


def test_health():
    r = client().get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["strategyVersion"] == "0.19.0-phase19"


def test_system_status_declares_broker_free_and_never_leaks_secrets():
    c = client(market_data_api_key=SecretStr(SECRET), database_url="postgresql+psycopg://u:dbpass@h/db")
    r = c.get("/api/v1/system/status")
    assert r.status_code == 200
    body = r.json()
    assert body["brokerConnection"] == "NONE"
    assert body["orderExecution"] == "NONE"
    assert body["marketDataSource"] == "INDEPENDENT_PROVIDER"
    assert "RENDER_ONLY" in body["chartRenderer"]
    assert body["verdictAuthority"] == "FAIL_SAFE_ONLY"
    assert body["primaryMarket"] == "XAUUSD"
    assert body["provider"]["credentialsConfigured"] is True
    for path in (
        "/api/v1/system/status",
        "/api/v1/providers/health",
        "/api/v1/market-state/XAUUSD",
        "/openapi.json",
    ):
        text = c.get(path).text
        assert SECRET not in text and "dbpass" not in text


def test_instruments_position_size_unverified():
    body = client().get("/api/v1/instruments").json()
    assert body[0]["instrument"]["symbol"] == "XAUUSD"
    assert all(i["positionSizeStatus"] == "POSITION_SIZE_UNVERIFIED" for i in body)
    assert all(i["instrument"]["spec"] is None for i in body)


def test_unconfigured_provider_market_state_unavailable():
    c = client()
    assert c.get("/api/v1/providers/health").json()["status"] == "DOWN"
    body = c.get("/api/v1/market-state/XAUUSD").json()
    d = body["decision"]
    assert d["verdict"] == "UNAVAILABLE"
    assert d["dataQuality"] == "DISCONNECTED"
    assert "PROVIDER_UNAVAILABLE" in d["blockers"]
    assert body["data"]["positionSizeStatus"] == "POSITION_SIZE_UNVERIFIED"


def test_fixture_provider_is_blocked_even_when_fresh():
    # Clock pinned just after the fixture window: data is CURRENT, but synthetic -> UNAVAILABLE.
    c = client(SyntheticFixtureProvider(), clock=lambda: SERIES_END + timedelta(seconds=30))
    body = c.get("/api/v1/market-state/xauusd").json()
    assert body["data"]["isSynthetic"] is True
    assert body["data"]["quality"] == "CURRENT"
    assert body["decision"]["verdict"] == "UNAVAILABLE"
    assert "DATA_SYNTHETIC" in body["decision"]["blockers"]


def test_clock_before_provider_bars_is_invalid():
    # Bars "from the future" relative to our clock mean a timezone/clock fault: never trusted.
    # StaticProvider ignores `end`, so this proves validation does not rely on providers honouring it.
    raw = consecutive_bars(TUE_10_UTC, 60)
    c = client(StaticProvider(raw), clock=lambda: raw[30].open_time)
    body = c.get("/api/v1/market-state/XAUUSD").json()
    assert body["data"]["quality"] == "INVALID"
    assert body["decision"]["verdict"] == "UNAVAILABLE"


def test_fixture_provider_real_clock_is_stale():
    body = client(SyntheticFixtureProvider()).get("/api/v1/market-state/XAUUSD").json()
    assert body["decision"]["verdict"] == "UNAVAILABLE"
    assert "DATA_STALE" in body["decision"]["blockers"]


def test_clean_real_provider_data_is_wait_not_directional():
    raw = consecutive_bars(TUE_10_UTC, 60)
    c = client(StaticProvider(raw), clock=lambda: after_last(raw))
    body = c.get("/api/v1/market-state/XAUUSD").json()
    d = body["decision"]
    assert d["verdict"] == "WAIT"
    assert d["dataQuality"] == "CURRENT"
    assert "ANALYSIS_GATES_NOT_IMPLEMENTED" in d["blockers"]
    assert "INSTRUMENT_SPEC_MISSING" in d["blockers"]
    assert d["direction"] is None and d["stop"] is None and d["tp1"] is None
    assert body["data"]["candleCount"] == 60


def test_gap_in_real_data_is_reported():
    raw = consecutive_bars(TUE_10_UTC, 60)
    del raw[20:25]
    body = (
        client(StaticProvider(raw), clock=lambda: after_last(raw)).get("/api/v1/market-state/XAUUSD").json()
    )
    assert body["decision"]["verdict"] == "WAIT"
    assert "DATA_GAP" in body["decision"]["blockers"]
    assert any(i["code"] == "MISSING_BARS" for i in body["data"]["issues"])


@pytest.mark.parametrize("exc", [ProviderUnavailableError("down"), RuntimeError("boom")])
def test_provider_exceptions_fail_safe(exc):
    raw = consecutive_bars(TUE_10_UTC, 5)
    body = client(StaticProvider(raw, raise_on_bars=exc)).get("/api/v1/market-state/XAUUSD").json()
    assert body["decision"]["verdict"] == "UNAVAILABLE"
    assert "PROVIDER_UNAVAILABLE" in body["decision"]["blockers"]
    assert "boom" not in (body["data"]["providerError"] or "")


def test_unknown_and_malformed_symbols():
    c = client()
    body = c.get("/api/v1/market-state/BTCUSD").json()
    assert body["decision"]["verdict"] == "UNAVAILABLE"
    assert "UNKNOWN_SYMBOL" in body["decision"]["blockers"]
    assert c.get("/api/v1/market-state/XAU;DROP").status_code in (404, 422)


FORBIDDEN_ROUTE = re.compile(r"order|execut|broker|trade|position|account|withdraw|deposit|login", re.I)


def test_api_surface_is_read_only_and_has_no_execution_routes():
    spec = client().get("/openapi.json").json()
    for path, ops in spec["paths"].items():
        assert not FORBIDDEN_ROUTE.search(path), path
        if path == "/api/v1/risk/calculate":
            assert set(ops) == {"post"}  # the stateless what-if calculator: stores nothing
        elif path == "/api/v1/alerts/ready-watches":
            assert set(ops) == {"get", "post"}  # ALERT_ME_WHEN_READY watches (symbol + direction only)
        elif path == "/api/v1/alerts/ready-watches/{watch_id}":
            assert set(ops) == {"delete"}
        elif path == "/api/v1/assistant/ask":
            assert set(ops) == {"post"}  # a question in, an explanation out; nothing stored
        elif path == "/api/v1/journal/entries":
            assert set(ops) == {"get", "post"}  # the user's own manual records (nothing is placed)
        elif path == "/api/v1/journal/entries/{entry_id}":
            assert set(ops) == {"get", "delete"}  # read / user-controlled deletion; never an update
        elif path == "/api/v1/journal/entries/{entry_id}/outcomes":
            assert set(ops) == {"post"}  # append-only outcome revisions
        elif path == "/api/v1/paper/sims":
            assert set(ops) == {"get", "post"}  # broker-free simulations only (nothing is sent anywhere)
        elif path == "/api/v1/paper/sims/{sim_id}":
            assert set(ops) == {"get", "delete"}
        elif path in ("/api/v1/paper/sims/{sim_id}/close", "/api/v1/paper/sims/{sim_id}/cancel"):
            assert set(ops) == {"post"}  # simulated close / cancel of a paper sim
        elif path == "/api/v1/backtests":
            assert set(ops) == {"get", "post"}  # research replays over closed history
        elif path == "/api/v1/backtests/{run_id}":
            assert set(ops) == {"get", "delete"}
        elif path == "/api/v1/backtests/{run_id}/cancel":
            assert set(ops) == {"post"}
        elif path == "/api/v1/replay/sessions":
            assert set(ops) == {"get", "post"}  # education replays (in memory)
        elif path == "/api/v1/replay/sessions/{session_id}":
            assert set(ops) == {"get", "delete"}
        elif path.startswith("/api/v1/replay/sessions/{session_id}/"):
            assert set(ops) == {"post"}  # step / answer / end
        else:
            assert set(ops) == {"get"}, f"{path} exposes non-GET methods {set(ops)}"


def test_unhandled_error_returns_fail_safe_body():
    app = create_app(settings=Settings(_env_file=None), provider=UnconfiguredProvider())  # type: ignore[call-arg]

    @app.get("/boom")
    async def boom():
        raise RuntimeError("secret internals")

    r = TestClient(app, raise_server_exceptions=False).get("/boom")
    assert r.status_code == 500
    assert r.json()["verdict"] == "UNAVAILABLE"
    assert "secret internals" not in r.text
