# markets-feed

Hourly price feed for Jamie's markets agent. A GitHub Actions job runs `fetch_prices.py` every hour and commits `data/prices.json`.

- Crypto: CoinGecko public API (price, 24h, 7d and 30d change, volume, market cap, BTC dominance).
- Shares and GBP/USD: Yahoo Finance (latest price, pre-market or after-hours price, last 30 daily closes), with Stooq as a fallback.
- To track something new, add it to `symbols.json`.
- Prices only. No holdings, costs or account details ever go in this repo.
