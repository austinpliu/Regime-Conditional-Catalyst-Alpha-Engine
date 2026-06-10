from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

import requests


@dataclass(frozen=True)
class DailyOHLCV:
    date: date
    price_usd: float | None
    volume_24h_usd: float | None
    market_cap_usd: float | None


@dataclass(frozen=True)
class CoinMarketEntry:
    coingecko_id: str
    symbol: str
    name: str
    market_cap_rank: int | None


class CoinGeckoClient:
    def __init__(
        self,
        base_url: str = "https://api.coingecko.com/api/v3",
        api_key: str = "",
        request_delay_seconds: float = 2.5,
        session: requests.Session | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._request_delay_seconds = request_delay_seconds
        self._session = session or requests.Session()
        headers: dict[str, str] = {"Accept": "application/json"}
        if api_key:
            headers["x-cg-demo-api-key"] = api_key
        self._session.headers.update(headers)

    def fetch_top_markets(
        self,
        vs_currency: str = "usd",
        per_page: int = 250,
        pages: int = 1,
    ) -> list[CoinMarketEntry]:
        results: list[CoinMarketEntry] = []
        for page in range(1, pages + 1):
            data: list[dict[str, Any]] = self._get_with_retry(
                f"{self._base_url}/coins/markets",
                params={
                    "vs_currency": vs_currency,
                    "order": "market_cap_desc",
                    "per_page": per_page,
                    "page": page,
                    "sparkline": "false",
                },
            )
            for item in data:
                results.append(
                    CoinMarketEntry(
                        coingecko_id=item["id"],
                        symbol=item["symbol"].upper(),
                        name=item["name"],
                        market_cap_rank=item.get("market_cap_rank"),
                    )
                )
            if page < pages:
                time.sleep(self._request_delay_seconds)
        return results

    def fetch_market_chart(
        self,
        coin_id: str,
        vs_currency: str = "usd",
        days: int = 120,
        interval: str = "daily",
    ) -> list[DailyOHLCV]:
        data: dict[str, Any] = self._get_with_retry(
            f"{self._base_url}/coins/{coin_id}/market_chart",
            params={"vs_currency": vs_currency, "days": days, "interval": interval},
        )
        prices_by_date: dict[date, float] = {
            _ts_ms_to_date(ts): price for ts, price in data.get("prices", [])
        }
        volumes_by_date: dict[date, float] = {
            _ts_ms_to_date(ts): vol for ts, vol in data.get("total_volumes", [])
        }
        market_caps_by_date: dict[date, float] = {
            _ts_ms_to_date(ts): mc for ts, mc in data.get("market_caps", [])
        }
        all_dates = sorted(
            set(prices_by_date) | set(volumes_by_date) | set(market_caps_by_date)
        )
        return [
            DailyOHLCV(
                date=d,
                price_usd=prices_by_date.get(d),
                volume_24h_usd=volumes_by_date.get(d),
                market_cap_usd=market_caps_by_date.get(d),
            )
            for d in all_dates
        ]

    def _get_with_retry(self, url: str, params: dict[str, Any]) -> Any:
        backoff = 10.0
        max_retries = 3
        last_response: requests.Response | None = None
        for attempt in range(max_retries + 1):
            last_response = self._session.get(url, params=params, timeout=30)
            if last_response.status_code == 429:
                if attempt >= max_retries:
                    break
                wait = float(last_response.headers.get("Retry-After", backoff))
                time.sleep(wait)
                backoff = min(backoff * 2, 80.0)
                continue
            last_response.raise_for_status()
            return last_response.json()
        if last_response is not None:
            last_response.raise_for_status()
        raise RuntimeError(f"No response received from {url}")


def resolve_symbol_to_id(symbol: str, markets: list[CoinMarketEntry]) -> str | None:
    """Return the CoinGecko ID for symbol, preferring the lowest (best) market_cap_rank."""
    candidates = [m for m in markets if m.symbol.upper() == symbol.upper()]
    if not candidates:
        return None
    ranked = [c for c in candidates if c.market_cap_rank is not None]
    if ranked:
        return min(ranked, key=lambda c: c.market_cap_rank).coingecko_id  # type: ignore[arg-type]
    return candidates[0].coingecko_id


def _ts_ms_to_date(ts_ms: int | float) -> date:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).date()
