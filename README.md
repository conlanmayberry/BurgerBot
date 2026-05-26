# BurgerBot Trading Backend

Python cloud backend for paper-trading strategy execution with Alpaca, technical/sentiment scoring, risk gating, and Telegram alerts.

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
