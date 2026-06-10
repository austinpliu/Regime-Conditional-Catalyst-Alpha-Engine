from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone

import requests

COINGECKO_BASE = "https://api.coingecko.com/api/v3"
REQUEST_DELAY_SECONDS = 1.5


@dataclass(frozen=True)
class DailyOHLCV:
    date: date
    close_usd: float | None
    volume_usd: float | None
    market_cap_usd: float | None


class CoinGeckoClient:
    def __init__(self, session: requests.Session | None = None) -> None:
        self._session = session or requests.Session()
        self._session.headers.update({"Accept": "application/json"})

    def fetch_daily_history(self, coingecko_id: str, days: int = 90) -> list[DailyOHLCV]:
        response = self._session.get(
            f"{COINGECKO_BASE}/coins/{coingecko_id}/market_chart",
            params={"vs_currency": "usd", "days": days, "interval": "daily"},
            timeout=20,
        )
        response.raise_for_status()
        data = response.json()

        prices_by_date: dict[date, float] = {
            _ts_ms_to_date(ts): price for ts, price in data.get("prices", [])
        }
        volumes_by_date: dict[date, float] = {
            _ts_ms_to_date(ts): vol for ts, vol in data.get("total_volumes", [])
        }
        market_caps_by_date: dict[date, float] = {
            _ts_ms_to_date(ts): mc for ts, mc in data.get("market_caps", [])
        }

        all_dates = sorted(set(prices_by_date) | set(volumes_by_date) | set(market_caps_by_date))
        return [
            DailyOHLCV(
                date=d,
                close_usd=prices_by_date.get(d),
                volume_usd=volumes_by_date.get(d),
                market_cap_usd=market_caps_by_date.get(d),
            )
            for d in all_dates
        ]


def _ts_ms_to_date(ts_ms: int | float) -> date:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).date()
