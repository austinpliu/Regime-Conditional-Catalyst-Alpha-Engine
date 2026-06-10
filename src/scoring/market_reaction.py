from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta


# ─── Snapshot-based types (preserved) ─────────────────────────────────────────

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


# ─── History-based types ───────────────────────────────────────────────────────

@dataclass(frozen=True)
class DailyPricePoint:
    date: date
    price_usd: float | None
    volume_24h_usd: float | None = None


@dataclass(frozen=True)
class HistoryReactionMetrics:
    return_1d_pct: float | None = None
    return_3d_pct: float | None = None
    return_7d_pct: float | None = None
    return_14d_pct: float | None = None
    return_30d_pct: float | None = None
    btc_adjusted_return_7d_pct: float | None = None
    btc_adjusted_return_30d_pct: float | None = None
    eth_adjusted_return_7d_pct: float | None = None
    eth_adjusted_return_30d_pct: float | None = None
    volume_z_score: float | None = None
    realized_vol_7d_pct: float | None = None
    realized_vol_30d_pct: float | None = None
    volatility_ratio: float | None = None
    ma20_distance_pct: float | None = None


# ─── Snapshot-based functions (preserved) ──────────────────────────────────────

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


def calculate_priced_in_penalty(
    metrics: MarketReactionMetrics,
    days_until_event: int,
    volume_z_score: float | None = None,
    volatility_ratio: float | None = None,
) -> float:
    penalty = 0.0
    penalty += _tiered_penalty(metrics.return_30d_pct, low_threshold=10, high_threshold=25, extreme_threshold=50)
    penalty += _tiered_penalty(metrics.return_14d_pct, low_threshold=7, high_threshold=20, extreme_threshold=None)
    penalty += _tiered_penalty(metrics.return_7d_pct, low_threshold=5, high_threshold=15, extreme_threshold=None)

    relative_penalty = max(
        _relative_penalty(metrics.btc_relative_return_pct),
        _relative_penalty(metrics.eth_relative_return_pct),
    )
    penalty += relative_penalty

    if volume_z_score is not None:
        penalty += _volume_z_score_penalty(volume_z_score)
    else:
        penalty += _volume_penalty(metrics.volume_change_pct)

    if volatility_ratio is not None:
        penalty += _volatility_ratio_penalty(volatility_ratio)

    penalty += _proximity_penalty(days_until_event)

    return min(100.0, penalty)


def adjusted_opportunity_score(catalyst_score: float, priced_in_penalty: float) -> float:
    return round(max(0.0, catalyst_score - priced_in_penalty), 2)


def percentage_return(current_value: float | None, past_value: float | None) -> float | None:
    if current_value is None or past_value is None or past_value <= 0:
        return None
    return ((current_value / past_value) - 1) * 100


# ─── History-based calculation ─────────────────────────────────────────────────

def calculate_market_reaction_from_history(
    coin_history: list[DailyPricePoint],
    btc_history: list[DailyPricePoint] | None = None,
    eth_history: list[DailyPricePoint] | None = None,
    as_of: date | None = None,
) -> HistoryReactionMetrics:
    if not coin_history:
        return HistoryReactionMetrics()

    cutoff = as_of or max(r.date for r in coin_history)
    sorted_coin = sorted(
        (r for r in coin_history if r.price_usd is not None and r.date <= cutoff),
        key=lambda r: r.date,
    )
    if not sorted_coin:
        return HistoryReactionMetrics()

    return_1d = _hist_pct_return(sorted_coin, days=1)
    return_3d = _hist_pct_return(sorted_coin, days=3)
    return_7d = _hist_pct_return(sorted_coin, days=7)
    return_14d = _hist_pct_return(sorted_coin, days=14)
    return_30d = _hist_pct_return(sorted_coin, days=30)

    as_of_date = sorted_coin[-1].date
    btc_return_7d = _hist_pct_return_as_of(btc_history or [], as_of_date, days=7)
    btc_return_30d = _hist_pct_return_as_of(btc_history or [], as_of_date, days=30)
    eth_return_7d = _hist_pct_return_as_of(eth_history or [], as_of_date, days=7)
    eth_return_30d = _hist_pct_return_as_of(eth_history or [], as_of_date, days=30)

    rvol_7d = _hist_realized_vol(sorted_coin, days=7)
    rvol_30d = _hist_realized_vol(sorted_coin, days=30)
    vol_ratio: float | None = None
    if rvol_7d is not None and rvol_30d is not None and rvol_30d > 0:
        vol_ratio = round(rvol_7d / rvol_30d, 4)

    return HistoryReactionMetrics(
        return_1d_pct=return_1d,
        return_3d_pct=return_3d,
        return_7d_pct=return_7d,
        return_14d_pct=return_14d,
        return_30d_pct=return_30d,
        btc_adjusted_return_7d_pct=_relative_return_pct(return_7d, btc_return_7d),
        btc_adjusted_return_30d_pct=_relative_return_pct(return_30d, btc_return_30d),
        eth_adjusted_return_7d_pct=_relative_return_pct(return_7d, eth_return_7d),
        eth_adjusted_return_30d_pct=_relative_return_pct(return_30d, eth_return_30d),
        volume_z_score=_hist_volume_z_score(sorted_coin),
        realized_vol_7d_pct=rvol_7d,
        realized_vol_30d_pct=rvol_30d,
        volatility_ratio=vol_ratio,
        ma20_distance_pct=_hist_ma20_distance(sorted_coin),
    )


# ─── History-based helpers ─────────────────────────────────────────────────────

def _hist_pct_return(sorted_history: list[DailyPricePoint], days: int) -> float | None:
    if len(sorted_history) < 2:
        return None
    current = sorted_history[-1]
    target_date = current.date - timedelta(days=days)
    past_candidates = [r for r in sorted_history[:-1] if r.date <= target_date]
    if not past_candidates:
        return None
    past = max(past_candidates, key=lambda r: r.date)
    if past.price_usd is None or past.price_usd <= 0 or current.price_usd is None:
        return None
    return round(((current.price_usd / past.price_usd) - 1) * 100, 4)


def _hist_pct_return_as_of(
    history: list[DailyPricePoint],
    as_of: date,
    days: int,
) -> float | None:
    """Return for a benchmark series up to `as_of` — same-window as the coin."""
    sorted_hist = sorted(
        (r for r in history if r.price_usd is not None and r.date <= as_of),
        key=lambda r: r.date,
    )
    return _hist_pct_return(sorted_hist, days=days)


def _hist_realized_vol(sorted_history: list[DailyPricePoint], days: int) -> float | None:
    """Annualized std of log daily returns × √365 × 100."""
    cutoff = sorted_history[-1].date - timedelta(days=days)
    window = [r for r in sorted_history if r.date >= cutoff and r.price_usd is not None]
    if len(window) < 3:
        return None
    log_returns = [
        math.log(window[i].price_usd / window[i - 1].price_usd)
        for i in range(1, len(window))
        if window[i].price_usd > 0 and window[i - 1].price_usd > 0  # type: ignore[operator]
    ]
    if len(log_returns) < 2:
        return None
    mean = sum(log_returns) / len(log_returns)
    variance = sum((r - mean) ** 2 for r in log_returns) / (len(log_returns) - 1)
    return round(math.sqrt(variance) * math.sqrt(365) * 100, 4)


def _hist_volume_z_score(sorted_history: list[DailyPricePoint]) -> float | None:
    """(latest_vol - mean_baseline) / std_baseline; baseline = 30 days excluding latest day."""
    latest = sorted_history[-1]
    if latest.volume_24h_usd is None:
        return None
    baseline_start = latest.date - timedelta(days=30)
    baseline_vols = [
        r.volume_24h_usd
        for r in sorted_history[:-1]
        if r.date >= baseline_start and r.volume_24h_usd is not None
    ]
    if len(baseline_vols) < 3:
        return None
    mean = sum(baseline_vols) / len(baseline_vols)
    variance = sum((v - mean) ** 2 for v in baseline_vols) / (len(baseline_vols) - 1)
    std = math.sqrt(variance)
    if std == 0:
        return 0.0
    return round((latest.volume_24h_usd - mean) / std, 4)


def _hist_ma20_distance(sorted_history: list[DailyPricePoint]) -> float | None:
    """(current_price / SMA20 - 1) × 100."""
    current_price = sorted_history[-1].price_usd
    if not current_price:
        return None
    window_start = sorted_history[-1].date - timedelta(days=20)
    window = [r for r in sorted_history if r.date >= window_start and r.price_usd is not None]
    if len(window) < 10:
        return None
    ma20 = sum(r.price_usd for r in window) / len(window)  # type: ignore[misc]
    if ma20 == 0:
        return None
    return round((current_price / ma20 - 1) * 100, 4)


# ─── Snapshot helpers (preserved) ─────────────────────────────────────────────

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
    return round(coin_return - benchmark_return, 4)


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


def _volume_z_score_penalty(z: float | None) -> float:
    if z is None or z < 2.0:
        return 0.0
    if z > 3.0:
        return 20.0
    return 10.0


def _volatility_ratio_penalty(ratio: float | None) -> float:
    if ratio is None or ratio < 1.5:
        return 0.0
    if ratio > 2.0:
        return 10.0
    return 5.0


def _proximity_penalty(days_until_event: int) -> float:
    if days_until_event <= 7:
        return 10.0
    if days_until_event <= 14:
        return 5.0
    return 0.0
