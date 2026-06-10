from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
import sys

from sqlalchemy import func, select

sys.path.append(str(Path(__file__).resolve().parents[1]))

from src.config import Settings, get_settings
from src.models.coin import Coin
from src.models.market_snapshot import MarketSnapshot
from src.storage.db import init_db, session_scope


@dataclass(frozen=True)
class SeedResult:
    inserted_count: int
    skipped_symbols: list[str]


SEED_SNAPSHOTS = {
    "BTC": [
        {"days_ago": 30, "price_usd": 100_000, "volume_24h_usd": 30_000_000_000, "market_cap_usd": 1_950_000_000_000},
        {"days_ago": 14, "price_usd": 102_000, "volume_24h_usd": 32_000_000_000, "market_cap_usd": 1_990_000_000_000},
        {"days_ago": 7, "price_usd": 104_000, "volume_24h_usd": 34_000_000_000, "market_cap_usd": 2_030_000_000_000},
        {"days_ago": 0, "price_usd": 105_000, "volume_24h_usd": 35_000_000_000, "market_cap_usd": 2_050_000_000_000},
    ],
    "ETH": [
        {"days_ago": 30, "price_usd": 3_000, "volume_24h_usd": 12_000_000_000, "market_cap_usd": 360_000_000_000},
        {"days_ago": 14, "price_usd": 3_100, "volume_24h_usd": 12_800_000_000, "market_cap_usd": 372_000_000_000},
        {"days_ago": 7, "price_usd": 3_150, "volume_24h_usd": 13_400_000_000, "market_cap_usd": 378_000_000_000},
        {"days_ago": 0, "price_usd": 3_200, "volume_24h_usd": 14_000_000_000, "market_cap_usd": 384_000_000_000},
    ],
    "SOL": [
        {"days_ago": 30, "price_usd": 120, "volume_24h_usd": 2_800_000_000, "market_cap_usd": 70_000_000_000},
        {"days_ago": 14, "price_usd": 145, "volume_24h_usd": 3_300_000_000, "market_cap_usd": 84_000_000_000},
        {"days_ago": 7, "price_usd": 160, "volume_24h_usd": 3_200_000_000, "market_cap_usd": 93_000_000_000},
        {"days_ago": 0, "price_usd": 180, "volume_24h_usd": 10_000_000_000, "market_cap_usd": 104_000_000_000},
    ],
}


def seed_market_snapshots(
    settings: Settings | None = None,
    as_of: datetime | None = None,
) -> SeedResult:
    settings = settings or get_settings()
    seed_as_of = (as_of or datetime.now(timezone.utc)).replace(microsecond=0)
    init_db(settings.database_url)

    inserted_count = 0
    skipped_symbols: list[str] = []

    with session_scope(settings.database_url) as session:
        available_symbols = set(
            session.execute(
                select(func.upper(Coin.symbol)).where(func.upper(Coin.symbol).in_(SEED_SNAPSHOTS.keys()))
            ).scalars()
        )

        for symbol, snapshots in SEED_SNAPSHOTS.items():
            if symbol not in available_symbols:
                skipped_symbols.append(symbol)
                continue

            for snapshot in snapshots:
                session.add(
                    MarketSnapshot(
                        symbol=symbol,
                        price_usd=snapshot["price_usd"],
                        volume_24h_usd=snapshot["volume_24h_usd"],
                        market_cap_usd=snapshot["market_cap_usd"],
                        timestamp=seed_as_of - timedelta(days=snapshot["days_ago"]),
                    )
                )
                inserted_count += 1

    return SeedResult(inserted_count=inserted_count, skipped_symbols=skipped_symbols)


def main() -> None:
    result = seed_market_snapshots()
    message = f"Seeded {result.inserted_count} market snapshots for MVP 2 validation."
    if result.skipped_symbols:
        message += (
            f" Skipped missing symbols: {', '.join(result.skipped_symbols)}."
            " Run python scripts/update_coin_universe.py first if needed."
        )
    print(message)


if __name__ == "__main__":
    main()
