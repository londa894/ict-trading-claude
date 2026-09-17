"""Watchlist & scanner (Phase 10): ranking, isolation, caching, filters, non-validated-market blocker."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.domain.enums import (
    AssetClass,
    Blocker,
    DataQuality,
    DecisionConfidence,
    MarketStatus,
    SetupState,
    Timeframe,
    Verdict,
)
from app.domain.instrument import get_instrument
from app.main import create_app
from app.providers.fixture import SERIES_END, SyntheticFixtureProvider
from app.services.events.bus import InMemoryEventBus
from app.services.market_state.gate import GateInput, evaluate
from app.services.market_state.service import MarketStateService
from app.services.scanner.models import MarketRow, ScannerConfig, ScanResponse, ScanRow
from app.services.scanner.service import ScannerService, default_symbols, rank_key

MID_FIXTURE = SERIES_END - timedelta(hours=30, minutes=-1)
CONTRACT = Path(__file__).resolve().parents[4] / "packages" / "shared-types" / "contract" / "api_fields.json"
CFG = ScannerConfig.from_spec()


class NonSyntheticStub(SyntheticFixtureProvider):
    name = "stub-real"
    is_synthetic = False
    source = "stub-real"


def row(symbol: str, **over) -> ScanRow:
    inst = get_instrument(symbol)
    assert inst is not None
    base = dict(
        rank=0,
        symbol=symbol,
        asset_class=inst.asset_class,
        priority=inst.priority,
        deeply_validated=inst.deeply_validated,
        market_status=MarketStatus.OPEN,
        verdict=Verdict.WAIT,
        data_quality=DataQuality.CURRENT,
        htf_bias="BULLISH",
        setup_state="WATCH",
        setup_type=None,
        setup_progress=CFG.progress("WATCH"),
        setup_score=24.0,
        setup_grade="D",
        decision_confidence=DecisionConfidence.LOW,
        risk_status="NOT_CONFIGURED",
        primary_dol=None,
        blockers=[Blocker.ANALYSIS_GATES_NOT_IMPLEMENTED],
        next_required_event=None,
        latest_closed_open_time=None,
        evaluated_at=MID_FIXTURE,
        cache_age_seconds=0.0,
        error=None,
    )
    base.update(over)
    return ScanRow(**base)


def test_setup_progress_order():
    assert CFG.progress("NO_SETUP") == CFG.progress("NOT_EVALUATED") == CFG.progress("ENTRY_MISSED") == 0
    assert CFG.progress("DISCOVERED") == 1 and CFG.progress("BLOCKED") == len(CFG.setup_progress)
    assert CFG.progress("WAITING_FOR_CONFIRMATION") > CFG.progress("SETUP_ARMED") > CFG.progress("WATCH")
    assert all(s not in CFG.setup_progress for s in (SetupState.LONG_READY, SetupState.SHORT_READY))


def test_ranking_rules_are_deterministic():
    rows = [
        row("EURUSD", setup_state="BLOCKED", setup_progress=11, setup_score=90.0),  # not validated
        row("XAUUSD", verdict=Verdict.UNAVAILABLE, setup_progress=0, setup_score=None),
        row("XAGUSD", setup_state="WATCH", setup_score=30.0),
        row("XAUUSD", setup_state="SETUP_ARMED", setup_progress=6, setup_score=10.0),
        row("GBPUSD", setup_score=None, setup_progress=0, setup_state="NO_SETUP"),
        row("AUDUSD", setup_score=24.0, blockers=[]),
        row("NZDUSD", setup_score=24.0),
    ]
    ordered = [(r.symbol, r.verdict.value) for r in sorted(rows, key=rank_key)]
    assert ordered == [
        ("XAUUSD", "WAIT"),  # validated first, even with a lower score
        ("EURUSD", "WAIT"),  # furthest progress among non-validated
        ("XAGUSD", "WAIT"),  # score 30
        ("AUDUSD", "WAIT"),  # score 24, no blockers
        ("NZDUSD", "WAIT"),  # score 24, one blocker
        ("GBPUSD", "WAIT"),  # no setup
        ("XAUUSD", "UNAVAILABLE"),  # unusable data last
    ]


def test_non_validated_markets_get_a_wait_class_blocker():
    def gate(symbol):
        return evaluate(
            GateInput(
                symbol=symbol,
                now=datetime(2024, 1, 9, 15, tzinfo=UTC),
                instrument=get_instrument(symbol),
                provider_available=True,
                is_synthetic=False,
                data_quality=DataQuality.CURRENT,
                market_status=MarketStatus.OPEN,
            )
        )

    eur, gold = gate("EURUSD"), gate("XAUUSD")
    assert Blocker.MARKET_NOT_VALIDATED in eur.blockers and eur.verdict is Verdict.WAIT
    assert Blocker.MARKET_NOT_VALIDATED not in gold.blockers


def services(clock=lambda: MID_FIXTURE, provider=None):
    app = create_app(settings=Settings(_env_file=None), provider=provider or NonSyntheticStub(), clock=clock)  # type: ignore[call-arg]
    return app


@pytest.fixture(scope="module")
def scan_client():
    return TestClient(services())


def test_scan_endpoint_ranks_real_decisions(scan_client):
    body = scan_client.get("/api/v1/scanner", params={"symbols": "eurusd,XAUUSD,USDJPY,XAUUSD"}).json()
    assert body["requestedSymbols"] == ["EURUSD", "XAUUSD", "USDJPY"]
    assert body["authority"] == "NOT_AUTHORIZED" and body["verdictAuthority"] == "FAIL_SAFE_ONLY"
    rows = body["rows"]
    assert [r["rank"] for r in rows] == [1, 2, 3] and rows[0]["symbol"] == "XAUUSD"
    for r in rows:
        assert r["verdict"] in ("WAIT", "UNAVAILABLE") and r["error"] is None
        assert ("MARKET_NOT_VALIDATED" in r["blockers"]) is (not r["deeplyValidated"])
        decision = scan_client.get(f"/api/v1/market-state/{r['symbol']}").json()["decision"]
        # one decision object everywhere: the row is the symbol's Master Decision
        for key in ("verdict", "setupState", "setupScore", "setupGrade", "riskStatus", "blockers", "htfBias"):
            assert r[key] == decision[key], (r["symbol"], key)
    text = json.dumps(body)
    assert "LONG_READY" not in text and "SHORT_READY" not in text


def test_default_scan_covers_the_catalog_and_filters(scan_client):
    body = scan_client.get("/api/v1/scanner").json()
    assert body["requestedSymbols"] == default_symbols() and len(body["rows"]) == 9
    assert body["rows"][0]["symbol"] == "XAUUSD"
    assert all(r["cacheAgeSeconds"] >= 0 for r in body["rows"])
    scored = scan_client.get("/api/v1/scanner", params={"minScore": 20}).json()["rows"]
    assert all(r["setupScore"] is not None and r["setupScore"] >= 20 for r in scored)
    assert [r["rank"] for r in scored] == list(range(1, len(scored) + 1))
    setups = scan_client.get("/api/v1/scanner", params={"onlySetups": "true"}).json()["rows"]
    assert all(r["setupProgress"] > 0 for r in setups)


@pytest.mark.parametrize(
    ("params", "code"),
    [
        ({"symbols": "BTCUSD"}, 404),
        ({"symbols": "XAU-USD"}, 422),
        ({"minScore": 101}, 422),
    ],
)
def test_scan_rejects_bad_requests(scan_client, params, code):
    assert scan_client.get("/api/v1/scanner", params=params).status_code == code


def test_duplicate_symbols_collapse(scan_client):
    body = scan_client.get("/api/v1/scanner", params={"symbols": "XAUUSD,xauusd,XAUUSD"}).json()
    assert body["requestedSymbols"] == ["XAUUSD"] and len(body["rows"]) == 1


async def test_max_symbols_is_enforced():
    market = MarketStateService(NonSyntheticStub(), InMemoryEventBus(), Timeframe.M5, lambda: MID_FIXTURE)
    small = ScannerConfig(cache_seconds=60, max_symbols=2, setup_progress=CFG.setup_progress)
    svc = ScannerService(market, cfg=small)
    with pytest.raises(ValueError, match="at most 2"):
        svc.resolve_symbols(["XAUUSD", "XAGUSD", "EURUSD"])


async def test_one_failing_symbol_is_isolated_and_cache_is_respected():
    provider = NonSyntheticStub()
    market = MarketStateService(provider, InMemoryEventBus(), Timeframe.M5, lambda: MID_FIXTURE)
    real = market.evaluate
    calls: list[str] = []

    async def flaky(symbol):
        calls.append(symbol)
        if symbol == "XAGUSD":
            raise RuntimeError("provider exploded for silver")
        if symbol == "EURUSD":
            response = await real("GBPUSD")  # a decision for the wrong symbol must not leak
            return response
        return await real(symbol)

    market.evaluate = flaky  # type: ignore[method-assign]
    ticks = [0.0]
    svc = ScannerService(market, lambda: MID_FIXTURE, monotonic=lambda: ticks[0])
    first = await svc.scan(["XAUUSD", "XAGUSD", "EURUSD"])
    by = {r.symbol: r for r in first.rows}
    assert by["XAGUSD"].verdict is Verdict.UNAVAILABLE and by["XAGUSD"].error == "scan failed"
    assert by["EURUSD"].blockers == [Blocker.SYSTEM_INTEGRITY_FAILURE] and by["EURUSD"].error == "scan failed"
    assert by["XAUUSD"].error is None and by["XAUUSD"].verdict is Verdict.WAIT
    assert [r.symbol for r in first.rows][-2:] == ["XAGUSD", "EURUSD"]  # unusable rows last, by priority

    ticks[0] = 30.0
    again = await svc.scan(["XAUUSD"])
    assert len(calls) == 3 and again.rows[0].cache_age_seconds == 30.0  # served from cache
    ticks[0] = 61.0
    await svc.scan(["XAUUSD"])
    assert len(calls) == 4  # expired -> re-evaluated


def test_markets_endpoint(scan_client):
    rows = scan_client.get("/api/v1/markets").json()
    assert [r["symbol"] for r in rows] == default_symbols()
    gold = rows[0]
    assert gold["deeplyValidated"] is True and gold["assetClass"] == AssetClass.METAL.value
    assert gold["positionSizeStatus"] == "POSITION_SIZE_UNVERIFIED"
    assert {r["marketStatus"] for r in rows} <= {s.value for s in MarketStatus}
    assert sum(r["deeplyValidated"] for r in rows) == 1


def test_synthetic_scan_is_unavailable_everywhere():
    body = (
        TestClient(
            services(provider=SyntheticFixtureProvider(), clock=lambda: SERIES_END + timedelta(seconds=30))
        )
        .get("/api/v1/scanner", params={"symbols": "XAUUSD,EURUSD"})
        .json()
    )
    assert {r["verdict"] for r in body["rows"]} == {"UNAVAILABLE"}
    assert all(r["setupProgress"] == 0 and r["setupScore"] is None for r in body["rows"])


@pytest.mark.parametrize("model", [MarketRow, ScanRow, ScanResponse])
def test_scanner_wire_fields_match_shared_contract(model):
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    assert sorted(f.alias or n for n, f in model.model_fields.items()) == sorted(contract[model.__name__])
