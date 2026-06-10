from datetime import datetime, timedelta

import pytest

from src.scoring.market_reaction import (
    MarketReactionMetrics,
    MarketSnapshotPoint,
    adjusted_opportunity_score,
    calculate_market_reaction,
    calculate_priced_in_penalty,
)


def test_no_market_data_returns_zero_penalty_without_crashing() -> None:
    metrics = calculate_market_reaction([])

    penalty = calculate_priced_in_penalty(metrics, days_until_event=30)

    assert metrics.return_7d_pct is None
    assert penalty == 0


def test_large_30d_return_creates_higher_penalty() -> None:
    low_penalty = calculate_priced_in_penalty(
        MarketReactionMetrics(return_30d_pct=9),
        days_until_event=30,
    )
    high_penalty = calculate_priced_in_penalty(
        MarketReactionMetrics(return_30d_pct=55),
        days_until_event=30,
    )

    assert high_penalty > low_penalty
    assert high_penalty == 30


def test_large_7d_return_creates_higher_penalty() -> None:
    low_penalty = calculate_priced_in_penalty(
        MarketReactionMetrics(return_7d_pct=4),
        days_until_event=30,
    )
    high_penalty = calculate_priced_in_penalty(
        MarketReactionMetrics(return_7d_pct=18),
        days_until_event=30,
    )

    assert high_penalty > low_penalty
    assert high_penalty == 20


def test_strong_btc_relative_outperformance_creates_higher_penalty() -> None:
    low_penalty = calculate_priced_in_penalty(
        MarketReactionMetrics(btc_relative_return_pct=4),
        days_until_event=30,
    )
    high_penalty = calculate_priced_in_penalty(
        MarketReactionMetrics(btc_relative_return_pct=18),
        days_until_event=30,
    )

    assert high_penalty > low_penalty
    assert high_penalty == 20


def test_volume_spike_creates_higher_penalty() -> None:
    low_penalty = calculate_priced_in_penalty(
        MarketReactionMetrics(volume_change_pct=40),
        days_until_event=30,
    )
    high_penalty = calculate_priced_in_penalty(
        MarketReactionMetrics(volume_change_pct=180),
        days_until_event=30,
    )

    assert high_penalty > low_penalty
    assert high_penalty == 20


def test_event_proximity_increases_penalty() -> None:
    far_penalty = calculate_priced_in_penalty(MarketReactionMetrics(), days_until_event=30)
    near_penalty = calculate_priced_in_penalty(MarketReactionMetrics(), days_until_event=6)

    assert near_penalty > far_penalty
    assert near_penalty == 10


def test_penalty_is_capped_at_one_hundred() -> None:
    penalty = calculate_priced_in_penalty(
        MarketReactionMetrics(
            return_7d_pct=50,
            return_14d_pct=60,
            return_30d_pct=100,
            volume_change_pct=300,
            btc_relative_return_pct=50,
            eth_relative_return_pct=50,
        ),
        days_until_event=1,
    )

    assert penalty == 100


def test_adjusted_score_does_not_go_below_zero() -> None:
    assert adjusted_opportunity_score(catalyst_score=25, priced_in_penalty=80) == 0


def test_market_reaction_calculates_returns_and_relative_performance() -> None:
    now = datetime(2026, 6, 8)
    coin_snapshots = [
        MarketSnapshotPoint(timestamp=now - timedelta(days=30), price_usd=100, volume_24h_usd=1000),
        MarketSnapshotPoint(timestamp=now - timedelta(days=7), price_usd=120, volume_24h_usd=1000),
        MarketSnapshotPoint(timestamp=now, price_usd=150, volume_24h_usd=2500),
    ]
    btc_snapshots = [
        MarketSnapshotPoint(timestamp=now - timedelta(days=30), price_usd=100, volume_24h_usd=1000),
        MarketSnapshotPoint(timestamp=now, price_usd=110, volume_24h_usd=1000),
    ]

    metrics = calculate_market_reaction(coin_snapshots, btc_snapshots=btc_snapshots)

    assert metrics.return_30d_pct == 50
    assert metrics.return_7d_pct == 25
    assert metrics.volume_change_pct == 150
    assert metrics.btc_relative_return_pct == pytest.approx(40)


def test_seeded_style_historical_snapshots_allow_return_calculation() -> None:
    now = datetime(2026, 6, 9)
    sol_snapshots = seeded_points(
        now,
        prices=[120, 145, 160, 180],
        volumes=[2_800_000_000, 3_300_000_000, 3_200_000_000, 10_000_000_000],
    )

    metrics = calculate_market_reaction(sol_snapshots)

    assert metrics.return_7d_pct == pytest.approx(12.5)
    assert metrics.return_14d_pct == pytest.approx((180 / 145 - 1) * 100)
    assert metrics.return_30d_pct == pytest.approx(50)
    assert metrics.volume_change_pct == pytest.approx(212.5)


def test_sol_style_outperformance_creates_visible_penalty_and_lower_adjusted_score() -> None:
    now = datetime(2026, 6, 9)
    btc_snapshots = seeded_points(
        now,
        prices=[100_000, 102_000, 104_000, 105_000],
        volumes=[30_000_000_000, 32_000_000_000, 34_000_000_000, 35_000_000_000],
    )
    eth_snapshots = seeded_points(
        now,
        prices=[3_000, 3_100, 3_150, 3_200],
        volumes=[12_000_000_000, 12_800_000_000, 13_400_000_000, 14_000_000_000],
    )
    sol_snapshots = seeded_points(
        now,
        prices=[120, 145, 160, 180],
        volumes=[2_800_000_000, 3_300_000_000, 3_200_000_000, 10_000_000_000],
    )

    metrics = calculate_market_reaction(
        sol_snapshots,
        btc_snapshots=btc_snapshots,
        eth_snapshots=eth_snapshots,
    )
    penalty = calculate_priced_in_penalty(metrics, days_until_event=30)
    adjusted_score = adjusted_opportunity_score(catalyst_score=95, priced_in_penalty=penalty)

    assert metrics.return_7d_pct > 0
    assert metrics.return_14d_pct > 0
    assert metrics.return_30d_pct > 0
    assert metrics.btc_relative_return_pct > 0
    assert metrics.eth_relative_return_pct > 0
    assert penalty == 90
    assert adjusted_score < 95


def seeded_points(
    now: datetime,
    prices: list[float],
    volumes: list[float],
) -> list[MarketSnapshotPoint]:
    days_ago = [30, 14, 7, 0]
    return [
        MarketSnapshotPoint(
            timestamp=now - timedelta(days=days),
            price_usd=price,
            volume_24h_usd=volume,
        )
        for days, price, volume in zip(days_ago, prices, volumes, strict=True)
    ]
