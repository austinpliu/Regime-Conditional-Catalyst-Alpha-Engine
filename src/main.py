from __future__ import annotations

import argparse

from scripts.add_catalyst import add_catalyst
from scripts.rank_catalysts import rank_catalysts
from scripts.update_coin_universe import update_coin_universe
from src.dashboard import run_server


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Crypto catalyst research MVP")
    subparsers = parser.add_subparsers(dest="command", required=True)

    update_parser = subparsers.add_parser("update-coins", help="Fetch and store the CoinMarketCap top coin universe")
    update_parser.add_argument("--limit", type=int, default=None)

    add_parser = subparsers.add_parser("add-catalyst", help="Manually add an upcoming catalyst")
    add_parser.add_argument("--symbol", required=True)
    add_parser.add_argument("--event-type", required=True)
    add_parser.add_argument("--event-date", required=True)
    add_parser.add_argument("--description", required=True)
    add_parser.add_argument("--source-url", required=True)
    add_parser.add_argument("--confidence-score", required=True, type=float)
    add_parser.add_argument("--source-credibility", type=float, default=None)

    rank_parser = subparsers.add_parser("rank-catalysts", help="Export ranked catalysts to CSV")
    rank_parser.add_argument("--days", type=int, default=None)
    rank_parser.add_argument("--output", default=None)

    dashboard_parser = subparsers.add_parser("dashboard", help="Run the local browser dashboard")
    dashboard_parser.add_argument("--host", default="127.0.0.1")
    dashboard_parser.add_argument("--port", default=8000, type=int)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "update-coins":
        update_coin_universe(limit=args.limit)
    elif args.command == "add-catalyst":
        add_catalyst(
            symbol=args.symbol,
            event_type=args.event_type,
            event_date=args.event_date,
            description=args.description,
            source_url=args.source_url,
            confidence_score=args.confidence_score,
            source_credibility=args.source_credibility,
        )
    elif args.command == "rank-catalysts":
        rank_catalysts(days=args.days, output=args.output)
    elif args.command == "dashboard":
        run_server(host=args.host, port=args.port)


if __name__ == "__main__":
    main()
