from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select

from src.config import get_settings, Settings
from src.data.coingecko_client import CoinGeckoClient, CoinMarketEntry, DailyOHLCV, resolve_symbol_to_id
from src.models.coin import Coin
from src.models.price_history import PriceHistory
from src.storage.db import init_db, session_scope


@dataclass
class BackfillResult:
    upserted_count: int = 0
    skipped_count: int = 0
    resolved: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)


def backfill_price_history(
    settings: Settings,
    days: int | None = None,
    symbols: list[str] | None = None,
    refresh: bool = False,
) -> BackfillResult:
    effective_days = 3 if refresh else (days or settings.price_history_days)
    init_db(settings.database_url)

    client = CoinGeckoClient(
        base_url=settings.coingecko_base_url,
        api_key=settings.coingecko_api_key,
        request_delay_seconds=settings.coingecko_request_delay_seconds,
    )

    print("Fetching CoinGecko market list for symbol → ID mapping…", flush=True)
    try:
        markets = client.fetch_top_markets(per_page=250, pages=2)
        time.sleep(settings.coingecko_request_delay_seconds)
    except Exception as exc:
        print(f"ERROR: Could not fetch CoinGecko market list: {exc}", flush=True)
        return BackfillResult()

    with session_scope(settings.database_url) as session:
        query = select(Coin.symbol).order_by(Coin.cmc_rank.is_(None), Coin.cmc_rank)
        if symbols:
            normalized = [s.upper() for s in symbols]
            query = query.where(Coin.symbol.in_(normalized))
        coin_symbols: list[str] = [row[0] for row in session.execute(query).all()]

    result = BackfillResult()
    total = len(coin_symbols)

    for idx, symbol in enumerate(coin_symbols, start=1):
        coingecko_id = resolve_symbol_to_id(symbol, markets)
        if coingecko_id is None:
            print(f"  {idx}/{total} {symbol}: WARNING — no CoinGecko mapping found, skipping", flush=True)
            result.failed.append(symbol)
            continue

        if not refresh:
            latest_date = _latest_stored_date(settings, symbol)
            today = date.today()
            if latest_date is not None and latest_date >= today - timedelta(days=1):
                print(f"  {idx}/{total} {symbol}: already current (latest {latest_date}), skipping", flush=True)
                result.skipped_count += 1
                continue

        try:
            history = client.fetch_market_chart(coingecko_id, days=effective_days)
        except requests.HTTPError as exc:
            code = exc.response.status_code if exc.response is not None else "?"
            print(f"  {idx}/{total} {symbol}: SKIP — CoinGecko ID '{coingecko_id}' HTTP {code}", flush=True)
            result.failed.append(symbol)
            time.sleep(settings.coingecko_request_delay_seconds)
            continue
        except Exception as exc:
            print(f"  {idx}/{total} {symbol}: FAIL — {exc}", flush=True)
            result.failed.append(symbol)
            time.sleep(settings.coingecko_request_delay_seconds)
            continue

        upserted = _upsert_coin_history(settings, symbol, coingecko_id, history)
        result.upserted_count += upserted
        result.resolved.append(symbol)
        print(f"  {idx}/{total} {symbol}: {upserted} rows upserted", flush=True)
        time.sleep(settings.coingecko_request_delay_seconds)

    return result


def _latest_stored_date(settings: Settings, symbol: str) -> date | None:
    with session_scope(settings.database_url) as session:
        return session.scalar(
            select(func.max(PriceHistory.date)).where(PriceHistory.symbol == symbol.upper())
        )


def _upsert_coin_history(
    settings: Settings,
    symbol: str,
    coingecko_id: str,
    history: list[DailyOHLCV],
) -> int:
    upserted = 0
    with session_scope(settings.database_url) as session:
        existing_rows = session.execute(
            select(PriceHistory)
            .where(PriceHistory.symbol == symbol.upper())
        ).scalars().all()
        existing_map: dict[date, PriceHistory] = {row.date: row for row in existing_rows}

        for point in history:
            if point.date in existing_map:
                row = existing_map[point.date]
                row.price_usd = point.price_usd
                row.volume_24h_usd = point.volume_24h_usd
                row.market_cap_usd = point.market_cap_usd
                row.coingecko_id = coingecko_id
            else:
                session.add(PriceHistory(
                    symbol=symbol.upper(),
                    coingecko_id=coingecko_id,
                    date=point.date,
                    price_usd=point.price_usd,
                    volume_24h_usd=point.volume_24h_usd,
                    market_cap_usd=point.market_cap_usd,
                ))
            upserted += 1

    return upserted


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill daily price history from CoinGecko")
    parser.add_argument(
        "--days", type=int, default=None,
        help="Days of history to fetch (default: PRICE_HISTORY_DAYS setting, 120)",
    )
    parser.add_argument(
        "--symbols", default=None,
        help="Comma-separated symbols to backfill, e.g. BTC,ETH,SOL (default: all coins in DB)",
    )
    parser.add_argument(
        "--refresh", action="store_true",
        help="Fast-refresh mode: fetch only last 3 days for all mapped coins",
    )
    args = parser.parse_args()

    settings = get_settings()
    symbol_list = [s.strip().upper() for s in args.symbols.split(",")] if args.symbols else None
    effective_days = 3 if args.refresh else (args.days or settings.price_history_days)

    target = ", ".join(symbol_list) if symbol_list else "all coins"
    mode = "refresh (3 days)" if args.refresh else f"{effective_days} days"
    print(f"Backfilling {mode} price history for {target}…")

    result = backfill_price_history(settings, days=args.days, symbols=symbol_list, refresh=args.refresh)

    print(f"\nDone: {result.upserted_count} rows upserted across {len(result.resolved)} coins")
    if result.skipped_count:
        print(f"Skipped (already current): {result.skipped_count} coins")
    if result.failed:
        print(f"Failed / unmapped ({len(result.failed)}): {', '.join(result.failed)}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted. Database is consistent, re-run to resume.", flush=True)
        raise SystemExit(130)
