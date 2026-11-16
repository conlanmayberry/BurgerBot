# BurgerBot — Automated Trading Engine

**A cloud-hosted trading bot that finds, sizes and manages trades on its own, with every rule checked against historical data first.** BurgerBot pulls live market data from Alpaca, scores stocks on technical indicators plus news and analyst sentiment, checks each trade against a risk engine, places paper trades, and sends Telegram alerts explaining every decision.

> Python 3.11 with FastAPI. It has a modular backend (data → features → strategy → decision → risk → execution), a custom backtesting engine and 17 test files.

## Highlights

- **Strategy checked against history.** A daily "buy the pullback" strategy: buy short dips in stocks that are in long-term uptrends, but only while the overall market is healthy. Over a 12-month backtest it returned **+21.6% vs. +20.7% for SPY**, with a **-3.9% max drawdown**, a **70.6% win rate** over 180 trades and a **2.03 profit factor**. See [STRATEGY.md](STRATEGY.md) for the full results and their limitations.
- **Custom backtesting engine.** Simulates next-day-open fills with slippage. Includes a grid search across parameters showing that every variant tested stayed profitable, so the results don't hinge on one lucky setting.
- **Risk engine.** Sizes each position so it risks 1.5% of the account, caps any single position at 12%, never uses margin, and enforces stop-losses, time limits, a market-wide exit rule and drawdown protection.
- **Explainable decisions.** Every BUY, SELL or HOLD comes with a confidence score and a plain-English reason. These are served through a REST API and a Botpress chatbot webhook, so you can ask the bot why it made a trade.
- **Runs in production.** An APScheduler loop, Docker and docker-compose files, an EC2 setup script, health and status endpoints, and Telegram notifications.
- **Multiple data sources.** Alpaca market data and news, Finnhub analyst ratings, and cached web research, combined into one sentiment signal.

## Tech stack

| Area | Tools |
|---|---|
| Backend | Python 3.11, FastAPI, Uvicorn, Pydantic, APScheduler, httpx |
| Market data / brokerage | Alpaca (paper trading), Finnhub |
| Integrations | Telegram Bot API, Botpress |
| Testing | pytest (strategy, risk, scheduler, backtest, API) |
| Deployment | Docker, docker-compose, AWS EC2 |

## Architecture at a glance

```
Alpaca / Finnhub / news ─▶ features (technical + sentiment) ─▶ strategy (daily pullback)
                                                                     │
Telegram ◀── execution (Alpaca orders) ◀── risk engine ◀── decision engine (BUY/SELL/HOLD + why)
                                 │
                       FastAPI: /health /status /watchlist /botpress/decision/explain
```

> ⚠️ This is an educational and paper-trading project, not financial advice.

---


## Quick start

1. Install dependencies:
   - `pip install -e .[dev]`
2. Copy env template:
   - `cp .env.example .env`
3. Run API + scheduler:
   - `uvicorn backend.app.main:app --host 0.0.0.0 --port 8000`
4. Run tests:
   - `pytest`

## Docker quick start

1. Copy env template:
   - `cp .env.example .env`
2. Start container:
   - `docker compose up -d --build`
3. Check service:
   - `curl http://localhost:8000/health`

## Endpoints

- `GET /health`
- `GET /status`
- `GET /watchlist`
- `POST /botpress/decision/explain`

### Botpress integration

Use the backend as a webhook-style explainability service:

- URL: `/botpress/decision/explain`
- Method: `POST`
- Body:
  - `{"symbol":"NVDA"}`
- Optional header:
  - `x-botpress-token: <BOT_BOTPRESS_VERIFY_TOKEN>`

Response includes:

- decision payload (`BUY`/`SELL`/`HOLD`, confidence, rationale)
- risk gate status and reason
- current portfolio snapshot used for the explanation

## Notes

- If Alpaca keys are not configured, the app uses synthetic candles and mock order submission so you can validate the full flow safely.
- The current web intelligence collector is a pluggable phase-1 stub designed for later integration with real news/social/analyst providers.

## AWS hosting (lower-cost option)

If App Runner pricing/features are not a fit, deploy on a small EC2 instance (for example `t3.small`) with Docker and a systemd service. This keeps monthly cost lower while still running 24/7.

### EC2 deployment checklist

1. In AWS, launch EC2:
   - Ubuntu 22.04 LTS
   - instance type `t3.small` (or `t3.micro` for very light testing)
   - attach SSH key pair
2. Security group:
   - inbound `22` from your IP only
   - inbound `8000` from your IP (or from Botpress/Nginx only)
3. SSH to EC2:
   - `ssh -i <your-key>.pem ubuntu@<ec2-public-ip>`
4. Clone your repo and create env:
   - `git clone <your-repo-url> /opt/burgerbot`
   - `cp /opt/burgerbot/.env.example /opt/burgerbot/.env`
   - edit `/opt/burgerbot/.env` with real Alpaca and Telegram keys
5. Run bootstrap:
   - `cd /opt/burgerbot`
   - `sudo PROJECT_GIT_URL="<your-repo-url>" PROJECT_DIR="/opt/burgerbot" /bin/bash scripts/ec2-bootstrap.sh`
6. Verify:
   - `curl http://localhost:8000/health`
   - `curl http://<ec2-public-ip>:8000/watchlist`
7. Point Botpress to backend:
   - set `TRADING_BACKEND_URL=http://<ec2-public-ip>:8000`
   - set `BOTPRESS_TO_TRADER_TOKEN` to match `BOT_BOTPRESS_VERIFY_TOKEN`
   - use `examples/botpress-action.js` as your action template

### Update/rollback on EC2

- Update to latest code:
  - `cd /opt/burgerbot && git pull --ff-only && docker compose up -d --build`
- View logs:
  - `docker compose logs -f`
- Restart:
  - `sudo systemctl restart burgerbot`
