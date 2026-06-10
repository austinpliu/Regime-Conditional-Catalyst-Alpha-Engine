from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from scripts.seed_market_snapshots import seed_market_snapshots
from src.config import Settings
from src.data.cmc_client import CoinMarketData
from src.models.coin import Coin
from src.models.market_snapshot import MarketSnapshot
from src.services.catalyst_service import save_market_snapshot
from src.storage.db import init_db, session_scope


def test_save_market_snapshot_persists_required_fields(tmp_path) -> None:
    settings = make_test_settings(tmp_path)
    init_db(settings.database_url)
    timestamp = datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc)
    coin_data = CoinMarketData(
        cmc_id=5426,
        name="Solana",
        symbol="sol",
        slug="solana",
        price_usd=180,
        volume_24h_usd=10_000_000_000,
        market_cap_usd=104_000_000_000,
    )

    with session_scope(settings.database_url) as session:
        save_market_snapshot(session, coin_data, timestamp=timestamp)

    with session_scope(settings.database_url) as session:
        snapshot = session.execute(select(MarketSnapshot)).scalar_one()
        assert snapshot.symbol == "SOL"
        assert snapshot.price_usd == 180
        assert snapshot.volume_24h_usd == 10_000_000_000
        assert snapshot.market_cap_usd == 104_000_000_000
        assert snapshot.timestamp.replace(tzinfo=timezone.utc) == timestamp


def test_seed_market_snapshots_inserts_dev_history_for_existing_symbols(tmp_path) -> None:
    settings = make_test_settings(tmp_path)
    init_db(settings.database_url)
    seed_coins(settings)
    as_of = datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc)

    result = seed_market_snapshots(settings=settings, as_of=as_of)

    with session_scope(settings.database_url) as session:
        snapshots = session.execute(select(MarketSnapshot).order_by(MarketSnapshot.symbol, MarketSnapshot.timestamp)).scalars().all()
        sol_snapshots = [snapshot for snapshot in snapshots if snapshot.symbol == "SOL"]

        assert result.inserted_count == 12
        assert result.skipped_symbols == []
        assert len(snapshots) == 12
        assert [snapshot.price_usd for snapshot in sol_snapshots] == [120, 145, 160, 180]
        assert sol_snapshots[-1].volume_24h_usd == 10_000_000_000


def make_test_settings(tmp_path) -> Settings:
    return Settings(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        output_dir=tmp_path,
        cmc_api_key="",
    )


def seed_coins(settings: Settings) -> None:
    with session_scope(settings.database_url) as session:
        for index, symbol in enumerate(["BTC", "ETH", "SOL"], start=1):
            session.add(
                Coin(
                    cmc_id=index,
                    name=symbol,
                    symbol=symbol,
                    slug=symbol.lower(),
                    cmc_rank=index,
                )
            )
