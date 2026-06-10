from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import select

from src.data.coingecko_client import CoinMarketEntry, resolve_symbol_to_id
from src.models.price_history import PriceHistory
from src.storage.db import init_db, session_scope


@pytest.fixture()
def db_url(tmp_path: Path) -> str:
    url = f"sqlite:///{tmp_path}/test_mapping.db"
    init_db(url)
    return url


# ─── Symbol collision resolution ──────────────────────────────────────────────

def test_prefers_lowest_market_cap_rank() -> None:
    markets = [
        CoinMarketEntry("fake-eth", "ETH", "Fake ETH", market_cap_rank=500),
        CoinMarketEntry("ethereum", "ETH", "Ethereum", market_cap_rank=2),
        CoinMarketEntry("another-eth", "ETH", "Another ETH", market_cap_rank=300),
    ]
    assert resolve_symbol_to_id("ETH", markets) == "ethereum"


def test_case_insensitive_symbol_match() -> None:
    markets = [CoinMarketEntry("bitcoin", "BTC", "Bitcoin", market_cap_rank=1)]
    assert resolve_symbol_to_id("btc", markets) == "bitcoin"
    assert resolve_symbol_to_id("BTC", markets) == "bitcoin"
    assert resolve_symbol_to_id("Btc", markets) == "bitcoin"


def test_returns_none_for_unmapped_symbol() -> None:
    markets = [CoinMarketEntry("bitcoin", "BTC", "Bitcoin", market_cap_rank=1)]
    assert resolve_symbol_to_id("XYZ", markets) is None


def test_returns_none_for_empty_market_list() -> None:
    assert resolve_symbol_to_id("BTC", []) is None


def test_falls_back_to_first_when_all_unranked() -> None:
    markets = [
        CoinMarketEntry("fake-eth", "ETH", "Fake ETH", market_cap_rank=None),
        CoinMarketEntry("ethereum", "ETH", "Ethereum", market_cap_rank=None),
    ]
    result = resolve_symbol_to_id("ETH", markets)
    assert result == "fake-eth"  # first candidate in list


def test_ranked_candidates_win_over_unranked() -> None:
    markets = [
        CoinMarketEntry("unranked-btc", "BTC", "Unranked BTC", market_cap_rank=None),
        CoinMarketEntry("bitcoin", "BTC", "Bitcoin", market_cap_rank=1),
    ]
    assert resolve_symbol_to_id("BTC", markets) == "bitcoin"


def test_single_candidate_returned_directly() -> None:
    markets = [CoinMarketEntry("solana", "SOL", "Solana", market_cap_rank=5)]
    assert resolve_symbol_to_id("SOL", markets) == "solana"


# ─── Upsert idempotency ───────────────────────────────────────────────────────

def test_upsert_idempotency_produces_one_row(db_url: str) -> None:
    """Calling the upsert path twice for the same (symbol, date) must not duplicate rows."""

    def _upsert(price: float) -> None:
        with session_scope(db_url) as session:
            existing_rows = session.execute(
                select(PriceHistory).where(PriceHistory.symbol == "BTC")
            ).scalars().all()
            existing_map = {row.date: row for row in existing_rows}
            target_date = date(2026, 1, 1)
            if target_date in existing_map:
                existing_map[target_date].price_usd = price
            else:
                session.add(PriceHistory(
                    symbol="BTC",
                    coingecko_id="bitcoin",
                    date=target_date,
                    price_usd=price,
                    volume_24h_usd=1e9,
                    market_cap_usd=1e12,
                ))

    _upsert(50000.0)
    _upsert(51000.0)  # second call with same date — should update, not insert

    with session_scope(db_url) as session:
        rows = session.execute(select(PriceHistory).where(PriceHistory.symbol == "BTC")).scalars().all()
        assert len(rows) == 1
        assert rows[0].price_usd == pytest.approx(51000.0)


def test_unique_constraint_prevents_duplicate_insert(db_url: str) -> None:
    """Direct double-add raises an integrity error due to the unique constraint."""
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        with session_scope(db_url) as session:
            for _ in range(2):
                session.add(PriceHistory(
                    symbol="ETH",
                    coingecko_id="ethereum",
                    date=date(2026, 2, 1),
                    price_usd=3000.0,
                ))
            session.flush()


def test_different_dates_produce_separate_rows(db_url: str) -> None:
    with session_scope(db_url) as session:
        session.add(PriceHistory(symbol="SOL", coingecko_id="solana", date=date(2026, 3, 1), price_usd=150.0))
        session.add(PriceHistory(symbol="SOL", coingecko_id="solana", date=date(2026, 3, 2), price_usd=155.0))

    with session_scope(db_url) as session:
        rows = session.execute(
            select(PriceHistory).where(PriceHistory.symbol == "SOL").order_by(PriceHistory.date)
        ).scalars().all()
        assert len(rows) == 2
        assert rows[0].price_usd == pytest.approx(150.0)
        assert rows[1].price_usd == pytest.approx(155.0)
