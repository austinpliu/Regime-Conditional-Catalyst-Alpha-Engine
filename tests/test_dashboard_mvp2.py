from datetime import date, timedelta

import pytest

from src.config import Settings
from src.dashboard import normalize_optional_score_input, normalize_score_input, render_dashboard
from src.models.catalyst import Catalyst
from src.models.coin import Coin
from src.services.catalyst_service import CSV_COLUMNS, market_overview, rank_catalyst_rows, update_catalyst
from src.storage.db import init_db, session_scope


def test_confidence_score_normalization_clamps_dashboard_input() -> None:
    assert normalize_score_input("80") == pytest.approx(0.8)
    assert normalize_score_input("0") == 0
    assert normalize_score_input("100") == 1
    assert normalize_score_input("-10") == 0
    assert normalize_score_input("150") == 1
    assert normalize_score_input("not-a-number") == 0
    assert normalize_optional_score_input("") is None
    assert normalize_optional_score_input("125") == 1


def test_catalyst_edit_updates_existing_row_without_duplicate(tmp_path) -> None:
    settings = make_test_settings(tmp_path)
    coin_id = seed_coin_and_catalyst(settings)

    result = update_catalyst(
        settings=settings,
        catalyst_id=1,
        symbol="ETH",
        event_type="partnership",
        event_date=date.today() + timedelta(days=20),
        description="Updated catalyst description",
        source_url="https://example.com/updated",
        confidence_score=0.9,
        source_credibility=0.7,
    )

    with session_scope(settings.database_url) as session:
        catalysts = session.query(Catalyst).all()
        updated = catalysts[0]
        assert len(catalysts) == 1
        assert updated.coin_id == coin_id
        assert updated.event_type == "partnership"
        assert updated.description == "Updated catalyst description"
        assert updated.source_url == "https://example.com/updated"
        assert updated.confidence_score == pytest.approx(0.9)
        assert updated.source_credibility == pytest.approx(0.7)
        assert result.catalyst_id == updated.id


def test_missing_mvp2_market_data_does_not_crash_dashboard_rendering(tmp_path, monkeypatch) -> None:
    settings = make_test_settings(tmp_path)
    init_db(settings.database_url)
    monkeypatch.setattr("src.dashboard.get_settings", lambda: settings)

    html = render_dashboard({})

    assert "Market overview" in html
    assert "N/A" in html
    assert "No upcoming catalysts yet" in html


def test_ranked_catalyst_rows_include_priced_in_fields(tmp_path) -> None:
    settings = make_test_settings(tmp_path)
    seed_coin_and_catalyst(settings)

    rows = rank_catalyst_rows(settings)

    assert len(rows) == 1
    assert "adjusted_score" in rows[0]
    assert "priced_in_penalty" in rows[0]
    assert rows[0]["adjusted_score"] <= rows[0]["catalyst_score"]


def test_dashboard_ranked_table_shows_mvp2_priced_in_columns(tmp_path, monkeypatch) -> None:
    settings = make_test_settings(tmp_path)
    seed_coin_and_catalyst(settings)
    monkeypatch.setattr("src.dashboard.get_settings", lambda: settings)

    html = render_dashboard({})

    for label in [
        "Cat. Score",
        "7D Return",
        "14D Return",
        "30D Return",
        "VOL Z",
        "vs BTC",
        "vs ETH",
        "Priced-In",
        "Adj. Score",
    ]:
        assert label in html


def test_csv_columns_include_mvp2_priced_in_fields() -> None:
    for column in [
        "catalyst_score",
        "return_7d_pct",
        "return_14d_pct",
        "return_30d_pct",
        "volume_change_pct",
        "btc_relative_return_pct",
        "eth_relative_return_pct",
        "priced_in_penalty",
        "adjusted_score",
    ]:
        assert column in CSV_COLUMNS


def test_market_overview_handles_empty_snapshot_data_safely(tmp_path) -> None:
    settings = make_test_settings(tmp_path)
    init_db(settings.database_url)

    overview = market_overview(settings)

    assert overview["total_tracked_market_cap"] is None
    assert overview["total_tracked_volume_24h"] is None
    assert overview["latest_snapshot_timestamp"] is None
    assert overview["btc_return_7d_pct"] is None
    assert overview["eth_return_7d_pct"] is None


def make_test_settings(tmp_path) -> Settings:
    return Settings(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        output_dir=tmp_path,
        cmc_api_key="",
    )


def seed_coin_and_catalyst(settings: Settings) -> int:
    init_db(settings.database_url)
    with session_scope(settings.database_url) as session:
        coin = Coin(
            cmc_id=1027,
            name="Ethereum",
            symbol="ETH",
            slug="ethereum",
            cmc_rank=2,
            price_usd=3000,
            market_cap_usd=360_000_000_000,
            volume_24h_usd=12_000_000_000,
        )
        session.add(coin)
        session.flush()
        session.add(
            Catalyst(
                coin_id=coin.id,
                event_type="mainnet_upgrade",
                event_date=date.today() + timedelta(days=30),
                description="Initial catalyst description",
                source_url="https://example.com/source",
                confidence_score=0.8,
                source_credibility=0.6,
            )
        )
        return coin.id
