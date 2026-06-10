from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True)
class MarketSnapshotPoint:
    timestamp: datetime
    price_usd: float | None = None
    volume_24h_usd: float | None = None


@dataclass(frozen=True)
class MarketReactionMetrics:
    return_7d_pct: float | None = None
    return_14d_pct: float | None = None
    return_30d_pct: float | None = None
    volume_change_pct: float | None = None
    btc_relative_return_pct: float | None = None
    eth_relative_return_pct: float | None = None


def calculate_market_reaction(
    coin_snapshots: list[MarketSnapshotPoint],
    btc_snapshots: list[MarketSnapshotPoint] | None = None,
    eth_snapshots: list[MarketSnapshotPoint] | None = None,
    as_of: datetime | None = None,
) -> MarketReactionMetrics:
    current = _latest_snapshot_with_price(coin_snapshots, as_of=as_of)
    if current is None:
        return MarketReactionMetrics()

    return_7d = _lookback_return_pct(coin_snapshots, current, days=7)
    return_14d = _lookback_return_pct(coin_snapshots, current, days=14)
    return_30d = _lookback_return_pct(coin_snapshots, current, days=30)
    volume_change = _first_available_volume_change_pct(coin_snapshots, current, lookback_days=[7, 14, 30])

    btc_return_30d = _benchmark_return_pct(btc_snapshots or [], current, days=30)
    eth_return_30d = _benchmark_return_pct(eth_snapshots or [], current, days=30)

    return MarketReactionMetrics(
        return_7d_pct=return_7d,
        return_14d_pct=return_14d,
        return_30d_pct=return_30d,
        volume_change_pct=volume_change,
        btc_relative_return_pct=_relative_return_pct(return_30d, btc_return_30d),
        eth_relative_return_pct=_relative_return_pct(return_30d, eth_return_30d),
    )


def calculate_priced_in_penalty(metrics: MarketReactionMetrics, days_until_event: int) -> float:
    penalty = 0.0
    penalty += _tiered_penalty(metrics.return_30d_pct, low_threshold=10, high_threshold=25, extreme_threshold=50)
    penalty += _tiered_penalty(metrics.return_14d_pct, low_threshold=7, high_threshold=20, extreme_threshold=None)
    penalty += _tiered_penalty(metrics.return_7d_pct, low_threshold=5, high_threshold=15, extreme_threshold=None)

    relative_penalty = max(
        _relative_penalty(metrics.btc_relative_return_pct),
        _relative_penalty(metrics.eth_relative_return_pct),
    )
    penalty += relative_penalty
    penalty += _volume_penalty(metrics.volume_change_pct)
    penalty += _proximity_penalty(days_until_event)

    return min(100.0, penalty)


def adjusted_opportunity_score(catalyst_score: float, priced_in_penalty: float) -> float:
    return round(max(0.0, catalyst_score - priced_in_penalty), 2)


def percentage_return(current_value: float | None, past_value: float | None) -> float | None:
    if current_value is None or past_value is None or past_value <= 0:
        return None
    return ((current_value / past_value) - 1) * 100


def _latest_snapshot_with_price(
    snapshots: list[MarketSnapshotPoint],
    as_of: datetime | None = None,
) -> MarketSnapshotPoint | None:
    eligible = [
        snapshot
        for snapshot in snapshots
        if snapshot.price_usd is not None and (as_of is None or snapshot.timestamp <= as_of)
    ]
    if not eligible:
        return None
    return max(eligible, key=lambda snapshot: snapshot.timestamp)


def _lookback_return_pct(
    snapshots: list[MarketSnapshotPoint],
    current: MarketSnapshotPoint,
    days: int,
) -> float | None:
    past = _past_snapshot_with_price(snapshots, current.timestamp - timedelta(days=days))
    if past is None:
        return None
    return percentage_return(current.price_usd, past.price_usd)


def _benchmark_return_pct(
    snapshots: list[MarketSnapshotPoint],
    current: MarketSnapshotPoint,
    days: int,
) -> float | None:
    benchmark_current = _latest_snapshot_with_price(snapshots, as_of=current.timestamp)
    if benchmark_current is None:
        return None
    return _lookback_return_pct(snapshots, benchmark_current, days=days)


def _past_snapshot_with_price(
    snapshots: list[MarketSnapshotPoint],
    target_timestamp: datetime,
) -> MarketSnapshotPoint | None:
    eligible = [
        snapshot
        for snapshot in snapshots
        if snapshot.price_usd is not None and snapshot.timestamp <= target_timestamp
    ]
    if not eligible:
        return None
    return max(eligible, key=lambda snapshot: snapshot.timestamp)


def _first_available_volume_change_pct(
    snapshots: list[MarketSnapshotPoint],
    current: MarketSnapshotPoint,
    lookback_days: list[int],
) -> float | None:
    if current.volume_24h_usd is None:
        return None

    for days in lookback_days:
        past = _past_snapshot_with_volume(snapshots, current.timestamp - timedelta(days=days))
        if past is not None:
            return percentage_return(current.volume_24h_usd, past.volume_24h_usd)

    return None


def _past_snapshot_with_volume(
    snapshots: list[MarketSnapshotPoint],
    target_timestamp: datetime,
) -> MarketSnapshotPoint | None:
    eligible = [
        snapshot
        for snapshot in snapshots
        if snapshot.volume_24h_usd is not None and snapshot.timestamp <= target_timestamp
    ]
    if not eligible:
        return None
    return max(eligible, key=lambda snapshot: snapshot.timestamp)


def _relative_return_pct(coin_return: float | None, benchmark_return: float | None) -> float | None:
    if coin_return is None or benchmark_return is None:
        return None
    return coin_return - benchmark_return


def _tiered_penalty(
    value: float | None,
    low_threshold: float,
    high_threshold: float,
    extreme_threshold: float | None,
) -> float:
    if value is None or value < low_threshold:
        return 0.0
    if extreme_threshold is not None and value > extreme_threshold:
        return 30.0
    if value > high_threshold:
        return 20.0
    return 10.0


def _relative_penalty(value: float | None) -> float:
    if value is None or value < 5:
        return 0.0
    if value > 15:
        return 20.0
    return 10.0


def _volume_penalty(value: float | None) -> float:
    if value is None or value < 50:
        return 0.0
    if value > 150:
        return 20.0
    return 10.0


def _proximity_penalty(days_until_event: int) -> float:
    if days_until_event <= 7:
        return 10.0
    if days_until_event <= 14:
        return 5.0
    return 0.0
