from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from src.config import Settings, get_settings
from src.data.coingecko_client import REQUEST_DELAY_SECONDS, CoinGeckoClient
from src.models.coin import Coin
from src.models.price_history import PriceHistory
from src.storage.db import init_db, session_scope


@dataclass
class BackfillResult:
    inserted_count: int = 0
    resolved: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)


def backfill_price_history(
    settings: Settings,
    days: int = 90,
    symbols: list[str] | None = None,
) -> BackfillResult:
    init_db(settings.database_url)
    client = CoinGeckoClient()
    result = BackfillResult()

    with session_scope(settings.database_url) as session:
        query = select(Coin.symbol, Coin.slug).order_by(Coin.cmc_rank.is_(None), Coin.cmc_rank)
        if symbols:
            normalized = [s.upper() for s in symbols]
            query = query.where(Coin.symbol.in_(normalized))
        coin_rows = session.execute(query).all()

    for symbol, slug in coin_rows:
        symbol = symbol.upper()
        try:
            history = client.fetch_daily_history(slug, days=days)
        except requests.HTTPError as exc:
            result.failed.append(symbol)
            code = exc.response.status_code if exc.response is not None else "?"
            print(f"  SKIP {symbol}: CoinGecko ID '{slug}' returned HTTP {code}", flush=True)
            continue
        except Exception as exc:
            result.failed.append(symbol)
            print(f"  FAIL {symbol}: {exc}", flush=True)
            continue

        inserted = _upsert_history(settings, symbol, history)
        result.inserted_count += inserted
        result.resolved.append(symbol)
        print(f"  {symbol}: {inserted} rows inserted", flush=True)
        time.sleep(REQUEST_DELAY_SECONDS)

    return result


def _upsert_history(settings: Settings, symbol: str, history) -> int:
    inserted = 0
    with session_scope(settings.database_url) as session:
        existing_dates = set(
            session.execute(
                select(PriceHistory.date).where(PriceHistory.symbol == symbol)
            ).scalars().all()
        )
        for point in history:
            if point.date in existing_dates:
                continue
            session.add(PriceHistory(
                symbol=symbol,
                date=point.date,
                close_usd=point.close_usd,
                volume_usd=point.volume_usd,
                market_cap_usd=point.market_cap_usd,
            ))
            inserted += 1
    return inserted


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill price history from CoinGecko")
    parser.add_argument("--days", type=int, default=90, help="Days of history to fetch (default: 90)")
    parser.add_argument("--symbols", nargs="*", help="Only backfill these symbols (default: all coins in DB)")
    args = parser.parse_args()

    settings = get_settings()
    target = ", ".join(args.symbols) if args.symbols else "all coins"
    print(f"Backfilling {args.days}d price history for {target}...")

    result = backfill_price_history(settings, days=args.days, symbols=args.symbols)

    print(f"\nDone: {result.inserted_count} rows inserted across {len(result.resolved)} coins")
    if result.failed:
        print(f"Failed ({len(result.failed)}): {', '.join(result.failed)}")
        print("  → CMC slug may not match CoinGecko ID for these coins")


if __name__ == "__main__":
    main()
