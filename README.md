# Crypto Catalyst Bot

Local MVP for researching upcoming crypto catalysts. It pulls a top-200 coin universe from CoinMarketCap, stores metadata in SQLite, lets you manually add catalysts, scores them, and exports a ranked CSV for the next 90 days.

This project does not implement live trading, does not connect to Hyperliquid, and does not scrape X/Twitter or Reddit.

MVP 2 adds a simple market reaction / priced-in estimator. Each CoinMarketCap coin update stores market snapshots, and catalyst rankings now include recent return metrics, relative performance, a priced-in penalty, and an adjusted score.

## Setup

1. Create and activate a virtual environment:

```bash
python -m venv .venv
source .venv/bin/activate
```

2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Create your local environment file:

```bash
cp .env.example .env
```

## CoinMarketCap API Key

Add your CoinMarketCap Pro API key to `.env`:

```bash
CMC_API_KEY=your_coinmarketcap_api_key_here
```

The default CoinMarketCap endpoint is `GET /v3/cryptocurrency/listings/latest`, which is the current CoinMarketCap docs path for ranked market-cap listings. You can override `CMC_BASE_URL` or `CMC_LISTINGS_ENDPOINT` in `.env` if needed later.

## Update Coin Universe

Fetch and upsert the top 200 crypto assets by market cap:

```bash
python scripts/update_coin_universe.py
```

This creates or updates the local SQLite database at `data/crypto_catalysts.db` by default.

## Add A Catalyst

Add catalysts manually after the coin universe has been populated:

```bash
python scripts/add_catalyst.py \
  --symbol ETH \
  --event-type mainnet_upgrade \
  --event-date 2026-08-15 \
  --description "Protocol upgrade target date" \
  --source-url "https://blog.ethereum.org/example" \
  --confidence-score 0.8
```

Confidence can be entered as `0.8` or `80`; both are stored as `0.8`. Source credibility is inferred from the URL domain. You can override it when needed:

```bash
python scripts/add_catalyst.py \
  --symbol SOL \
  --event-type conference \
  --event-date 2026-07-20 \
  --description "Ecosystem conference keynote" \
  --source-url "https://solana.com/news/example" \
  --confidence-score 70 \
  --source-credibility 0.9
```

Supported event types:

- `exchange_listing`
- `mainnet_upgrade`
- `governance_vote`
- `token_unlock`
- `airdrop_snapshot`
- `conference`
- `roadmap_release`
- `partnership`
- `other`

## Generate Ranked CSV

Rank upcoming catalysts within the next 90 days:

```bash
python scripts/rank_catalysts.py
```

The default output path is:

```text
outputs/ranked_catalysts.csv
```

You can override the date window and output path:

```bash
python scripts/rank_catalysts.py --days 45 --output outputs/next_45_days.csv
```

## Run The Dashboard

Start the local browser dashboard:

```bash
python scripts/run_dashboard.py
```

Then open:

```text
http://127.0.0.1:8000
```

The dashboard lets you:

- update the CoinMarketCap coin universe
- add catalysts from a form
- edit upcoming catalysts
- view ranked upcoming catalysts
- export or download the ranked CSV

You can choose a different port:

```bash
python scripts/run_dashboard.py --port 8010
```

The CSV includes:

- `symbol`
- `project_name`
- `event_type`
- `event_date`
- `days_until_event`
- `description`
- `source_url`
- `confidence_score`
- `catalyst_score`
- `return_7d_pct`
- `return_14d_pct`
- `return_30d_pct`
- `volume_change_pct`
- `btc_relative_return_pct`
- `eth_relative_return_pct`
- `priced_in_penalty`
- `adjusted_score`

## Scoring

Catalyst scores are normalized from 0 to 100 using:

- event type weight
- inferred or overridden source credibility
- days until the event
- confidence score

Events closer to today receive a higher proximity component. Past events are not included in ranked output.

## Priced-In Estimator

Market snapshots are saved when the coin universe is updated. When enough historical snapshots exist, the estimator calculates recent 7-day, 14-day, and 30-day returns, volume change, and BTC/ETH-relative return. A simple `priced_in_penalty` is subtracted from the original `catalyst_score` to produce `adjusted_score`.

The adjusted score is still a research ranking signal only. It is not a buy/sell signal.

For local MVP 2 validation before you have 30 days of real snapshots, seed development-only BTC, ETH, and SOL snapshot history:

```bash
python scripts/seed_market_snapshots.py
```

This script only runs when manually called. It does not require an API key and only inserts snapshots for BTC, ETH, and SOL if those symbols already exist in the local coin database.

## Tests

Run the scoring tests:

```bash
pytest
```

or:

```bash
python -m pytest
```
