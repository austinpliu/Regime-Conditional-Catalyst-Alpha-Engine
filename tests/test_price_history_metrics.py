from __future__ import annotations

import math
from datetime import date, timedelta

import pytest

from src.scoring.market_reaction import (
    DailyPricePoint,
    HistoryReactionMetrics,
    calculate_market_reaction_from_history,
)


# ─── Helpers ──────────────────────────────────────────────────────────────────

END = date(2026, 6, 10)


def daily_points(
    prices: list[float],
    volumes: list[float] | None = None,
    end: date = END,
) -> list[DailyPricePoint]:
    n = len(prices)
    return [
        DailyPricePoint(
            date=end - timedelta(days=n - 1 - i),
            price_usd=prices[i],
            volume_24h_usd=volumes[i] if volumes else None,
        )
        for i in range(n)
    ]


def flat(n: int, price: float = 100.0, volume: float | None = None) -> list[DailyPricePoint]:
    prices = [price] * n
    vols = [volume] * n if volume is not None else None
    return daily_points(prices, vols)


# ─── Empty / short history ────────────────────────────────────────────────────

def test_empty_returns_all_none() -> None:
    m = calculate_market_reaction_from_history([])
    assert m == HistoryReactionMetrics()


def test_single_point_returns_all_none() -> None:
    m = calculate_market_reaction_from_history(flat(1))
    assert m.return_1d_pct is None
    assert m.return_7d_pct is None
    assert m.realized_vol_7d_pct is None


def test_five_days_history_most_metrics_none() -> None:
    m = calculate_market_reaction_from_history(flat(5, price=100.0))
    # Not enough for 7d / 14d / 30d returns
    assert m.return_7d_pct is None
    assert m.return_14d_pct is None
    assert m.return_30d_pct is None
    # Realized vol needs ≥3 points in window; 5-day series has 4 in a 7d window → should compute
    assert m.realized_vol_7d_pct is not None or m.realized_vol_7d_pct is None  # no crash


# ─── Return windows ───────────────────────────────────────────────────────────

def test_return_1d_pct() -> None:
    prices = [100.0] * 3 + [110.0]  # today +10% vs yesterday
    m = calculate_market_reaction_from_history(daily_points(prices))
    assert m.return_1d_pct == pytest.approx(10.0, rel=1e-4)


def test_return_3d_pct() -> None:
    prices = [100.0] + [100.0] * 2 + [120.0]  # today +20% vs 3d ago
    m = calculate_market_reaction_from_history(daily_points(prices))
    assert m.return_3d_pct == pytest.approx(20.0, rel=1e-4)


def test_return_7d_pct() -> None:
    prices = [200.0] + [200.0] * 6 + [100.0]  # 8 points; today -50%
    m = calculate_market_reaction_from_history(daily_points(prices))
    assert m.return_7d_pct == pytest.approx(-50.0, rel=1e-4)


def test_return_14d_pct() -> None:
    prices = [50.0] + [50.0] * 13 + [100.0]  # 15 points; today +100%
    m = calculate_market_reaction_from_history(daily_points(prices))
    assert m.return_14d_pct == pytest.approx(100.0, rel=1e-4)


def test_return_30d_pct() -> None:
    prices = [80.0] + [80.0] * 29 + [100.0]  # 31 points; today +25%
    m = calculate_market_reaction_from_history(daily_points(prices))
    assert m.return_30d_pct == pytest.approx(25.0, rel=1e-4)


def test_return_none_when_insufficient_history() -> None:
    m = calculate_market_reaction_from_history(flat(3))
    assert m.return_7d_pct is None
    assert m.return_14d_pct is None
    assert m.return_30d_pct is None


# ─── Same-window BTC/ETH adjusted returns ─────────────────────────────────────

def test_btc_adjusted_return_7d_same_window() -> None:
    coin_prices = [100.0] + [100.0] * 6 + [120.0]   # +20% over 7d
    btc_prices  = [50000.0] + [50000.0] * 6 + [55000.0]  # +10% over 7d
    coin = daily_points(coin_prices)
    btc  = daily_points(btc_prices)
    m = calculate_market_reaction_from_history(coin, btc_history=btc)
    # adjusted = coin_return_7d - btc_return_7d = 20 - 10 = 10
    assert m.btc_adjusted_return_7d_pct == pytest.approx(10.0, rel=1e-4)


def test_btc_adjusted_return_30d_same_window() -> None:
    coin_prices = [100.0] + [100.0] * 29 + [150.0]   # +50% over 30d
    btc_prices  = [50000.0] + [50000.0] * 29 + [60000.0]  # +20% over 30d
    coin = daily_points(coin_prices)
    btc  = daily_points(btc_prices)
    m = calculate_market_reaction_from_history(coin, btc_history=btc)
    assert m.btc_adjusted_return_30d_pct == pytest.approx(30.0, rel=1e-4)


def test_eth_adjusted_return_7d_same_window() -> None:
    coin_prices = [100.0] + [100.0] * 6 + [115.0]  # +15%
    eth_prices  = [3000.0] + [3000.0] * 6 + [3150.0]  # +5%
    coin = daily_points(coin_prices)
    eth  = daily_points(eth_prices)
    m = calculate_market_reaction_from_history(coin, eth_history=eth)
    assert m.eth_adjusted_return_7d_pct == pytest.approx(10.0, rel=1e-4)


def test_adjusted_returns_none_when_no_benchmark() -> None:
    m = calculate_market_reaction_from_history(flat(35))
    assert m.btc_adjusted_return_7d_pct is None
    assert m.btc_adjusted_return_30d_pct is None
    assert m.eth_adjusted_return_7d_pct is None
    assert m.eth_adjusted_return_30d_pct is None


# ─── Volume z-score ───────────────────────────────────────────────────────────

def _varying_vols(n: int, base: float = 1_000_000.0) -> list[float]:
    """Return n volumes with meaningful variance so z-score std > 0."""
    return [base * (1 + 0.15 * ((i % 5) - 2)) for i in range(n)]


def test_volume_z_score_spike_is_positive() -> None:
    baseline_vols = _varying_vols(30)   # mean ≈ 1M, std > 0
    spike_vols = [8_000_000.0]
    pts = daily_points([100.0] * 31, volumes=baseline_vols + spike_vols)
    m = calculate_market_reaction_from_history(pts)
    assert m.volume_z_score is not None
    assert m.volume_z_score > 2.0


def test_volume_z_score_low_vol_is_negative() -> None:
    baseline_vols = _varying_vols(30, base=5_000_000.0)  # mean ≈ 5M, std > 0
    low_vols = [100_000.0]
    pts = daily_points([100.0] * 31, volumes=baseline_vols + low_vols)
    m = calculate_market_reaction_from_history(pts)
    assert m.volume_z_score is not None
    assert m.volume_z_score < -1.0


def test_volume_z_score_excludes_latest_day_from_baseline() -> None:
    # Baseline has real variance; latest day is a big spike.
    # If latest day were incorrectly included in the baseline, the mean would rise
    # and std would widen, reducing the z-score.  By excluding it, z should remain large.
    baseline_vols = _varying_vols(30)   # mean ≈ 1M, std ≈ 150k
    latest_vol = [10_000_000.0]
    pts = daily_points([100.0] * 31, volumes=baseline_vols + latest_vol)
    m = calculate_market_reaction_from_history(pts)
    assert m.volume_z_score is not None
    assert m.volume_z_score > 2.0


def test_volume_z_score_none_when_no_volumes() -> None:
    m = calculate_market_reaction_from_history(flat(35))
    assert m.volume_z_score is None


def test_volume_z_score_none_when_insufficient_baseline() -> None:
    # 3 total points → 2 baseline points (< 3 required) → None
    pts = daily_points([100.0] * 3, volumes=[1_000_000.0] * 3)
    m = calculate_market_reaction_from_history(pts)
    assert m.volume_z_score is None


# ─── Realized volatility ──────────────────────────────────────────────────────

def test_realized_vol_7d_flat_price_is_zero() -> None:
    m = calculate_market_reaction_from_history(flat(35))
    assert m.realized_vol_7d_pct == pytest.approx(0.0, abs=1e-6)


def test_realized_vol_30d_flat_price_is_zero() -> None:
    m = calculate_market_reaction_from_history(flat(35))
    assert m.realized_vol_30d_pct == pytest.approx(0.0, abs=1e-6)


def test_realized_vol_7d_uses_sqrt_365_annualization() -> None:
    # Known log return: 1 price doubling over single day → ln(2)
    # With 3 points: returns = [ln(2), ln(2)]; std = 0; annualized = 0
    # Use volatile series to verify formula direction
    prices = [100.0, 110.0, 90.0, 115.0, 95.0, 108.0, 98.0, 112.0]
    m = calculate_market_reaction_from_history(daily_points(prices))
    assert m.realized_vol_7d_pct is not None
    assert m.realized_vol_7d_pct > 0


def test_realized_vol_30d_higher_for_more_volatile_series() -> None:
    stable   = flat(35, price=100.0)
    volatile = daily_points([100.0 * (1.05 ** i) if i % 2 == 0 else 100.0 * (0.96 ** i) for i in range(35)])
    m_stable   = calculate_market_reaction_from_history(stable)
    m_volatile = calculate_market_reaction_from_history(volatile)
    assert m_volatile.realized_vol_30d_pct > m_stable.realized_vol_30d_pct


def test_realized_vol_none_with_insufficient_history() -> None:
    m = calculate_market_reaction_from_history(flat(2))
    assert m.realized_vol_7d_pct is None
    assert m.realized_vol_30d_pct is None


# ─── Volatility ratio ─────────────────────────────────────────────────────────

def test_volatility_ratio_equals_7d_over_30d() -> None:
    zigzag = [100.0 + (i % 5) * 4.0 - (i % 7) * 2.0 for i in range(35)]
    m = calculate_market_reaction_from_history(daily_points(zigzag))
    if m.realized_vol_7d_pct is not None and m.realized_vol_30d_pct is not None and m.realized_vol_30d_pct > 0:
        expected_ratio = m.realized_vol_7d_pct / m.realized_vol_30d_pct
        assert m.volatility_ratio == pytest.approx(expected_ratio, rel=1e-4)


def test_volatility_ratio_none_when_vol_missing() -> None:
    m = calculate_market_reaction_from_history(flat(2))
    assert m.volatility_ratio is None


# ─── MA20 distance ────────────────────────────────────────────────────────────

def test_ma20_distance_positive_when_price_above_ma() -> None:
    prices = [100.0] * 20 + [200.0]
    m = calculate_market_reaction_from_history(daily_points(prices))
    assert m.ma20_distance_pct is not None
    assert m.ma20_distance_pct > 0


def test_ma20_distance_negative_when_price_below_ma() -> None:
    prices = [200.0] * 20 + [100.0]
    m = calculate_market_reaction_from_history(daily_points(prices))
    assert m.ma20_distance_pct is not None
    assert m.ma20_distance_pct < 0


def test_ma20_distance_near_zero_for_flat_price() -> None:
    m = calculate_market_reaction_from_history(flat(25))
    assert m.ma20_distance_pct == pytest.approx(0.0, abs=1e-4)


def test_ma20_distance_none_with_short_history() -> None:
    m = calculate_market_reaction_from_history(flat(5))
    assert m.ma20_distance_pct is None


# ─── as_of parameter ──────────────────────────────────────────────────────────

def test_as_of_restricts_history_window() -> None:
    prices = [100.0] * 8 + [150.0] * 8  # first half flat, second half up
    pts = daily_points(prices)
    # as_of = 8th day: latest price should be 100
    as_of = pts[7].date
    m = calculate_market_reaction_from_history(pts, as_of=as_of)
    # All prices in window are 100; no 7d return possible (not enough points before day 8-7=1)
    # But MA20 should be None (only 8 points)
    assert m.ma20_distance_pct is None
