"""Historical-file provider: serves the validated research dataset (DATA_ROOT/cleaned) as market data.

Ported from V2. The dataset gate refuses to serve a symbol unless a validation manifest exists, the
dataset is approved, and every raw source recorded at validation time is still present and unchanged.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import polars as pl
import pytest

from app.domain.enums import AssetClass, ProviderHealthStatus, ResearchStatus, Timeframe
from app.providers.base import ProviderError, ProviderUnavailableError
from app.providers.history_file import HistoricalFileProvider
from app.services.data_quality.market_hours import is_market_open

T0 = datetime(2024, 3, 4, tzinfo=UTC)  # Monday; the window spans the 2024-03-10 US DST change


def m5_rows(start: datetime = T0, days: int = 14):
    """Deterministic M5 bars inside reference market hours."""
    from datetime import timedelta

    price, t, out = 2100.0, start, []
    while t < start + timedelta(days=days):
        if is_market_open(AssetClass.METAL, t):
            o = round(price, 2)
            c = round(o + 0.15, 2)
            out.append((t, o, round(c + 0.1, 2), round(o - 0.1, 2), c, 10.0))
            price = c
        t += timedelta(minutes=5)
    return out


def write_tf(root: Path, symbol: str, tf: str, rows) -> None:
    frame = pl.DataFrame(
        rows, schema=["open_time", "open", "high", "low", "close", "volume"], orient="row"
    ).with_columns(
        pl.col("open_time").dt.cast_time_unit("us"), pl.col("open_time").dt.year().alias("year")
    )
    frame.write_parquet(root / "cleaned" / symbol / tf, partition_by="year")


def make_data_root(tmp: Path, status: str = ResearchStatus.APPROVED_WITH_WARNINGS) -> Path:
    root = tmp / "data"
    rows = m5_rows()
    write_tf(root, "XAUUSD", "M5", rows)
    raw = root / "raw" / "XAUUSD" / "5M" / "XAU_5m_data.csv"
    raw.parent.mkdir(parents=True)
    raw.write_text("Date;Open;High;Low;Close;Volume\n", encoding="utf-8")
    manifest = root / "manifests" / "XAUUSD" / "dataset.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps(
            {"status": status, "rawFiles": [{"file": str(raw), "bytes": raw.stat().st_size}]}
        ),
        encoding="utf-8",
    )
    return root


def test_approved_dataset_serves_validated_bars(tmp_path):
    provider = HistoricalFileProvider(make_data_root(tmp_path))
    assert provider.refusal("XAUUSD") is None

    bars = await_sync(provider.get_historical_bars("XAUUSD", Timeframe.M5, limit=10))

    assert len(bars) == 10
    assert bars == sorted(bars, key=lambda b: b.open_time)
    assert all(b.symbol == "XAUUSD" and b.timeframe is Timeframe.M5 for b in bars)
    assert all(b.low <= b.open <= b.high and b.low <= b.close <= b.high for b in bars)


def test_not_approved_dataset_is_never_served(tmp_path):
    provider = HistoricalFileProvider(make_data_root(tmp_path, status=ResearchStatus.NOT_APPROVED))

    assert "NOT_APPROVED" in (provider.refusal("XAUUSD") or "")
    with pytest.raises(ProviderUnavailableError):
        await_sync(provider.get_historical_bars("XAUUSD", Timeframe.M5, limit=5))


def test_changed_raw_source_since_validation_is_refused(tmp_path):
    root = make_data_root(tmp_path)
    provider = HistoricalFileProvider(root)
    assert provider.refusal("XAUUSD") is None

    next((root / "raw").rglob("*.csv")).write_text("tampered\n" * 5, encoding="utf-8")

    assert "changed since validation" in (provider.refusal("XAUUSD") or "")
    assert await_sync(provider.health_check()).status is ProviderHealthStatus.DOWN


def test_windows_manifest_paths_resolve_on_this_host(tmp_path):
    """Manifests are written on Windows; the recorded absolute path will not exist here."""
    root = make_data_root(tmp_path)
    manifest = root / "manifests" / "XAUUSD" / "dataset.json"
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    recorded = root / "raw" / "XAUUSD" / "5M" / "XAU_5m_data.csv"
    payload["rawFiles"][0] = {
        "file": r"C:\Users\Londa\Documents\Data\raw\XAUUSD\5M\XAU_5m_data.csv",
        "bytes": recorded.stat().st_size,
    }
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    assert HistoricalFileProvider(root).refusal("XAUUSD") is None


def test_bulk_load_and_live_quotes_are_refused(tmp_path):
    provider = HistoricalFileProvider(make_data_root(tmp_path))

    with pytest.raises(ProviderError):
        await_sync(provider.get_historical_bars("XAUUSD", Timeframe.M5))  # no start, no limit
    with pytest.raises(ProviderUnavailableError):
        await_sync(provider.get_latest_quote("XAUUSD"))


def test_unconfigured_root_reports_down_with_a_reason():
    health = await_sync(HistoricalFileProvider("").health_check())
    assert health.status is ProviderHealthStatus.DOWN
    assert "DATA_ROOT" in health.message


def await_sync(coro):
    import asyncio

    return asyncio.run(coro)
