"""Paper simulation engine (Phase 16): fills, exits, gaps, ambiguity, costs, no lookahead."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.domain.candle import Candle
from app.domain.enums import (
    DataQuality,
    Direction,
    ExitReason,
    PaperEntryType,
    PaperEventType,
    PaperSource,
    PaperStatus,
    Timeframe,
)
from app.services.paper import models as paper_models
from app.services.paper.engine import SimParams, SimState, manual_close_price, simulate, step
from app.services.paper.models import AssumedCosts, CreatePaperSimRequest, PaperConfig

T0 = datetime(2024, 4, 18, 15, 0, tzinfo=UTC)
M5 = timedelta(minutes=5)
FREE = AssumedCosts(spread=0.0, slippage=0.0, commission=0.0)
COSTS = AssumedCosts(spread=0.4, slippage=0.1, commission=0.0)
E = PaperEventType


def bar(i: int, o: float, h: float, lo: float, c: float, closed: bool = True) -> Candle:
    t = T0 + M5 * i
    return Candle(
        symbol="XAUUSD",
        timeframe=Timeframe.M5,
        open_time=t,
        close_time=t + M5,
        open=o,
        high=h,
        low=lo,
        close=c,
        volume=None,
        source="t",
        is_closed=closed,
        data_quality=DataQuality.CURRENT,
    )


def params(
    direction=Direction.BULLISH,
    entry=PaperEntryType.MARKET,
    limit=None,
    stop=1990.0,
    target=2020.0,
    costs=FREE,
    created=T0,
    expiry=3,
):
    return SimParams(direction, entry, limit, stop, target, created, costs, expiry)


def types(events):
    return [e.type for e in events]


# --- config and request ------------------------------------------------------------


def test_config_and_costs_are_assumptions_per_symbol(monkeypatch):
    cfg = PaperConfig.from_spec()
    assert cfg.timeframe is Timeframe.M5 and cfg.costs["XAUUSD"].spread == 0.30 and len(cfg.costs) == 9
    real = paper_models.load_spec

    def patched(name):
        data = json.loads(json.dumps(real(name)))
        data["pendingExpiryBars"] = 0
        return data

    monkeypatch.setattr(paper_models, "load_spec", patched)
    with pytest.raises(ValueError):
        PaperConfig.from_spec()


def test_request_shapes():
    ok = CreatePaperSimRequest(symbol="XAUUSD", direction=Direction.BULLISH, stop=1990, target=2020)
    assert ok.entry_type is PaperEntryType.MARKET
    with pytest.raises(ValidationError):
        CreatePaperSimRequest(symbol="XAUUSD", direction=Direction.BULLISH, stop=1990)  # no target
    with pytest.raises(ValidationError):
        CreatePaperSimRequest(
            symbol="XAUUSD",
            direction=Direction.BULLISH,
            stop=1990,
            target=2020,
            entry_type=PaperEntryType.LIMIT,
        )
    with pytest.raises(ValidationError):
        CreatePaperSimRequest(symbol="XAUUSD", source=PaperSource.ENGINE_PLAN, stop=1990)
    assert CreatePaperSimRequest(symbol="XAUUSD", source=PaperSource.ENGINE_PLAN).direction is None


# --- fills ------------------------------------------------------------


def test_market_fill_pays_spread_and_slippage_on_the_first_bar_after_creation():
    p = params(costs=COSTS, created=T0 + timedelta(minutes=1))
    state, events = simulate(p, SimState(), [bar(0, 2000, 2001, 1999, 2000), bar(1, 2002, 2003, 2001, 2002)])
    assert state.status is PaperStatus.OPEN and state.filled_at == T0 + M5  # bar 0 opened before creation
    assert state.fill_price == pytest.approx(2002 + 0.2 + 0.1) and types(events) == [E.FILLED]
    short = params(Direction.BEARISH, stop=2010, target=1980, costs=COSTS)
    s_state, _ = simulate(short, SimState(), [bar(0, 2000, 2001, 1999, 2000)])
    assert s_state.fill_price == pytest.approx(2000 - 0.2 - 0.1)


def test_limit_fill_at_the_limit_or_a_better_gap_open_and_expiry():
    p = params(entry=PaperEntryType.LIMIT, limit=1995.0)
    state, _ = simulate(p, SimState(), [bar(0, 2000, 2001, 1994, 1996)])
    assert state.status is PaperStatus.OPEN and state.fill_price == 1995.0
    gap, _ = simulate(p, SimState(), [bar(0, 1993, 1994, 1992, 1993)])
    assert gap.fill_price == 1993.0  # opened below the buy limit: better fill
    spread = params(
        entry=PaperEntryType.LIMIT, limit=1995.0, costs=AssumedCosts(spread=2.0, slippage=0, commission=0)
    )
    untouched, _ = simulate(spread, SimState(), [bar(0, 2000, 2001, 1995.5, 1996)])  # ask low 1996.5
    assert untouched.status is PaperStatus.PENDING and untouched.pending_bars == 1
    bars = [bar(i, 2000, 2001, 1999, 2000) for i in range(5)]
    expired, ev = simulate(p, SimState(), bars)
    assert (
        expired.status is PaperStatus.EXPIRED
        and types(ev) == [E.EXPIRED]
        and expired.processed_through == T0 + M5 * 2
    )


# --- exits ------------------------------------------------------------


def test_target_and_stop_with_costs():
    p = params(costs=COSTS)
    state, events = simulate(p, SimState(), [bar(0, 2000, 2005, 1998, 2004), bar(1, 2004, 2021, 2003, 2019)])
    assert (
        types(events) == [E.FILLED, E.TARGET_HIT]
        and state.exit_price == 2020.0
        and state.exit_reason is ExitReason.TARGET
    )
    assert state.exited_at == T0 + M5 * 2
    stopped, ev = simulate(p, SimState(), [bar(0, 2000, 2001, 1989, 1990)])
    assert types(ev) == [E.FILLED, E.STOP_HIT] and stopped.exit_price == pytest.approx(1990 - 0.1)


def test_target_requires_the_bid_to_reach_it():
    p = params(costs=AssumedCosts(spread=2.0, slippage=0, commission=0))
    state, _ = simulate(p, SimState(), [bar(0, 2000, 2020.5, 1999, 2010)])  # bid high 2019.5
    assert state.status is PaperStatus.OPEN


def test_same_bar_stop_and_target_assumes_the_stop():
    state, events = simulate(params(), SimState(), [bar(0, 2000, 2025, 1985, 2000)])
    assert types(events) == [E.FILLED, E.STOP_HIT] and events[-1].ambiguous and state.exit_price == 1990.0


def test_limit_fill_bar_only_checks_the_stop():
    p = params(entry=PaperEntryType.LIMIT, limit=1995.0)
    target_too, _ = simulate(p, SimState(), [bar(0, 2000, 2025, 1994, 2010)])
    assert target_too.status is PaperStatus.OPEN  # target not credited on the fill bar
    _, ev = simulate(p, SimState(), [bar(0, 2000, 2001, 1988, 1992)])
    assert types(ev) == [E.FILLED, E.STOP_HIT] and ev[-1].ambiguous


def test_gaps_through_stop_and_target_fill_at_the_open():
    p = params(costs=AssumedCosts(spread=0, slippage=0.5, commission=0))
    open_state, _ = simulate(p, SimState(), [bar(0, 2000, 2001, 1999, 2000)])
    gap_down, ev = simulate(p, open_state, [bar(1, 1980, 1985, 1975, 1982)])
    assert (
        ev[-1].type is E.STOP_HIT
        and gap_down.exit_price == pytest.approx(1979.5)
        and "gapped" in ev[-1].detail
    )
    gap_up, ev = simulate(p, open_state, [bar(1, 2030, 2035, 2028, 2031)])
    assert ev[-1].type is E.TARGET_HIT and gap_up.exit_price == 2030.0


def test_short_side_exits_use_the_ask():
    p = params(
        Direction.BEARISH, stop=2010, target=1980, costs=AssumedCosts(spread=2.0, slippage=0, commission=0)
    )
    state, events = simulate(p, SimState(), [bar(0, 2000, 2009.5, 1995, 1998)])  # ask high 2010.5 -> stopped
    assert types(events) == [E.FILLED, E.STOP_HIT] and state.exit_price == 2010.0
    ok, _ = simulate(p, SimState(), [bar(0, 2000, 2008.5, 1995, 1998)])
    assert (
        ok.status is PaperStatus.OPEN
        and ok.best == pytest.approx(1996.0)
        and ok.worst == pytest.approx(2009.5)
    )


# --- sequencing ------------------------------------------------------------


def test_no_lookahead_open_or_processed_bars_and_idempotence():
    p = params()
    assert step(p, SimState(), bar(0, 2000, 2030, 1985, 2000, closed=False)) == (SimState(), [])
    earlier = params(created=T0 + M5)
    assert simulate(earlier, SimState(), [bar(0, 2000, 2030, 1980, 2000)]) == (SimState(), [])
    bars = [bar(0, 2000, 2005, 1998, 2004), bar(1, 2004, 2008, 2003, 2006)]
    once, ev = simulate(p, SimState(), bars)
    again, ev2 = simulate(p, once, bars)  # replaying processed bars changes nothing
    assert again == once and ev2 == [] and len(ev) == 1
    prefix, _ = simulate(p, SimState(), bars[:1])
    continued, _ = simulate(p, prefix, bars[1:])
    assert continued == once  # chunked advancing equals one pass


def test_tracks_extremes_and_manual_close_price():
    p = params(costs=COSTS)
    state, _ = simulate(p, SimState(), [bar(0, 2000, 2012, 1995, 2010), bar(1, 2010, 2015, 2004, 2008)])
    assert state.best == pytest.approx(2015 - 0.2) and state.worst == pytest.approx(1995 - 0.2)
    assert manual_close_price(p, bar(1, 2010, 2015, 2004, 2008)) == pytest.approx(2008 - 0.2 - 0.1)
