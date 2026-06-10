from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import joinedload

from src.config import Settings
from src.data.cmc_client import CoinMarketCapClient, CoinMarketData
from src.models.catalyst import Catalyst, CatalystCreate, EventType
from src.models.coin import Coin
from src.models.market_snapshot import MarketSnapshot
from src.models.price_history import PriceHistory
from src.scoring.catalyst_score import calculate_catalyst_score, days_until_event, estimate_source_credibility
from src.scoring.market_reaction import (
    DailyPricePoint,
    HistoryReactionMetrics,
    MarketReactionMetrics,
    MarketSnapshotPoint,
    adjusted_opportunity_score,
    calculate_market_reaction,
    calculate_market_reaction_from_history,
    calculate_priced_in_penalty,
)
from src.storage.db import init_db, session_scope


CSV_COLUMNS = [
    "symbol",
    "project_name",
    "event_type",
    "event_date",
    "days_until_event",
    "description",
    "source_url",
    "confidence_score",
    "catalyst_score",
    # Returns — present in both paths
    "return_1d_pct",
    "return_3d_pct",
    "return_7d_pct",
    "return_14d_pct",
    "return_30d_pct",
    # Benchmark-adjusted returns (history path)
    "btc_adjusted_return_7d_pct",
    "btc_adjusted_return_30d_pct",
    "eth_adjusted_return_7d_pct",
    "eth_adjusted_return_30d_pct",
    # Legacy benchmark names (snapshot fallback; also populated from history for dashboard compat)
    "volume_change_pct",
    "btc_relative_return_pct",
    "eth_relative_return_pct",
    # Scoring
    "priced_in_penalty",
    "adjusted_score",
    # Quant metrics — history path
    "volume_z_score",
    "realized_vol_7d_pct",
    "realized_vol_30d_pct",
    "volatility_ratio",
    "ma20_distance_pct",
    # Legacy quant column names (dashboard compat; mapped from history metrics)
    "realized_vol_7d",
    "realized_vol_30d",
    "volume_z_score_30d",
    "ma_20d_distance_pct",
    "btc_correlation_30d",
]


@dataclass(frozen=True)
class CatalystAddResult:
    catalyst_id: int
    symbol: str
    project_name: str
    catalyst_score: float


def ensure_database(settings: Settings) -> None:
    init_db(settings.database_url)


def update_coin_universe(settings: Settings, limit: int | None = None) -> int:
    ensure_database(settings)
    client = CoinMarketCapClient(settings)
    coins = client.get_top_assets(limit=limit or settings.cmc_limit)
    snapshot_timestamp = datetime.now(timezone.utc)

    with session_scope(settings.database_url) as session:
        for coin_data in coins:
            upsert_coin(session, coin_data)
            save_market_snapshot(session, coin_data, timestamp=snapshot_timestamp)

    return len(coins)


def upsert_coin(session, coin_data: CoinMarketData) -> None:
    coin = session.execute(select(Coin).where(Coin.cmc_id == coin_data.cmc_id)).scalar_one_or_none()

    values = {
        "cmc_id": coin_data.cmc_id,
        "name": coin_data.name,
        "symbol": coin_data.symbol,
        "slug": coin_data.slug,
        "cmc_rank": coin_data.cmc_rank,
        "price_usd": coin_data.price_usd,
        "market_cap_usd": coin_data.market_cap_usd,
        "volume_24h_usd": coin_data.volume_24h_usd,
        "last_updated": coin_data.last_updated,
    }

    if coin is None:
        session.add(Coin(**values))
        return

    for field_name, value in values.items():
        setattr(coin, field_name, value)


def save_market_snapshot(session, coin_data: CoinMarketData, timestamp: datetime) -> None:
    session.add(
        MarketSnapshot(
            symbol=coin_data.symbol.upper(),
            price_usd=coin_data.price_usd,
            volume_24h_usd=coin_data.volume_24h_usd,
            market_cap_usd=coin_data.market_cap_usd,
            timestamp=timestamp,
        )
    )


def add_catalyst(
    settings: Settings,
    symbol: str,
    event_type: str,
    event_date: str | date,
    description: str,
    source_url: str,
    confidence_score: float,
    source_credibility: float | None = None,
) -> CatalystAddResult:
    ensure_database(settings)

    parsed_event_date = date.fromisoformat(event_date) if isinstance(event_date, str) else event_date
    inferred_source_credibility = estimate_source_credibility(source_url)
    catalyst_input = CatalystCreate(
        coin_symbol=symbol,
        event_type=EventType(event_type),
        event_date=parsed_event_date,
        description=description,
        source_url=source_url,
        confidence_score=confidence_score,
        source_credibility=source_credibility if source_credibility is not None else inferred_source_credibility,
    )

    with session_scope(settings.database_url) as session:
        coin = _find_coin_by_symbol(session, catalyst_input.coin_symbol)

        catalyst = Catalyst(
            coin_id=coin.id,
            event_type=catalyst_input.event_type.value,
            event_date=catalyst_input.event_date,
            description=catalyst_input.description,
            source_url=catalyst_input.source_url,
            confidence_score=catalyst_input.confidence_score,
            source_credibility=catalyst_input.source_credibility,
        )
        session.add(catalyst)
        session.flush()

        score = calculate_catalyst_score(
            event_type=catalyst.event_type,
            source_credibility=catalyst.source_credibility,
            days_until=days_until_event(catalyst.event_date),
            confidence_score=catalyst.confidence_score,
            window_days=settings.ranking_window_days,
        )

        return CatalystAddResult(
            catalyst_id=catalyst.id,
            symbol=coin.symbol,
            project_name=coin.name,
            catalyst_score=score,
        )


def update_catalyst(
    settings: Settings,
    catalyst_id: int,
    symbol: str,
    event_type: str,
    event_date: str | date,
    description: str,
    source_url: str,
    confidence_score: float,
    source_credibility: float | None = None,
) -> CatalystAddResult:
    ensure_database(settings)

    parsed_event_date = date.fromisoformat(event_date) if isinstance(event_date, str) else event_date
    inferred_source_credibility = estimate_source_credibility(source_url)
    catalyst_input = CatalystCreate(
        coin_symbol=symbol,
        event_type=EventType(event_type),
        event_date=parsed_event_date,
        description=description,
        source_url=source_url,
        confidence_score=confidence_score,
        source_credibility=source_credibility if source_credibility is not None else inferred_source_credibility,
    )

    with session_scope(settings.database_url) as session:
        catalyst = session.execute(
            select(Catalyst).where(Catalyst.id == catalyst_id)
        ).scalar_one_or_none()
        if catalyst is None:
            raise ValueError(f"No catalyst found with id {catalyst_id}.")

        coin = _find_coin_by_symbol(session, catalyst_input.coin_symbol)

        catalyst.coin_id = coin.id
        catalyst.event_type = catalyst_input.event_type.value
        catalyst.event_date = catalyst_input.event_date
        catalyst.description = catalyst_input.description
        catalyst.source_url = catalyst_input.source_url
        catalyst.confidence_score = catalyst_input.confidence_score
        catalyst.source_credibility = catalyst_input.source_credibility
        session.flush()

        score = calculate_catalyst_score(
            event_type=catalyst.event_type,
            source_credibility=catalyst.source_credibility,
            days_until=days_until_event(catalyst.event_date),
            confidence_score=catalyst.confidence_score,
            window_days=settings.ranking_window_days,
        )

        return CatalystAddResult(
            catalyst_id=catalyst.id,
            symbol=coin.symbol,
            project_name=coin.name,
            catalyst_score=score,
        )


def get_catalyst_detail(settings: Settings, catalyst_id: int) -> dict[str, object] | None:
    ensure_database(settings)

    with session_scope(settings.database_url) as session:
        catalyst = session.execute(
            select(Catalyst)
            .options(joinedload(Catalyst.coin))
            .where(Catalyst.id == catalyst_id)
        ).scalar_one_or_none()

        if catalyst is None:
            return None

        return {
            "id": catalyst.id,
            "symbol": catalyst.coin.symbol,
            "project_name": catalyst.coin.name,
            "event_type": catalyst.event_type,
            "event_date": catalyst.event_date.isoformat(),
            "description": catalyst.description,
            "source_url": catalyst.source_url,
            "confidence_score": catalyst.confidence_score,
            "source_credibility": catalyst.source_credibility,
        }


def dashboard_summary(settings: Settings, days: int | None = None) -> dict[str, int]:
    ensure_database(settings)
    window_days = days or settings.ranking_window_days
    today = date.today()
    max_date = today + timedelta(days=window_days)

    with session_scope(settings.database_url) as session:
        coin_count = session.scalar(select(func.count(Coin.id))) or 0
        catalyst_count = session.scalar(select(func.count(Catalyst.id))) or 0
        upcoming_count = session.scalar(
            select(func.count(Catalyst.id))
            .where(Catalyst.event_date >= today)
            .where(Catalyst.event_date <= max_date)
        ) or 0

    return {
        "coin_count": coin_count,
        "catalyst_count": catalyst_count,
        "upcoming_count": upcoming_count,
        "window_days": window_days,
    }


def top_coin_rows(settings: Settings, limit: int = 12) -> list[dict[str, object]]:
    ensure_database(settings)

    with session_scope(settings.database_url) as session:
        coins = session.execute(
            select(Coin)
            .order_by(Coin.cmc_rank.is_(None), Coin.cmc_rank)
            .limit(limit)
        ).scalars().all()

        symbols = [c.symbol.upper() for c in coins]
        history_prices: dict[str, float | None] = {}
        if symbols:
            latest_date_sq = (
                select(PriceHistory.symbol, func.max(PriceHistory.date).label("max_date"))
                .where(PriceHistory.symbol.in_(symbols))
                .group_by(PriceHistory.symbol)
                .subquery()
            )
            for row in session.execute(
                select(PriceHistory.symbol, PriceHistory.price_usd)
                .join(latest_date_sq,
                      (PriceHistory.symbol == latest_date_sq.c.symbol) &
                      (PriceHistory.date == latest_date_sq.c.max_date))
            ).all():
                history_prices[row.symbol] = row.price_usd

        return [
            {
                "symbol": coin.symbol,
                "name": coin.name,
                "rank": coin.cmc_rank,
                "market_cap_usd": coin.market_cap_usd,
                "price_usd": history_prices.get(coin.symbol.upper()),
            }
            for coin in coins
        ]


def market_overview(settings: Settings) -> dict[str, object]:
    ensure_database(settings)

    with session_scope(settings.database_url) as session:
        tracked_coin_count = session.scalar(select(func.count(Coin.id))) or 0

        latest_date_sq = (
            select(PriceHistory.symbol, func.max(PriceHistory.date).label("max_date"))
            .group_by(PriceHistory.symbol)
            .subquery()
        )
        latest_rows = session.execute(
            select(PriceHistory)
            .join(latest_date_sq,
                  (PriceHistory.symbol == latest_date_sq.c.symbol) &
                  (PriceHistory.date == latest_date_sq.c.max_date))
        ).scalars().all()

        latest_date = max((r.date for r in latest_rows), default=None)
        total_market_cap = sum(r.market_cap_usd or 0 for r in latest_rows) if latest_rows else None
        total_volume = sum(r.volume_24h_usd or 0 for r in latest_rows) if latest_rows else None

        history_cache: dict[str, list[DailyPricePoint]] = {}
        btc_history = _daily_price_points_for_symbol(session, "BTC", history_cache)
        eth_history = _daily_price_points_for_symbol(session, "ETH", history_cache)

    btc_metrics = calculate_market_reaction_from_history(btc_history)
    eth_metrics = calculate_market_reaction_from_history(eth_history)

    return {
        "total_tracked_market_cap": total_market_cap,
        "total_tracked_volume_24h": total_volume,
        "tracked_coin_count": tracked_coin_count,
        "latest_snapshot_timestamp": latest_date.isoformat() if latest_date else None,
        "btc_return_7d_pct": _round_optional(btc_metrics.return_7d_pct),
        "eth_return_7d_pct": _round_optional(eth_metrics.return_7d_pct),
    }


def top_market_cap_snapshot_rows(settings: Settings, limit: int = 10) -> list[dict[str, object]]:
    ensure_database(settings)

    with session_scope(settings.database_url) as session:
        latest_date_sq = (
            select(PriceHistory.symbol, func.max(PriceHistory.date).label("max_date"))
            .group_by(PriceHistory.symbol)
            .subquery()
        )
        rows = session.execute(
            select(PriceHistory)
            .join(latest_date_sq,
                  (PriceHistory.symbol == latest_date_sq.c.symbol) &
                  (PriceHistory.date == latest_date_sq.c.max_date))
            .where(PriceHistory.market_cap_usd.is_not(None))
            .order_by(PriceHistory.market_cap_usd.desc())
            .limit(limit)
        ).scalars().all()

        return [{"symbol": r.symbol, "market_cap_usd": r.market_cap_usd} for r in rows]


def market_timeseries(settings: Settings, limit_points: int = 48) -> list[dict[str, object]]:
    ensure_database(settings)

    with session_scope(settings.database_url) as session:
        rows = session.execute(
            select(
                PriceHistory.date,
                func.sum(PriceHistory.market_cap_usd).label("total_market_cap"),
                func.sum(PriceHistory.volume_24h_usd).label("total_volume_24h"),
            )
            .group_by(PriceHistory.date)
            .order_by(PriceHistory.date)
        ).all()

    return [
        {
            "timestamp": row.date.isoformat(),
            "total_market_cap": float(row.total_market_cap or 0),
            "total_volume_24h": float(row.total_volume_24h or 0),
        }
        for row in rows
    ][-limit_points:]


def rank_catalyst_rows(settings: Settings, days: int | None = None) -> list[dict[str, object]]:
    ensure_database(settings)
    window_days = days or settings.ranking_window_days
    today = date.today()
    max_date = today + timedelta(days=window_days)

    with session_scope(settings.database_url) as session:
        catalysts = session.execute(
            select(Catalyst)
            .options(joinedload(Catalyst.coin))
            .where(Catalyst.event_date >= today)
            .where(Catalyst.event_date <= max_date)
        ).scalars().all()

        snapshot_cache: dict[str, list[MarketSnapshotPoint]] = {}
        btc_snapshots = _snapshot_points_for_symbol(session, "BTC", snapshot_cache)
        eth_snapshots = _snapshot_points_for_symbol(session, "ETH", snapshot_cache)

        history_cache: dict[str, list[DailyPricePoint]] = {}
        btc_history = _daily_price_points_for_symbol(session, "BTC", history_cache)
        eth_history = _daily_price_points_for_symbol(session, "ETH", history_cache)

        rows = []
        for catalyst in catalysts:
            days_until = days_until_event(catalyst.event_date, as_of=today)
            catalyst_score = calculate_catalyst_score(
                event_type=catalyst.event_type,
                source_credibility=catalyst.source_credibility,
                days_until=days_until,
                confidence_score=catalyst.confidence_score,
                window_days=window_days,
            )

            coin_history = _daily_price_points_for_symbol(session, catalyst.coin.symbol, history_cache)

            if coin_history:
                history_metrics = calculate_market_reaction_from_history(
                    coin_history, btc_history, eth_history
                )
                compat_metrics = MarketReactionMetrics(
                    return_7d_pct=history_metrics.return_7d_pct,
                    return_14d_pct=history_metrics.return_14d_pct,
                    return_30d_pct=history_metrics.return_30d_pct,
                    volume_change_pct=None,
                    btc_relative_return_pct=history_metrics.btc_adjusted_return_30d_pct,
                    eth_relative_return_pct=history_metrics.eth_adjusted_return_30d_pct,
                )
                priced_in_penalty = calculate_priced_in_penalty(
                    compat_metrics,
                    days_until_event=days_until,
                    volume_z_score=history_metrics.volume_z_score,
                    volatility_ratio=history_metrics.volatility_ratio,
                )
                adjusted_score = adjusted_opportunity_score(catalyst_score, priced_in_penalty)
                row = _history_row(catalyst, days_until, catalyst_score, priced_in_penalty, adjusted_score, history_metrics)
            else:
                coin_snapshots = _snapshot_points_for_symbol(session, catalyst.coin.symbol, snapshot_cache)
                reaction = calculate_market_reaction(
                    coin_snapshots=coin_snapshots,
                    btc_snapshots=btc_snapshots,
                    eth_snapshots=eth_snapshots,
                )
                priced_in_penalty = calculate_priced_in_penalty(reaction, days_until_event=days_until)
                adjusted_score = adjusted_opportunity_score(catalyst_score, priced_in_penalty)
                row = _snapshot_row(catalyst, days_until, catalyst_score, priced_in_penalty, adjusted_score, reaction)

            rows.append(row)

    return sorted(rows, key=lambda row: (-float(row["adjusted_score"]), str(row["event_date"])))


def export_ranked_catalysts(settings: Settings, days: int | None = None, output: str | None = None) -> Path:
    output_path = Path(output) if output else settings.output_dir / "ranked_catalysts.csv"
    if not output_path.is_absolute():
        output_path = Path.cwd() / output_path
    output_path.parent.mkdir(parents=True, exist_ok=True)

    frame = pd.DataFrame(rank_catalyst_rows(settings, days=days), columns=CSV_COLUMNS)
    frame.to_csv(output_path, index=False)
    return output_path


# ─── Row builders ──────────────────────────────────────────────────────────────

def _history_row(
    catalyst,
    days_until: int,
    catalyst_score: float,
    priced_in_penalty: float,
    adjusted_score: float,
    hrm: HistoryReactionMetrics,
) -> dict[str, object]:
    return {
        "symbol": catalyst.coin.symbol,
        "project_name": catalyst.coin.name,
        "catalyst_id": catalyst.id,
        "event_type": catalyst.event_type,
        "event_date": catalyst.event_date.isoformat(),
        "days_until_event": days_until,
        "description": catalyst.description,
        "source_url": catalyst.source_url,
        "confidence_score": round(catalyst.confidence_score, 4),
        "catalyst_score": catalyst_score,
        # Returns
        "return_1d_pct": _round_optional(hrm.return_1d_pct),
        "return_3d_pct": _round_optional(hrm.return_3d_pct),
        "return_7d_pct": _round_optional(hrm.return_7d_pct),
        "return_14d_pct": _round_optional(hrm.return_14d_pct),
        "return_30d_pct": _round_optional(hrm.return_30d_pct),
        # Benchmark-adjusted (new names)
        "btc_adjusted_return_7d_pct": _round_optional(hrm.btc_adjusted_return_7d_pct),
        "btc_adjusted_return_30d_pct": _round_optional(hrm.btc_adjusted_return_30d_pct),
        "eth_adjusted_return_7d_pct": _round_optional(hrm.eth_adjusted_return_7d_pct),
        "eth_adjusted_return_30d_pct": _round_optional(hrm.eth_adjusted_return_30d_pct),
        # Legacy names (dashboard compat)
        "volume_change_pct": None,
        "btc_relative_return_pct": _round_optional(hrm.btc_adjusted_return_30d_pct),
        "eth_relative_return_pct": _round_optional(hrm.eth_adjusted_return_30d_pct),
        # Scoring
        "priced_in_penalty": priced_in_penalty,
        "adjusted_score": adjusted_score,
        # Quant (new names)
        "volume_z_score": _round_optional(hrm.volume_z_score),
        "realized_vol_7d_pct": _round_optional(hrm.realized_vol_7d_pct),
        "realized_vol_30d_pct": _round_optional(hrm.realized_vol_30d_pct),
        "volatility_ratio": _round_optional(hrm.volatility_ratio),
        "ma20_distance_pct": _round_optional(hrm.ma20_distance_pct),
        # Legacy quant names (dashboard compat)
        "realized_vol_7d": _round_optional(hrm.realized_vol_7d_pct),
        "realized_vol_30d": _round_optional(hrm.realized_vol_30d_pct),
        "volume_z_score_30d": _round_optional(hrm.volume_z_score),
        "ma_20d_distance_pct": _round_optional(hrm.ma20_distance_pct),
        "btc_correlation_30d": None,
    }


def _snapshot_row(
    catalyst,
    days_until: int,
    catalyst_score: float,
    priced_in_penalty: float,
    adjusted_score: float,
    reaction: MarketReactionMetrics,
) -> dict[str, object]:
    return {
        "symbol": catalyst.coin.symbol,
        "project_name": catalyst.coin.name,
        "catalyst_id": catalyst.id,
        "event_type": catalyst.event_type,
        "event_date": catalyst.event_date.isoformat(),
        "days_until_event": days_until,
        "description": catalyst.description,
        "source_url": catalyst.source_url,
        "confidence_score": round(catalyst.confidence_score, 4),
        "catalyst_score": catalyst_score,
        # Returns
        "return_1d_pct": None,
        "return_3d_pct": None,
        "return_7d_pct": _round_optional(reaction.return_7d_pct),
        "return_14d_pct": _round_optional(reaction.return_14d_pct),
        "return_30d_pct": _round_optional(reaction.return_30d_pct),
        # Benchmark-adjusted (new names — None for snapshot path)
        "btc_adjusted_return_7d_pct": None,
        "btc_adjusted_return_30d_pct": _round_optional(reaction.btc_relative_return_pct),
        "eth_adjusted_return_7d_pct": None,
        "eth_adjusted_return_30d_pct": _round_optional(reaction.eth_relative_return_pct),
        # Legacy names
        "volume_change_pct": _round_optional(reaction.volume_change_pct),
        "btc_relative_return_pct": _round_optional(reaction.btc_relative_return_pct),
        "eth_relative_return_pct": _round_optional(reaction.eth_relative_return_pct),
        # Scoring
        "priced_in_penalty": priced_in_penalty,
        "adjusted_score": adjusted_score,
        # Quant — all None (no daily history)
        "volume_z_score": None,
        "realized_vol_7d_pct": None,
        "realized_vol_30d_pct": None,
        "volatility_ratio": None,
        "ma20_distance_pct": None,
        "realized_vol_7d": None,
        "realized_vol_30d": None,
        "volume_z_score_30d": None,
        "ma_20d_distance_pct": None,
        "btc_correlation_30d": None,
    }


# ─── DB helpers ────────────────────────────────────────────────────────────────

def _find_coin_by_symbol(session, symbol: str) -> Coin:
    coin = session.execute(
        select(Coin)
        .where(func.upper(Coin.symbol) == symbol.upper())
        .order_by(Coin.cmc_rank.is_(None), Coin.cmc_rank)
    ).scalars().first()

    if coin is None:
        raise ValueError(f"No coin found for symbol {symbol.upper()}. Update the coin universe first.")

    return coin


def _snapshot_points_for_symbol(
    session,
    symbol: str,
    snapshot_cache: dict[str, list[MarketSnapshotPoint]],
) -> list[MarketSnapshotPoint]:
    normalized_symbol = symbol.upper()
    if normalized_symbol in snapshot_cache:
        return snapshot_cache[normalized_symbol]

    snapshots = session.execute(
        select(MarketSnapshot)
        .where(func.upper(MarketSnapshot.symbol) == normalized_symbol)
        .order_by(MarketSnapshot.timestamp)
    ).scalars().all()

    points = [
        MarketSnapshotPoint(
            timestamp=snapshot.timestamp,
            price_usd=snapshot.price_usd,
            volume_24h_usd=snapshot.volume_24h_usd,
        )
        for snapshot in snapshots
    ]
    snapshot_cache[normalized_symbol] = points
    return points


def _daily_price_points_for_symbol(
    session,
    symbol: str,
    cache: dict[str, list[DailyPricePoint]],
) -> list[DailyPricePoint]:
    normalized = symbol.upper()
    if normalized in cache:
        return cache[normalized]

    rows = session.execute(
        select(PriceHistory)
        .where(func.upper(PriceHistory.symbol) == normalized)
        .order_by(PriceHistory.date)
    ).scalars().all()

    points = [
        DailyPricePoint(
            date=row.date,
            price_usd=row.price_usd,
            volume_24h_usd=row.volume_24h_usd,
        )
        for row in rows
    ]
    cache[normalized] = points
    return points


def _latest_snapshots_by_symbol(session) -> dict[str, MarketSnapshot]:
    snapshots = session.execute(
        select(MarketSnapshot).order_by(MarketSnapshot.timestamp)
    ).scalars().all()

    latest_by_symbol: dict[str, MarketSnapshot] = {}
    for snapshot in snapshots:
        latest_by_symbol[snapshot.symbol.upper()] = snapshot
    return latest_by_symbol


def price_history_status(settings: Settings) -> dict[str, object]:
    ensure_database(settings)
    with session_scope(settings.database_url) as session:
        coins_with_history = session.scalar(
            select(func.count(func.distinct(PriceHistory.symbol)))
        ) or 0
        last_backfilled = session.scalar(
            select(func.max(PriceHistory.date))
        )
        total_rows = session.scalar(
            select(func.count(PriceHistory.id))
        ) or 0
    return {
        "api_key_configured": bool(settings.coingecko_api_key),
        "coins_with_history": coins_with_history,
        "last_backfilled": last_backfilled,
        "total_rows": total_rows,
    }


def _round_optional(value: float | None) -> float | None:
    if value is None:
        return None
    return round(value, 4)
