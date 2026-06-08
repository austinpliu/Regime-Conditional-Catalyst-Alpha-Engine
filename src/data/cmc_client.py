from __future__ import annotations

from datetime import datetime
from typing import Any

import requests
from pydantic import BaseModel

from src.config import Settings


class CoinMarketData(BaseModel):
    cmc_id: int
    name: str
    symbol: str
    slug: str
    cmc_rank: int | None = None
    price_usd: float | None = None
    market_cap_usd: float | None = None
    volume_24h_usd: float | None = None
    last_updated: datetime | None = None


class CoinMarketCapClient:
    def __init__(self, settings: Settings, session: requests.Session | None = None) -> None:
        if not settings.cmc_api_key:
            raise ValueError("CMC_API_KEY is required to fetch CoinMarketCap data.")

        self.base_url = settings.cmc_base_url.rstrip("/")
        self.listings_endpoint = settings.cmc_listings_endpoint
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/json",
                "X-CMC_PRO_API_KEY": settings.cmc_api_key,
            }
        )

    def get_top_assets(self, limit: int = 200, convert: str = "USD") -> list[CoinMarketData]:
        response = self.session.get(
            f"{self.base_url}{self.listings_endpoint}",
            params={
                "start": 1,
                "limit": limit,
                "convert": convert,
                "sort": "market_cap",
            },
            timeout=30,
        )
        self._raise_for_cmc_error(response)

        payload = response.json()
        data = payload.get("data", [])
        if not isinstance(data, list):
            raise ValueError("Unexpected CoinMarketCap response: expected 'data' to be a list.")

        return [self._parse_coin(item, convert=convert) for item in data]

    @staticmethod
    def _raise_for_cmc_error(response: requests.Response) -> None:
        try:
            payload = response.json()
        except ValueError:
            payload = {}

        status = payload.get("status", {}) if isinstance(payload, dict) else {}
        error_message = status.get("error_message") if isinstance(status, dict) else None

        if response.ok and not error_message:
            return

        detail = error_message or response.text
        raise requests.HTTPError(f"CoinMarketCap request failed: {detail}", response=response)

    @classmethod
    def _parse_coin(cls, item: dict[str, Any], convert: str) -> CoinMarketData:
        quote = cls._quote_for_currency(item.get("quote", {}), convert)
        last_updated_raw = quote.get("last_updated") or item.get("last_updated")

        return CoinMarketData(
            cmc_id=item["id"],
            name=item["name"],
            symbol=item["symbol"],
            slug=item["slug"],
            cmc_rank=item.get("cmc_rank"),
            price_usd=quote.get("price"),
            market_cap_usd=quote.get("market_cap"),
            volume_24h_usd=quote.get("volume_24h"),
            last_updated=cls._parse_datetime(last_updated_raw),
        )

    @staticmethod
    def _quote_for_currency(quote: Any, convert: str) -> dict[str, Any]:
        if not isinstance(quote, dict):
            return {}

        if convert in quote and isinstance(quote[convert], dict):
            return quote[convert]

        for value in quote.values():
            if isinstance(value, dict):
                return value

        return {}

    @staticmethod
    def _parse_datetime(raw_value: str | None) -> datetime | None:
        if not raw_value:
            return None
        return datetime.fromisoformat(raw_value.replace("Z", "+00:00"))
