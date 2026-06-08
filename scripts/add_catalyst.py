from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))

from src.config import get_settings
from src.models.catalyst import EventType
from src.services.catalyst_service import add_catalyst as add_catalyst_service
from src.utils.logging import configure_logging


def add_catalyst(
    symbol: str,
    event_type: str,
    event_date: str,
    description: str,
    source_url: str,
    confidence_score: float,
    source_credibility: float | None = None,
) -> None:
    configure_logging()
    settings = get_settings()
    result = add_catalyst_service(
        settings=settings,
        symbol=symbol,
        event_type=event_type,
        event_date=event_date,
        description=description,
        source_url=source_url,
        confidence_score=confidence_score,
        source_credibility=source_credibility,
    )

    print(f"Added catalyst {result.catalyst_id} for {result.symbol}. Current score: {result.catalyst_score}.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manually add a crypto catalyst")
    parser.add_argument("--symbol", required=True, help="Coin symbol from the local coin universe, for example ETH")
    parser.add_argument("--event-type", required=True, choices=[event.value for event in EventType])
    parser.add_argument("--event-date", required=True, help="Event date in YYYY-MM-DD format")
    parser.add_argument("--description", required=True)
    parser.add_argument("--source-url", required=True)
    parser.add_argument("--confidence-score", required=True, type=float, help="0-1 or 0-100")
    parser.add_argument("--source-credibility", type=float, default=None, help="Optional override, 0-1 or 0-100")
    return parser


if __name__ == "__main__":
    args = build_parser().parse_args()
    add_catalyst(
        symbol=args.symbol,
        event_type=args.event_type,
        event_date=args.event_date,
        description=args.description,
        source_url=args.source_url,
        confidence_score=args.confidence_score,
        source_credibility=args.source_credibility,
    )
