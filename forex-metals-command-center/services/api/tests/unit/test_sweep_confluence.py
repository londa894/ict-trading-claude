"""Multi-session sweep confluence (spec section 5): London holds, New York sweeps both in one move."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from app.domain.candle import Candle
from app.domain.enums import (
    DataQuality,
    LiquiditySide,
    SessionInstanceState,
    SessionName,
    Timeframe,
)
from app.services.sessions.models import SessionConfig, SessionInstance
from app.services.sessions.sweep_confluence import detect_sweep_confluence

CFG = SessionConfig.from_spec()
DAY = date(2024, 1, 10)


def _dt(h, m=0):
    return datetime(2024, 1, 10, h, m, tzinfo=UTC)


def _session(session, start, end, high, low, *, state=SessionInstanceState.COMPLETE, known=True):
    return SessionInstance(
        id=f"{session.value}:{DAY.isoformat()}",
        session=session,
        trading_day=DAY,
        start=start,
        end=end,
        state=state,
        high=high,
        low=low,
        midpoint=None if high is None or low is None else (high + low) / 2,
        range=None if high is None or low is None else high - low,
        high_time=start,
        low_time=start,
        candle_count=16,
        expected_count=16,
        known_at=end if known else None,
        asian_range_state=None,
        asian_range_ratio=None,
    )


def _m15(open_time, o, h, low, c):
    return Candle(
        symbol="XAUUSD",
        timeframe=Timeframe.M15,
        open_time=open_time,
        close_time=open_time + timedelta(minutes=15),
        open=o,
        high=h,
        low=low,
        close=c,
        volume=1.0,
        source="t",
        is_closed=True,
        data_quality=DataQuality.CURRENT,
    )


def _instances(london_high, london_low):
    asia = _session(SessionName.ASIA, _dt(0), _dt(4), high=110, low=100)
    london = _session(SessionName.LONDON, _dt(7), _dt(11), high=london_high, low=london_low)
    ny = _session(
        SessionName.NY_AM,
        _dt(13),
        _dt(17),
        high=112,
        low=98,
        known=False,
        state=SessionInstanceState.COMPLETE,
    )
    return [asia, london, ny]


def test_ny_sweeps_both_lows_in_one_move_after_london_holds():
    # London low 102 > Asia low 100 (held); one NY candle spans 103 -> 99 (both lows swept).
    instances = _instances(london_high=108, london_low=102)
    candles = [_m15(_dt(14), 102, 103, 99, 100)]
    out = detect_sweep_confluence(candles, instances, _dt(17), CFG)
    assert len(out) == 1
    conf = out[0]
    assert conf.side is LiquiditySide.SSL
    assert (conf.asia_level, conf.london_level) == (100, 102)
    assert conf.session is SessionName.NY_AM and conf.sweep_extreme == 99


def test_ny_sweeps_both_highs_mirror():
    # London high 108 < Asia high 110 (held); one NY candle spans 107 -> 111 (both highs swept).
    instances = _instances(london_high=108, london_low=101)
    candles = [_m15(_dt(14), 108, 111, 107, 110)]
    out = detect_sweep_confluence(candles, instances, _dt(17), CFG)
    assert len(out) == 1 and out[0].side is LiquiditySide.BSL
    assert (out[0].asia_level, out[0].london_level) == (110, 108)


def test_no_confluence_when_london_already_took_asia_low():
    # London low 99 <= Asia low 100: London did NOT hold, so the pattern does not apply.
    instances = _instances(london_high=108, london_low=99)
    candles = [_m15(_dt(14), 102, 103, 97, 100)]
    assert detect_sweep_confluence(candles, instances, _dt(17), CFG) == []


def test_dedupes_to_first_ny_session_per_side():
    # NY_AM sweeps both highs; NY_PM (price still elevated) would re-satisfy it -> only NY_AM counts.
    asia = _session(SessionName.ASIA, _dt(0), _dt(4), high=110, low=100)
    london = _session(SessionName.LONDON, _dt(7), _dt(11), high=108, low=101)
    ny_am = _session(SessionName.NY_AM, _dt(13), _dt(15), high=112, low=107, known=False)
    ny_pm = _session(SessionName.NY_PM, _dt(15), _dt(17), high=113, low=110, known=False)
    candles = [_m15(_dt(14), 108, 111, 107, 110), _m15(_dt(16), 110, 113, 110, 112)]
    out = detect_sweep_confluence(candles, [asia, london, ny_am, ny_pm], _dt(17), CFG)
    assert len(out) == 1 and out[0].session is SessionName.NY_AM


def test_no_confluence_when_ny_does_not_reach_both_in_one_candle():
    # London holds (102 > 100) but the NY candle only dips to 101 — Asia's low is untouched.
    instances = _instances(london_high=108, london_low=102)
    candles = [_m15(_dt(14), 103, 104, 101, 102)]
    assert detect_sweep_confluence(candles, instances, _dt(17), CFG) == []
