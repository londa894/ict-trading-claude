from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.exc import StatementError
from sqlalchemy.orm import Session

from alembic import command
from app.db.models import Base, CandleRow, DataQualityEventRow
from app.db.repository import load_candles, sync_instruments, upsert_candles
from app.domain.enums import AssetClass, Timeframe
from app.domain.instrument import instrument_registry
from app.services.candles.normalize import build_series
from tests.helpers import TUE_10_UTC, after_last, bar, consecutive_bars

API_DIR = Path(__file__).resolve().parents[2]
NOW = datetime(2024, 1, 10, tzinfo=UTC)


@pytest.fixture
def session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        sync_instruments(s, list(instrument_registry().values()))
        yield s


def candles(raw, now=None):
    return build_series(
        raw,
        symbol="XAUUSD",
        timeframe=Timeframe.M5,
        source="t",
        asset_class=AssetClass.METAL,
        now=now or after_last(raw),
    ).candles


def test_insert_then_unchanged_and_utc_roundtrip(session):
    cs = candles(consecutive_bars(TUE_10_UTC, 5))
    assert upsert_candles(session, cs, NOW).inserted == 5
    assert upsert_candles(session, cs, NOW).unchanged == 5
    rows = load_candles(session, "XAUUSD", "M5", "t")
    assert [r.open_time for r in rows] == [c.open_time for c in cs]
    assert all(r.open_time.tzinfo is not None and r.open_time.utcoffset() == timedelta(0) for r in rows)


def test_forming_candle_is_updated(session):
    raw = consecutive_bars(TUE_10_UTC, 3)
    forming = candles(raw, now=raw[-1].open_time + timedelta(minutes=1))
    assert forming[-1].is_closed is False
    upsert_candles(session, forming, NOW)
    raw[-1] = bar(raw[-1].open_time, raw[-1].open, raw[-1].high + 2, raw[-1].low, raw[-1].close + 1)
    final = candles(raw)
    report = upsert_candles(session, final, NOW)
    assert report.updated == 1 and report.unchanged == 2
    last = load_candles(session, "XAUUSD", "M5", "t")[-1]
    assert last.is_closed is True and last.high == raw[-1].high


def test_closed_history_never_silently_rewritten(session):
    raw = consecutive_bars(TUE_10_UTC, 3)
    upsert_candles(session, candles(raw), NOW)
    original_high = raw[1].high
    raw[1] = bar(raw[1].open_time, raw[1].open, raw[1].high + 5, raw[1].low, raw[1].close)
    report = upsert_candles(session, candles(raw), NOW)
    assert report.conflicts == 1
    assert load_candles(session, "XAUUSD", "M5", "t")[1].high == original_high
    event = session.scalars(select(DataQualityEventRow)).one()
    assert event.code == "DUPLICATE_CONFLICT" and event.severity == "ERROR"


def test_naive_datetime_cannot_be_persisted(session):
    session.add(
        CandleRow(
            symbol="XAUUSD",
            timeframe="M5",
            open_time=datetime(2024, 1, 9, 10, 0),  # noqa: DTZ001
            close_time=datetime(2024, 1, 9, 10, 5, tzinfo=UTC),
            open=1,
            high=1,
            low=1,
            close=1,
            volume=None,
            source="t",
            is_closed=True,
            data_quality="CURRENT",
            ingested_at=NOW,
        )
    )
    with pytest.raises(StatementError):
        session.flush()


def test_alembic_migration_matches_models(tmp_path):
    url = f"sqlite+pysqlite:///{(tmp_path / 'm.db').as_posix()}"
    cfg = Config(str(API_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")

    insp = inspect(create_engine(url))
    tables = set(insp.get_table_names()) - {"alembic_version"}
    assert tables == set(Base.metadata.tables)
    for name, table in Base.metadata.tables.items():
        assert {c["name"] for c in insp.get_columns(name)} == {c.name for c in table.columns}
    uniques = {u["name"] for u in insp.get_unique_constraints("candles")}
    assert "uq_candle_identity" in uniques

    command.downgrade(cfg, "base")
    assert set(inspect(create_engine(url)).get_table_names()) <= {"alembic_version"}
