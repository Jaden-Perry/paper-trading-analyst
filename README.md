# Paper Trading Analyst

A fully automated, hands-off "quant analyst" that runs every Friday after
market close: screens a curated universe of large/mid-cap stocks, decides
buy/sell/hold for a simulated $1,000 portfolio, and writes an analyst-style
explanation for every decision (both the quantitative signal and the news
catalyst behind it). No real money or brokerage account involved anywhere -
this is a paper-trading track record, viewable on a dashboard, that you can
use to judge whether a strategy is worth ever acting on manually yourself.

**Dashboard:** enable GitHub Pages (Settings > Pages > Source: GitHub
Actions) and it'll be live at `https://<your-username>.github.io/<repo>/`.

## How it works

Every Friday at 22:00 UTC (`.github/workflows/paper_trader.yml`):

1. `scripts/screener.py` screens `config/stock_universe.json` for momentum,
   trend, volume, and relative valuation, filtering out penny stocks and
   historically low-volatility ("boring") names.
2. `scripts/portfolio_engine.py` decides buy/sell/hold using the ledger in
   `data/trades.json`: ~15% of portfolio value per position, up to 6
   concurrent positions, with a stop-loss, take-profit, and a max-hold
   timeout that force-closes stale positions.
3. `scripts/trade_writer.py` calls the Anthropic API once per run to write a
   short rationale for every decision, combining the quant signal with real
   headlines pulled via `scripts/market_news.py`.
4. The ledger and the derived dashboard data (`docs/portfolio.json`) are
   committed back to the repo and deployed to GitHub Pages.

## One-time setup

1. Add a repo secret: Settings > Secrets and variables > Actions > New
   repository secret > `ANTHROPIC_API_KEY` (get one at console.anthropic.com
   if you don't already have one - cost is roughly the cost of one Claude
   Sonnet call per week, so a few cents a month).
2. Enable Pages: Settings > Pages > Source: **GitHub Actions**.
3. Trigger the workflow once manually (Actions tab > "Weekly paper trading
   run" > Run workflow) to seed the first ledger and dashboard, rather than
   waiting for the next Friday.

## Local testing

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-...   # only needed once there's a buy/sell/hold to narrate
cd scripts
python paper_trader.py --limit 10   # small universe for a fast test run
```

This never touches git - inspect the result with `git diff` /
`git status` from the repo root before trusting it, and delete
`data/trades.json` to reset the paper portfolio back to a fresh $1,000 start.

## Adjusting risk/strategy

All screening thresholds and position-sizing rules live in
`config/paper_trading.json` - no code changes needed to tune them. The
tracked universe lives in `config/stock_universe.json`.
