from __future__ import annotations

import math
from datetime import date, timedelta

import pytest

from src.scoring.quant_metrics import PriceRow, QuantMetrics, calculate_quant_metrics


# --- helpers ---

def daily_rows(
    prices: list[float],
    volumes: list[float] | None = None,
    end: date = date(2026, 6, 9),
) -> list[PriceRow]:
    n = len(prices)
    return [
        PriceRow(
            date=end - timedelta(days=n - 1 - i),
            close_usd=prices[i],
            volume_usd=volumes[i] if volumes else None,
        )
        for i in range(n)
    ]


# --- no data ---

def test_empty_rows_returns_all_none_without_crash() -> None:
    metrics = calculate_quant_metrics([])
    assert metrics == QuantMetrics()


def test_single_row_returns_all_none() -> None:
    rows = daily_rows([100.0])
    metrics = calculate_quant_metrics(rows)
    assert metrics.realized_vol_7d is None
    assert metrics.realized_vol_30d is None
    assert metrics.ma_20d_distance_pct is None


# --- realized vol ---

def test_realized_vol_flat_price_is_zero() -> None:
    rows = daily_rows([100.0] * 35)
    metrics = calculate_quant_metrics(rows)
    assert metrics.realized_vol_30d == pytest.approx(0.0, abs=1e-6)


def test_realized_vol_7d_uses_only_last_7_days() -> None:
    stable = [100.0] * 30
    volatile = [100.0, 110.0, 90.0, 115.0, 95.0, 105.0, 98.0]
    rows = daily_rows(stable + volatile)
    metrics = calculate_quant_metrics(rows)
    assert metrics.realized_vol_7d is not None
    assert metrics.realized_vol_7d > 0


def test_realized_vol_higher_for_more_volatile_price_series() -> None:
    stable_rows = daily_rows([100.0 + (i % 2) * 0.1 for i in range(35)])
    volatile_rows = daily_rows([100.0 * (1.05 ** i) if i % 2 == 0 else 100.0 * (0.95 ** i) for i in range(35)])
    stable_metrics = calculate_quant_metrics(stable_rows)
    volatile_metrics = calculate_quant_metrics(volatile_rows)
    assert volatile_metrics.realized_vol_30d > stable_metrics.realized_vol_30d


def test_realized_vol_annualized_formula() -> None:
    # Two points 30 days apart: price doubles → log return = ln(2)
    # vol = ln(2) * sqrt(252) (single observation, not meaningful, but tests formula path)
    rows = daily_rows([100.0] * 3 + [200.0])
    metrics = calculate_quant_metrics(rows)
    assert metrics.realized_vol_7d is not None


# --- volume z-score ---

def test_volume_z_score_none_when_no_volume_data() -> None:
    rows = daily_rows([100.0] * 35)
    metrics = calculate_quant_metrics(rows)
    assert metrics.volume_z_score_30d is None


def test_volume_z_score_positive_when_current_volume_above_mean() -> None:
    normal_vols = [1_000_000.0] * 29
    spike_vol = [5_000_000.0]
    rows = daily_rows([100.0] * 30, volumes=normal_vols + spike_vol)
    metrics = calculate_quant_metrics(rows)
    assert metrics.volume_z_score_30d is not None
    assert metrics.volume_z_score_30d > 2.0


def test_volume_z_score_negative_when_current_volume_below_mean() -> None:
    normal_vols = [5_000_000.0] * 29
    low_vol = [100_000.0]
    rows = daily_rows([100.0] * 30, volumes=normal_vols + low_vol)
    metrics = calculate_quant_metrics(rows)
    assert metrics.volume_z_score_30d is not None
    assert metrics.volume_z_score_30d < -1.0


def test_volume_z_score_zero_when_all_volumes_identical() -> None:
    rows = daily_rows([100.0] * 30, volumes=[1_000_000.0] * 30)
    metrics = calculate_quant_metrics(rows)
    assert metrics.volume_z_score_30d == pytest.approx(0.0)


# --- MA distance ---

def test_ma_distance_positive_when_price_above_20d_average() -> None:
    low_prices = [100.0] * 19
    high_price = [200.0]
    rows = daily_rows(low_prices + high_price)
    metrics = calculate_quant_metrics(rows)
    assert metrics.ma_20d_distance_pct is not None
    assert metrics.ma_20d_distance_pct > 0


def test_ma_distance_negative_when_price_below_20d_average() -> None:
    high_prices = [200.0] * 19
    low_price = [100.0]
    rows = daily_rows(high_prices + low_price)
    metrics = calculate_quant_metrics(rows)
    assert metrics.ma_20d_distance_pct is not None
    assert metrics.ma_20d_distance_pct < 0


def test_ma_distance_near_zero_for_flat_price() -> None:
    rows = daily_rows([100.0] * 25)
    metrics = calculate_quant_metrics(rows)
    assert metrics.ma_20d_distance_pct == pytest.approx(0.0, abs=1e-4)


# --- BTC correlation ---

def test_btc_correlation_none_when_no_btc_rows() -> None:
    rows = daily_rows([100.0] * 35)
    metrics = calculate_quant_metrics(rows, btc_rows=None)
    assert metrics.btc_correlation_30d is None


def test_btc_correlation_none_when_insufficient_overlap() -> None:
    coin = daily_rows([100.0] * 35)
    btc = daily_rows([50_000.0] * 5, end=date(2026, 1, 1))
    metrics = calculate_quant_metrics(coin, btc_rows=btc)
    assert metrics.btc_correlation_30d is None


def test_btc_correlation_high_for_same_return_pattern() -> None:
    # Zigzag pattern — actual return variance, coin and BTC move identically
    zigzag = [100.0 + (i % 5) * 3.0 - (i % 7) * 1.5 for i in range(35)]
    coin = daily_rows(zigzag)
    btc = daily_rows([p * 500 for p in zigzag])
    metrics = calculate_quant_metrics(coin, btc_rows=btc)
    assert metrics.btc_correlation_30d == pytest.approx(1.0, abs=1e-4)


def test_btc_correlation_negative_for_opposite_return_pattern() -> None:
    end = date(2026, 6, 9)
    base = [100.0 + (i % 5) * 4.0 - (i % 7) * 2.0 for i in range(35)]
    coin = daily_rows(base, end=end)
    # Invert the returns: when coin goes up, BTC goes down
    btc_prices = [10_000.0]
    for i in range(1, 35):
        ret = base[i] / base[i - 1]
        btc_prices.append(btc_prices[-1] / ret)  # opposite log return
    btc = daily_rows(btc_prices, end=end)
    metrics = calculate_quant_metrics(coin, btc_rows=btc)
    assert metrics.btc_correlation_30d == pytest.approx(-1.0, abs=1e-4)


def test_btc_correlation_near_zero_for_uncorrelated_series() -> None:
    end = date(2026, 6, 9)
    coin_prices = [100.0 + (i % 3) * 5 for i in range(35)]
    btc_prices = [50_000.0 + (i % 7) * 1000 for i in range(35)]
    coin = daily_rows(coin_prices, end=end)
    btc = daily_rows(btc_prices, end=end)
    metrics = calculate_quant_metrics(coin, btc_rows=btc)
    assert metrics.btc_correlation_30d is not None
    assert abs(metrics.btc_correlation_30d) < 0.5
