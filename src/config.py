from pathlib import Path
import os

from dotenv import load_dotenv
from pydantic import BaseModel, Field


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseModel):
    cmc_api_key: str = ""
    cmc_base_url: str = "https://pro-api.coinmarketcap.com"
    cmc_listings_endpoint: str = "/v3/cryptocurrency/listings/latest"
    database_url: str = Field(default_factory=lambda: f"sqlite:///{PROJECT_ROOT / 'data' / 'crypto_catalysts.db'}")
    output_dir: Path = Field(default_factory=lambda: PROJECT_ROOT / "outputs")
    cmc_limit: int = Field(default=200, gt=0)
    ranking_window_days: int = Field(default=90, gt=0)
    coingecko_base_url: str = "https://api.coingecko.com/api/v3"
    coingecko_api_key: str = ""
    coingecko_request_delay_seconds: float = 2.5
    price_history_days: int = Field(default=120, gt=0)


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return int(value)


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return float(value)


def _env_path(name: str, default: Path) -> Path:
    raw_value = os.getenv(name)
    if raw_value is None or raw_value.strip() == "":
        return default

    path = Path(raw_value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def _env_database_url(default: str) -> str:
    raw_value = os.getenv("DATABASE_URL")
    if raw_value is None or raw_value.strip() == "":
        return default

    if not raw_value.startswith("sqlite:///"):
        return raw_value

    sqlite_path = raw_value.replace("sqlite:///", "", 1)
    if sqlite_path == ":memory:":
        return raw_value

    path = Path(sqlite_path)
    return raw_value if path.is_absolute() else f"sqlite:///{PROJECT_ROOT / path}"


def get_settings() -> Settings:
    load_dotenv(PROJECT_ROOT / ".env")
    default_database_url = f"sqlite:///{PROJECT_ROOT / 'data' / 'crypto_catalysts.db'}"
    return Settings(
        cmc_api_key=os.getenv("CMC_API_KEY", ""),
        cmc_base_url=os.getenv("CMC_BASE_URL", "https://pro-api.coinmarketcap.com").rstrip("/"),
        cmc_listings_endpoint=os.getenv("CMC_LISTINGS_ENDPOINT", "/v3/cryptocurrency/listings/latest"),
        database_url=_env_database_url(default_database_url),
        output_dir=_env_path("OUTPUT_DIR", PROJECT_ROOT / "outputs"),
        cmc_limit=_env_int("CMC_LIMIT", 200),
        ranking_window_days=_env_int("RANKING_WINDOW_DAYS", 90),
        coingecko_base_url=os.getenv("COINGECKO_BASE_URL", "https://api.coingecko.com/api/v3").rstrip("/"),
        coingecko_api_key=os.getenv("COINGECKO_API_KEY", ""),
        coingecko_request_delay_seconds=_env_float("COINGECKO_REQUEST_DELAY_SECONDS", 2.5),
        price_history_days=_env_int("PRICE_HISTORY_DAYS", 120),
    )
