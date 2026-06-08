from __future__ import annotations

from datetime import date
from urllib.parse import urlparse


EVENT_TYPE_WEIGHTS: dict[str, float] = {
    "exchange_listing": 0.95,
    "mainnet_upgrade": 0.9,
    "airdrop_snapshot": 0.82,
    "partnership": 0.72,
    "token_unlock": 0.68,
    "governance_vote": 0.62,
    "roadmap_release": 0.55,
    "conference": 0.42,
    "other": 0.3,
}

COMPONENT_WEIGHTS = {
    "event_type": 0.35,
    "source_credibility": 0.25,
    "proximity": 0.25,
    "confidence": 0.15,
}

KNOWN_SOURCE_CREDIBILITY: dict[str, float] = {
    "binance.com": 0.95,
    "coinbase.com": 0.95,
    "kraken.com": 0.9,
    "okx.com": 0.9,
    "bybit.com": 0.88,
    "upbit.com": 0.88,
    "coinmarketcap.com": 0.82,
    "coindesk.com": 0.78,
    "theblock.co": 0.78,
    "messari.io": 0.76,
    "decrypt.co": 0.72,
    "github.com": 0.72,
    "medium.com": 0.55,
    "substack.com": 0.55,
    "twitter.com": 0.35,
    "x.com": 0.35,
    "reddit.com": 0.3,
}


def normalize_unit_score(value: float) -> float:
    if 0 <= value <= 1:
        return float(value)
    if 1 < value <= 100:
        return float(value) / 100
    raise ValueError("Score must be between 0 and 1, or between 1 and 100.")


def days_until_event(event_date: date, as_of: date | None = None) -> int:
    today = as_of or date.today()
    return (event_date - today).days


def proximity_score(days_until: int, window_days: int = 90) -> float:
    if days_until < 0:
        return 0.0
    if window_days <= 0:
        raise ValueError("window_days must be greater than 0.")

    return max(0.0, min(1.0, 1 - (days_until / window_days)))


def event_type_score(event_type: str) -> float:
    return EVENT_TYPE_WEIGHTS.get(event_type, EVENT_TYPE_WEIGHTS["other"])


def estimate_source_credibility(source_url: str) -> float:
    domain = _domain(source_url)
    if not domain:
        return 0.4

    for known_domain, credibility in KNOWN_SOURCE_CREDIBILITY.items():
        if domain == known_domain or domain.endswith(f".{known_domain}"):
            return credibility

    if domain.endswith(".org"):
        return 0.62
    if domain.endswith(".com") or domain.endswith(".io") or domain.endswith(".xyz"):
        return 0.5

    return 0.45


def calculate_catalyst_score(
    event_type: str,
    source_credibility: float,
    days_until: int,
    confidence_score: float,
    window_days: int = 90,
) -> float:
    source_component = normalize_unit_score(source_credibility)
    confidence_component = normalize_unit_score(confidence_score)

    score = (
        event_type_score(event_type) * COMPONENT_WEIGHTS["event_type"]
        + source_component * COMPONENT_WEIGHTS["source_credibility"]
        + proximity_score(days_until, window_days) * COMPONENT_WEIGHTS["proximity"]
        + confidence_component * COMPONENT_WEIGHTS["confidence"]
    )

    return round(max(0.0, min(100.0, score * 100)), 2)


def _domain(source_url: str) -> str:
    parsed = urlparse(source_url.strip().lower())
    domain = parsed.netloc or parsed.path
    if domain.startswith("www."):
        domain = domain[4:]
    return domain.split("/")[0]
