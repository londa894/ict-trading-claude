from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.domain.enums import AssetClass, Timeframe
from app.main import create_app
from app.providers.base import ProviderUnavailableError
from app.providers.fixture import SERIES_END, SERIES_START, SyntheticFixtureProvider
from app.providers.unconfigured import UnconfiguredProvider
from app.services.candles.service import CHART_TIMEFRAMES, CandleService
from app.services.timeframes.core import bucket_start, is_aligned
from tests.helpers import TUE_10_UTC, StaticProvider, after_last, bar, consecutive_bars

AFTER_FIXTURE = SERIES_END + timedelta(seconds=30)


def client(provider, clock=None):
    return TestClient(create_app(settings=Settings(_env_file=None), provider=provider, clock=clock))  # type: ignore[call-arg]


def get(c, tf="M5", limit=None, symbol="XAUUSD"):
    params = {"timeframe": tf}
    if limit is not None:
        params["limit"] = limit
    return c.get(f"/api/v1/candles/{symbol}", params=params)


@pytest.fixture(scope="module")
def fixture_client():
    return client(SyntheticFixtureProvider(), clock=lambda: AFTER_FIXTURE)


@pytest.mark.parametrize("tf", [t.value for t in CHART_TIMEFRAMES])
def test_every_chart_timeframe_serves_aligned_ascending_candles(fixture_client, tf):
    r = get(fixture_client, tf, limit=1000)
    assert r.status_code == 200
    body = r.json()
    assert body["timeframe"] == tf
    assert body["isSynthetic"] is True
    assert body["quality"] == "CURRENT"
    assert body["strategyVersion"] == "0.19.0-phase19"
    candles = body["candles"]
    assert 0 < len(candles) <= 1000
    times = [datetime.fromisoformat(c["time"]) for c in candles]
    assert times == sorted(times) and len(set(times)) == len(times)
    assert all(is_aligned(t, Timeframe(tf)) for t in times)
    assert all(t < AFTER_FIXTURE for t in times)  # no lookahead
    for c in candles:
        assert c["low"] <= min(c["open"], c["close"]) <= max(c["open"], c["close"]) <= c["high"]


def test_derived_timeframes_declare_their_source(fixture_client):
    assert get(fixture_client, "H4").json()["sourceTimeframe"] == "H1"
    assert get(fixture_client, "D1").json()["sourceTimeframe"] == "H1"
    assert get(fixture_client, "M15").json()["sourceTimeframe"] == "M15"


def test_cross_timeframe_consistency_d1_and_h4_match_m5(fixture_client):
    m5 = get(fixture_client, "M5", limit=1000).json()["candles"]
    first_m5 = datetime.fromisoformat(m5[0]["time"])
    for tf in ("H4", "D1", "H1", "M15"):
        higher = get(fixture_client, tf, limit=1000).json()["candles"]
        checked = 0
        for h in higher:
            start = datetime.fromisoformat(h["time"])
            if start < bucket_start(first_m5, Timeframe(tf)) + Timeframe(tf).duration:
                continue  # skip a bucket that is only partly covered by the M5 window
            inside = [
                c for c in m5 if start <= datetime.fromisoformat(c["time"]) < start + Timeframe(tf).duration
            ]
            if not inside:
                continue
            assert h["high"] == max(c["high"] for c in inside), (tf, start)
            assert h["low"] == min(c["low"] for c in inside), (tf, start)
            assert h["open"] == inside[0]["open"] and h["close"] == inside[-1]["close"]
            checked += 1
        assert checked > 0, tf


def test_limit_returns_most_recent(fixture_client):
    full = get(fixture_client, "H1", limit=1000).json()["candles"]
    last10 = get(fixture_client, "H1", limit=10).json()["candles"]
    assert last10 == full[-10:]


def test_fixture_window_spans_dst_and_d1_buckets_stay_24h(fixture_client):
    d1 = get(fixture_client, "D1", limit=1000).json()["candles"]
    opens = [datetime.fromisoformat(c["time"]) for c in d1]
    assert opens[0] < datetime(2024, 3, 10, tzinfo=UTC) < opens[-1]
    assert {t.astimezone(__import__("zoneinfo").ZoneInfo("America/New_York")).hour for t in opens} == {17}
    assert all(t.weekday() != 5 for t in opens)  # no Saturday-starting trading days (Fri 17:00 NY)


def test_forming_candle_flagged_mid_window():
    now = datetime(2024, 3, 13, 14, 32, tzinfo=UTC)
    c = client(SyntheticFixtureProvider(end=now.replace(minute=30)), clock=lambda: now)
    for tf in ("D1", "H4"):
        body = get(c, tf).json()
        assert body["candles"][-1]["isClosed"] is False
        assert all(x["isClosed"] for x in body["candles"][:-1])
        assert not [i for i in body["issues"] if i["code"] == "INCOMPLETE_BUCKET"]


def test_validation_errors_withhold_candles():
    raw = consecutive_bars(TUE_10_UTC, 30)
    raw[10] = bar(raw[10].open_time, o=2030, h=2029, low=2028, c=2030)  # impossible OHLC
    body = get(client(StaticProvider(raw), clock=lambda: after_last(raw))).json()
    assert body["quality"] == "INVALID"
    assert body["candles"] == []
    assert any(i["code"] == "IMPOSSIBLE_OHLC" for i in body["issues"])


def test_stale_data_is_still_drawn_but_labelled():
    raw = consecutive_bars(TUE_10_UTC, 30)
    body = get(client(StaticProvider(raw), clock=lambda: after_last(raw) + timedelta(hours=2))).json()
    assert body["quality"] == "STALE"
    assert len(body["candles"]) == 30


@pytest.mark.parametrize(
    "provider",
    [
        UnconfiguredProvider(),
        StaticProvider([], healthy=False),
        StaticProvider([], raise_on_bars=ProviderUnavailableError("down")),
        StaticProvider([], raise_on_bars=RuntimeError("secret-internal")),
    ],
)
def test_provider_failures_are_disconnected_without_candles(provider):
    r = get(client(provider))
    assert r.status_code == 200
    body = r.json()
    assert body["quality"] == "DISCONNECTED"
    assert body["candles"] == []
    assert "secret-internal" not in r.text


def test_request_validation(fixture_client):
    assert get(fixture_client, "M1").status_code == 422
    assert get(fixture_client, "W1").status_code == 422
    assert get(fixture_client, "X9").status_code == 422
    assert get(fixture_client, "M5", limit=0).status_code == 422
    assert get(fixture_client, "M5", limit=1001).status_code == 422
    assert get(fixture_client, symbol="BTCUSD").status_code == 404


def test_chart_timeframe_never_changes_the_master_decision(fixture_client):
    decisions = set()
    for tf in ("M5", "H4", "D1"):
        get(fixture_client, tf)
        d = fixture_client.get("/api/v1/market-state/XAUUSD").json()["decision"]
        decisions.add((d["verdict"], d["dataQuality"], tuple(d["blockers"])))
    assert len(decisions) == 1


async def test_derived_series_requests_h1_from_provider():
    requested = []

    class Spy(StaticProvider):
        async def get_historical_bars(self, symbol, timeframe, start=None, end=None, limit=None):
            requested.append((timeframe, end, limit))
            return []

    svc = CandleService(Spy([]), clock=lambda: TUE_10_UTC)
    body = await svc.chart_series("XAUUSD", Timeframe.D1, 10)
    # H1 source, bounded window ending at "now": (10 + 1 edge bucket) * 24 + 25 warm-up bars.
    assert requested == [(Timeframe.H1, TUE_10_UTC, 11 * 24 + 25)]
    assert body.candles == [] and body.quality == "INVALID"


def test_provider_with_incompatible_signature_fails_safe():
    class Legacy(StaticProvider):
        async def get_historical_bars(self, symbol, timeframe, start=None, end=None):  # no `limit`
            return []

    body = get(client(Legacy([]))).json()
    assert body["quality"] == "DISCONNECTED" and body["candles"] == []


def test_fixture_series_bounds():
    p = SyntheticFixtureProvider()
    m5 = p.generate("XAUUSD", Timeframe.M5)
    assert m5[0].open_time == SERIES_START
    assert m5[-1].open_time + Timeframe.M5.duration == SERIES_END
    assert p.generate("XAUUSD", Timeframe.D1) == []
    assert AssetClass.METAL  # fixture uses metals hours for XAUUSD
