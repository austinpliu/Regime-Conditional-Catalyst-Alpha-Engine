from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import joinedload

from src.config import Settings
from src.data.cmc_client import CoinMarketCapClient, CoinMarketData
from src.models.catalyst import Catalyst, CatalystCreate, EventType
from src.models.coin import Coin
from src.scoring.catalyst_score import calculate_catalyst_score, days_until_event, estimate_source_credibility
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

    with session_scope(settings.database_url) as session:
        for coin_data in coins:
            upsert_coin(session, coin_data)

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
        coin = session.execute(
            select(Coin)
            .where(func.upper(Coin.symbol) == catalyst_input.coin_symbol)
            .order_by(Coin.cmc_rank.is_(None), Coin.cmc_rank)
        ).scalars().first()

        if coin is None:
            raise ValueError(
                f"No coin found for symbol {catalyst_input.coin_symbol}. Update the coin universe first."
            )

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
            rows.append(
                {
                    "symbol": catalyst.coin.symbol,
                    "project_name": catalyst.coin.name,
                    "event_type": catalyst.event_type,
                    "event_date": catalyst.event_date.isoformat(),
                    "days_until_event": days_until,
                    "description": catalyst.description,
                    "source_url": catalyst.source_url,
                    "confidence_score": round(catalyst.confidence_score, 4),
                    "catalyst_score": catalyst_score,
                }
            )

    return sorted(rows, key=lambda row: (-float(row["catalyst_score"]), str(row["event_date"])))


def export_ranked_catalysts(settings: Settings, days: int | None = None, output: str | None = None) -> Path:
    output_path = Path(output) if output else settings.output_dir / "ranked_catalysts.csv"
    if not output_path.is_absolute():
        output_path = Path.cwd() / output_path
    output_path.parent.mkdir(parents=True, exist_ok=True)

    frame = pd.DataFrame(rank_catalyst_rows(settings, days=days), columns=CSV_COLUMNS)
    frame.to_csv(output_path, index=False)
    return output_path
