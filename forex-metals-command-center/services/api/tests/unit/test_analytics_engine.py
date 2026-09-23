"""Analytics V1 engine (Phase 17): stats, labels, drawdown, breakdown gating, DOL accuracy, process."""

import json
from datetime import UTC, datetime, timedelta

import pytest

from app.domain.enums import AnalyticsSource, Direction, ProcessClassification, RuleViolation, SampleSizeLabel
from app.services.analytics import models as analytics_models
from app.services.analytics.engine import (
    breakdown,
    dol_accuracy,
    downsample,
    drawdown,
    durations,
    group_stats,
    label_for,
    parse_dol,
    process_stats,
)
from app.services.analytics.models import AnalyticsConfig, EquityPoint, Sample

CFG = AnalyticsConfig.from_spec()
T0 = datetime(2024, 4, 1, 12, 0, tzinfo=UTC)
PC = ProcessClassification


def sample(
    i: int,
    r: float | None,
    *,
    symbol="XAUUSD",
    session="NEW_YORK",
    violations=(),
    direction=Direction.BULLISH,
    dol=None,
    mfe_price=None,
    duration=60.0,
    result=None,
):
    win = r is not None and r > 0.1
    cls = (
        (PC.BAD_PROCESS_WIN if violations else PC.VALID_WIN)
        if win
        else (PC.PROCESS_ERROR if violations else PC.VALID_LOSS)
    )
    return Sample(
        id=f"s{i:04d}",
        source=AnalyticsSource.JOURNAL,
        symbol=symbol,
        direction=direction,
        closed_at=T0 + timedelta(hours=i),
        r=r,
        win=win,
        breakeven=r is not None and abs(r) <= 0.1,
        result=result or ("FULL_WIN" if win else "FULL_LOSS"),
        classification=cls,
        violations=tuple(violations),
        duration_minutes=duration,
        mfe_r=None,
        mae_r=None,
        entry_efficiency=None,
        exit_efficiency=None,
        session=session,
        setup_type="LIQUIDITY_SWEEP_MSS",
        timeframe="M5",
        day_of_week="Monday",
        no_wick=None,
        liquidity_event=None,
        dol=dol,
        entry_price=2380.0,
        mfe_price=mfe_price,
        synthetic=False,
        strategy_version="0.20.0-phase20",
    )


@pytest.mark.parametrize(
    ("n", "label"),
    [
        (0, SampleSizeLabel.INSUFFICIENT),
        (29, SampleSizeLabel.INSUFFICIENT),
        (30, SampleSizeLabel.LIMITED),
        (99, SampleSizeLabel.LIMITED),
        (100, SampleSizeLabel.MODERATE),
        (299, SampleSizeLabel.MODERATE),
        (300, SampleSizeLabel.STRONGER_EVIDENCE),
    ],
)
def test_spec_sample_size_labels(n, label):
    assert label_for(n, CFG) is label


def test_config_requires_increasing_thresholds(monkeypatch):
    real = analytics_models.load_spec

    def patched(name):
        data = json.loads(json.dumps(real(name)))
        data["sampleSizeLabels"]["MODERATE"] = 10
        return data

    monkeypatch.setattr(analytics_models, "load_spec", patched)
    with pytest.raises(ValueError):
        AnalyticsConfig.from_spec()


def test_group_stats_win_rate_expectancy_and_profit_factor():
    rs = [2.0, -1.0, 3.0, -1.0, 0.05, None]
    g = group_stats("X", [sample(i, r) for i, r in enumerate(rs)], CFG)
    assert (g.count, g.wins, g.breakeven, g.losses, g.r_count) == (6, 2, 1, 3, 5)
    assert g.win_rate == pytest.approx(2 / 6, abs=1e-4) and g.label is SampleSizeLabel.INSUFFICIENT
    assert g.total_r == pytest.approx(3.05) and g.avg_r == pytest.approx(0.61)
    assert g.avg_win_r == 2.5 and g.avg_loss_r == pytest.approx(-0.65)
    assert g.expectancy_r == pytest.approx(g.avg_r, abs=1e-4)  # expectancy equals average R
    assert g.profit_factor == 2.525  # 5.05 / 2 (the +0.05 break-even counts as positive R)
    no_losses = group_stats("W", [sample(0, 1.0), sample(1, 2.0)], CFG)
    assert no_losses.profit_factor is None and no_losses.avg_loss_r is None
    empty = group_stats("E", [], CFG)
    assert empty.win_rate is None and empty.avg_r is None and empty.expectancy_r is None


def test_drawdown_peak_trough_and_recovery():
    rs = [1.0, 1.0, -1.0, -1.5, 0.5, 2.5, -0.5]
    dd, points = drawdown([sample(i, r) for i, r in enumerate(rs)])
    assert [p.cumulative_r for p in points] == [1.0, 2.0, 1.0, -0.5, 0.0, 2.5, 2.0]
    assert (
        dd.max_drawdown_r == 2.5
        and dd.peak_at == T0 + timedelta(hours=1)
        and dd.trough_at == T0 + timedelta(hours=3)
    )
    assert dd.recovered_at == T0 + timedelta(hours=5) and dd.recovery_trades == 2
    unrecovered, _ = drawdown([sample(0, -1.0), sample(1, -1.0)])
    assert (
        unrecovered.max_drawdown_r == 2.0 and unrecovered.peak_at is None and unrecovered.recovered_at is None
    )
    flat, pts = drawdown([sample(0, None)])
    assert flat.max_drawdown_r == 0 and pts == []


def test_breakdown_never_names_a_best_group_on_small_samples():
    small = [sample(i, 1.0, symbol="XAUUSD") for i in range(5)] + [
        sample(10 + i, -1.0, symbol="EURUSD") for i in range(5)
    ]
    b = breakdown("ASSET", small, lambda s: s.symbol, CFG)
    assert b.best is None and "LIMITED" in b.best_reason and {g.key for g in b.groups} == {"XAUUSD", "EURUSD"}
    big = [sample(i, 0.5, symbol="XAUUSD") for i in range(30)] + [
        sample(100 + i, 1.0, symbol="EURUSD") for i in range(29)
    ]
    b = breakdown("ASSET", big, lambda s: s.symbol, CFG)
    assert b.best == "XAUUSD"  # EURUSD has higher expectancy but only 29 samples
    single = breakdown("ASSET", [sample(0, 1.0)], lambda s: s.symbol, CFG)
    assert single.best is None and "two groups" in single.best_reason


def test_process_stats_split_by_violations():
    samples = [
        sample(0, 2.0),
        sample(1, -1.0, violations=[RuleViolation.CHASED_ENTRY]),
        sample(2, 1.0, violations=[RuleViolation.CHASED_ENTRY, RuleViolation.NO_STOP_PLACED]),
    ]
    p = process_stats(samples, CFG)
    assert p.classifications == {"VALID_WIN": 1, "PROCESS_ERROR": 1, "BAD_PROCESS_WIN": 1}
    assert p.violation_counts == {"CHASED_ENTRY": 2, "NO_STOP_PLACED": 1}
    assert p.with_violations.count == 2 and p.without_violations.avg_r == 2.0


def test_dol_parsing_and_accuracy_for_aligned_records_only():
    assert parse_dol("H1 BSL PDH (strong) @ 2410.5") == ("BSL", 2410.5)
    assert parse_dol("no draw") is None and parse_dol(None) is None
    samples = [
        sample(0, 2.0, dol="H1 BSL PDH @ 2410", mfe_price=2412.0),  # aligned, reached
        sample(1, -1.0, dol="H1 BSL PDH @ 2410", mfe_price=2405.0),  # aligned, not reached
        sample(
            2, 1.0, direction=Direction.BEARISH, dol="H1 SSL PDL @ 2300", mfe_price=2299.0
        ),  # aligned, reached
        sample(3, -1.0, direction=Direction.BEARISH, dol="H1 BSL PDH @ 2410", mfe_price=2390.0),  # opposed
        sample(4, 1.0, dol=None),
        sample(5, 1.0, dol="H1 BSL EQH x3 @ 2050.91 (magnet 89.6)", mfe_price=2390.0),  # DOL behind the entry
    ]
    d = dol_accuracy(samples, CFG)
    assert (
        (d.evaluated, d.reached, d.unknown) == (3, 2, 1)
        and d.aligned.count == 4
        and d.rate == pytest.approx(0.6667, abs=1e-4)
    )
    assert d.opposed.count == 1 and d.label is SampleSizeLabel.INSUFFICIENT


def test_durations_and_downsampling():
    assert durations([sample(0, 1, duration=10), sample(1, 1, duration=30), sample(2, 1, duration=110)]) == (
        50.0,
        30.0,
    )
    assert durations([]) == (None, None)
    pts = [EquityPoint(at=T0 + timedelta(hours=i), cumulative_r=float(i)) for i in range(1000)]
    out = downsample(pts, 100)
    assert len(out) == 100 and out[-1] == pts[-1] and out[0] == pts[0]
    assert downsample(pts[:5], 100) == pts[:5]
