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
from src.scoring.catalyst_score import calculate_catalyst_score, days_until_event, estimate_source_credibility
from src.scoring.market_reaction import (
    MarketSnapshotPoint,
    adjusted_opportunity_score,
    calculate_market_reaction,
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
    "return_7d_pct",
    "return_14d_pct",
    "return_30d_pct",
    "volume_change_pct",
    "btc_relative_return_pct",
    "eth_relative_return_pct",
    "priced_in_penalty",
    "adjusted_score",
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

        return [
            {
                "symbol": coin.symbol,
                "name": coin.name,
                "rank": coin.cmc_rank,
                "market_cap_usd": coin.market_cap_usd,
                "price_usd": coin.price_usd,
            }
            for coin in coins
        ]


def market_overview(settings: Settings) -> dict[str, object]:
    ensure_database(settings)

    with session_scope(settings.database_url) as session:
        tracked_coin_count = session.scalar(select(func.count(Coin.id))) or 0
        latest_snapshots = _latest_snapshots_by_symbol(session)
        latest_timestamp = max((snapshot.timestamp for snapshot in latest_snapshots.values()), default=None)
        total_market_cap = sum(snapshot.market_cap_usd or 0 for snapshot in latest_snapshots.values()) if latest_snapshots else None
        total_volume = sum(snapshot.volume_24h_usd or 0 for snapshot in latest_snapshots.values()) if latest_snapshots else None
        snapshot_cache: dict[str, list[MarketSnapshotPoint]] = {}
        btc_reaction = calculate_market_reaction(_snapshot_points_for_symbol(session, "BTC", snapshot_cache))
        eth_reaction = calculate_market_reaction(_snapshot_points_for_symbol(session, "ETH", snapshot_cache))

    return {
        "total_tracked_market_cap": total_market_cap,
        "total_tracked_volume_24h": total_volume,
        "tracked_coin_count": tracked_coin_count,
        "latest_snapshot_timestamp": latest_timestamp.isoformat() if latest_timestamp else None,
        "btc_return_7d_pct": _round_optional(btc_reaction.return_7d_pct),
        "eth_return_7d_pct": _round_optional(eth_reaction.return_7d_pct),
    }


def top_market_cap_snapshot_rows(settings: Settings, limit: int = 10) -> list[dict[str, object]]:
    ensure_database(settings)

    with session_scope(settings.database_url) as session:
        latest_snapshots = _latest_snapshots_by_symbol(session)
        ranked = sorted(
            latest_snapshots.values(),
            key=lambda snapshot: snapshot.market_cap_usd or 0,
            reverse=True,
        )
        return [
            {
                "symbol": snapshot.symbol,
                "market_cap_usd": snapshot.market_cap_usd,
            }
            for snapshot in ranked[:limit]
        ]


def market_timeseries(settings: Settings, limit_points: int = 48) -> list[dict[str, object]]:
    ensure_database(settings)

    with session_scope(settings.database_url) as session:
        snapshots = session.execute(
            select(MarketSnapshot).order_by(MarketSnapshot.timestamp)
        ).scalars().all()

        grouped: dict[str, dict[str, object]] = {}
        for snapshot in snapshots:
            timestamp_key = snapshot.timestamp.isoformat()
            if timestamp_key not in grouped:
                grouped[timestamp_key] = {
                    "timestamp": timestamp_key,
                    "total_market_cap": 0.0,
                    "total_volume_24h": 0.0,
                }

            grouped[timestamp_key]["total_market_cap"] = float(grouped[timestamp_key]["total_market_cap"]) + (
                snapshot.market_cap_usd or 0
            )
            grouped[timestamp_key]["total_volume_24h"] = float(grouped[timestamp_key]["total_volume_24h"]) + (
                snapshot.volume_24h_usd or 0
            )

    return list(grouped.values())[-limit_points:]


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
            coin_snapshots = _snapshot_points_for_symbol(session, catalyst.coin.symbol, snapshot_cache)
            reaction = calculate_market_reaction(
                coin_snapshots=coin_snapshots,
                btc_snapshots=btc_snapshots,
                eth_snapshots=eth_snapshots,
            )
            priced_in_penalty = calculate_priced_in_penalty(reaction, days_until_event=days_until)
            adjusted_score = adjusted_opportunity_score(catalyst_score, priced_in_penalty)
            rows.append(
                {
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
                    "return_7d_pct": _round_optional(reaction.return_7d_pct),
                    "return_14d_pct": _round_optional(reaction.return_14d_pct),
                    "return_30d_pct": _round_optional(reaction.return_30d_pct),
                    "volume_change_pct": _round_optional(reaction.volume_change_pct),
                    "btc_relative_return_pct": _round_optional(reaction.btc_relative_return_pct),
                    "eth_relative_return_pct": _round_optional(reaction.eth_relative_return_pct),
                    "priced_in_penalty": priced_in_penalty,
                    "adjusted_score": adjusted_score,
                }
            )

    return sorted(rows, key=lambda row: (-float(row["adjusted_score"]), str(row["event_date"])))


def export_ranked_catalysts(settings: Settings, days: int | None = None, output: str | None = None) -> Path:
    output_path = Path(output) if output else settings.output_dir / "ranked_catalysts.csv"
    if not output_path.is_absolute():
        output_path = Path.cwd() / output_path
    output_path.parent.mkdir(parents=True, exist_ok=True)

    frame = pd.DataFrame(rank_catalyst_rows(settings, days=days), columns=CSV_COLUMNS)
    frame.to_csv(output_path, index=False)
    return output_path


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


def _latest_snapshots_by_symbol(session) -> dict[str, MarketSnapshot]:
    snapshots = session.execute(
        select(MarketSnapshot).order_by(MarketSnapshot.timestamp)
    ).scalars().all()

    latest_by_symbol: dict[str, MarketSnapshot] = {}
    for snapshot in snapshots:
        latest_by_symbol[snapshot.symbol.upper()] = snapshot
    return latest_by_symbol


def _round_optional(value: float | None) -> float | None:
    if value is None:
        return None
    return round(value, 4)
