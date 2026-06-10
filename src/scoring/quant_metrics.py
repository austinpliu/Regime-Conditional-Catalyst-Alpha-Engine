from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta


@dataclass(frozen=True)
class PriceRow:
    date: date
    close_usd: float | None
    volume_usd: float | None = None


@dataclass(frozen=True)
class QuantMetrics:
    realized_vol_7d: float | None = None
    realized_vol_30d: float | None = None
    volume_z_score_30d: float | None = None
    ma_20d_distance_pct: float | None = None
    btc_correlation_30d: float | None = None


def calculate_quant_metrics(
    rows: list[PriceRow],
    btc_rows: list[PriceRow] | None = None,
    as_of: date | None = None,
) -> QuantMetrics:
    cutoff = as_of or (rows[-1].date if rows else date.today())
    window = sorted(
        (r for r in rows if r.date <= cutoff and r.close_usd is not None),
        key=lambda r: r.date,
    )

    if not window:
        return QuantMetrics()

    return QuantMetrics(
        realized_vol_7d=_realized_vol(window, days=7),
        realized_vol_30d=_realized_vol(window, days=30),
        volume_z_score_30d=_volume_z_score(window, days=30),
        ma_20d_distance_pct=_ma_distance_pct(window, days=20),
        btc_correlation_30d=_btc_correlation(window, btc_rows or [], days=30),
    )


def _realized_vol(rows: list[PriceRow], days: int) -> float | None:
    start = rows[-1].date - timedelta(days=days)
    window = [r for r in rows if r.date >= start]
    if len(window) < 3:
        return None

    log_returns = [
        math.log(window[i].close_usd / window[i - 1].close_usd)
        for i in range(1, len(window))
        if window[i].close_usd > 0 and window[i - 1].close_usd > 0
    ]
    if len(log_returns) < 2:
        return None

    mean = sum(log_returns) / len(log_returns)
    variance = sum((r - mean) ** 2 for r in log_returns) / (len(log_returns) - 1)
    return round(math.sqrt(variance) * math.sqrt(252) * 100, 4)


def _volume_z_score(rows: list[PriceRow], days: int) -> float | None:
    start = rows[-1].date - timedelta(days=days)
    window = [r for r in rows if r.date >= start and r.volume_usd is not None]
    if len(window) < 3:
        return None

    volumes = [r.volume_usd for r in window]
    mean = sum(volumes) / len(volumes)
    variance = sum((v - mean) ** 2 for v in volumes) / (len(volumes) - 1)
    std = math.sqrt(variance)
    if std == 0:
        return 0.0
    return round((volumes[-1] - mean) / std, 4)


def _ma_distance_pct(rows: list[PriceRow], days: int) -> float | None:
    start = rows[-1].date - timedelta(days=days)
    window = [r for r in rows if r.date >= start and r.close_usd is not None]
    if len(window) < days // 2:
        return None

    current_price = rows[-1].close_usd
    if not current_price:
        return None

    ma = sum(r.close_usd for r in window) / len(window)
    if ma == 0:
        return None
    return round((current_price - ma) / ma * 100, 4)


def _btc_correlation(
    coin_rows: list[PriceRow],
    btc_rows: list[PriceRow],
    days: int,
) -> float | None:
    if not btc_rows:
        return None

    cutoff = coin_rows[-1].date
    start = cutoff - timedelta(days=days)

    btc_by_date = {r.date: r.close_usd for r in btc_rows if r.close_usd is not None}
    coin_by_date = {r.date: r.close_usd for r in coin_rows if r.close_usd is not None}

    common_dates = sorted(d for d in coin_by_date if d >= start and d in btc_by_date)
    if len(common_dates) < 10:
        return None

    coin_returns = [
        math.log(coin_by_date[common_dates[i]] / coin_by_date[common_dates[i - 1]])
        for i in range(1, len(common_dates))
        if coin_by_date[common_dates[i]] > 0 and coin_by_date[common_dates[i - 1]] > 0
    ]
    btc_returns = [
        math.log(btc_by_date[common_dates[i]] / btc_by_date[common_dates[i - 1]])
        for i in range(1, len(common_dates))
        if btc_by_date[common_dates[i]] > 0 and btc_by_date[common_dates[i - 1]] > 0
    ]

    if len(coin_returns) != len(btc_returns) or len(coin_returns) < 5:
        return None

    return round(_pearson(coin_returns, btc_returns), 4)


def _pearson(x: list[float], y: list[float]) -> float:
    n = len(x)
    mean_x = sum(x) / n
    mean_y = sum(y) / n
    cov = sum((x[i] - mean_x) * (y[i] - mean_y) for i in range(n))
    var_x = sum((xi - mean_x) ** 2 for xi in x)
    var_y = sum((yi - mean_y) ** 2 for yi in y)
    denom = math.sqrt(var_x * var_y)
    if denom == 0:
        return 0.0
    return cov / denom
