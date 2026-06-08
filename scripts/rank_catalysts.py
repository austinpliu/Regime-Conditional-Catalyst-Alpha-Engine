from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))

from src.config import get_settings
from src.services.catalyst_service import export_ranked_catalysts, rank_catalyst_rows
from src.utils.logging import configure_logging


def rank_catalysts(days: int | None = None, output: str | None = None) -> Path:
    configure_logging()
    settings = get_settings()

    output_path = export_ranked_catalysts(settings, days=days, output=output)
    row_count = len(rank_catalyst_rows(settings, days=days))
    print(f"Wrote {row_count} ranked catalysts to {output_path}.")
    return output_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Rank upcoming catalysts and export CSV")
    parser.add_argument("--days", type=int, default=None, help="Lookahead window in days")
    parser.add_argument("--output", default=None, help="Output CSV path")
    return parser


if __name__ == "__main__":
    args = build_parser().parse_args()
    rank_catalysts(days=args.days, output=args.output)
