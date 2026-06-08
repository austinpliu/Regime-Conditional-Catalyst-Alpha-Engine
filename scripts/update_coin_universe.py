from __future__ import annotations

from pathlib import Path
import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))

from src.config import get_settings
from src.services.catalyst_service import update_coin_universe as update_coin_universe_service
from src.utils.logging import configure_logging


def update_coin_universe(limit: int | None = None) -> None:
    configure_logging()
    settings = get_settings()
    fetch_limit = limit or settings.cmc_limit

    count = update_coin_universe_service(settings, limit=fetch_limit)
    print(f"Upserted {count} coins into {settings.database_url}.")


if __name__ == "__main__":
    update_coin_universe()
