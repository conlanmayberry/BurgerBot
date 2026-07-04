# BurgerBot AI Handoff Prompt

Use this prompt with another AI assistant when asking for help with this project. Do not paste real API keys, tokens, SSH keys, Telegram tokens, Alpaca secrets, Finnhub keys, GitHub tokens, or account numbers into the other AI.

```text
You are helping me with a Python automatic stock trading bot called BurgerBot.

The project lives at:
C:\Users\conla\OneDrive\Desktop\BurgerBot

It is deployed on an AWS EC2 Ubuntu server at:
/opt/burgerbot

It runs in Docker Compose as a FastAPI backend:
docker compose up -d --build

The app entrypoint is:
backend.app.main:app

The Docker container runs:
uvicorn backend.app.main:app --host 0.0.0.0 --port 8000

The backend exposes status endpoints such as:
GET http://localhost:8000/health
GET http://localhost:8000/status

Web dashboard:
GET / serves a burger-themed status dashboard (backend/app/static/dashboard.html).
It polls /status (5s), /connections (20s), and /settings, shows per-service
health as burger layers, kitchen alerts, decisions/exits tables, and a live
settings editor.

GET /connections returns per-service health: alpaca_trading, market_data,
analyst, news, telegram, scheduler. Statuses: ok / warn / error / off.

GET /settings and PATCH /settings read/update runtime trading settings
(min_confidence, max_open_positions, max_position_pct, stop_loss_pct,
take_profit_pct, buy/sell thresholds, market_interval_seconds, toggles for
auto-execution / protective exits / after-hours). PATCH applies immediately
to the live risk/decision/trader engines but is NOT persisted to .env -
container restart reverts to environment values. PATCH is token-guarded by
BOT_BOTPRESS_VERIFY_TOKEN if set (X-Botpress-Token header).

Start.bat at the repo root is the one-click launcher: pulls locally, SSHes
to EC2 (git pull + docker compose up -d --build), waits for /health, then
opens the dashboard in the browser. EC2 host/key/URL are set at the top of
the file.

The project uses:
- FastAPI
- APScheduler
- httpx
- pydantic-settings
- Alpaca paper trading
- Telegram notifications
- Finnhub analyst recommendation data
- Alpaca news data

Important deployment commands on EC2:
cd /opt/burgerbot
git pull
docker compose up -d --build
curl http://localhost:8000/status
docker compose logs -f

The local helper script is:
START_BURGERBOT.ps1

Local-only notes are in:
LOCAL_STARTUP_NOTES.md

Never commit .env or local secret notes. .env is ignored by Git.

Core architecture:

1. backend/app/main.py
Starts the FastAPI app. During app lifespan startup, it starts the trading scheduler.

2. backend/app/scheduler.py
The TradingScheduler is the main orchestration loop.
It:
- loads portfolio state from Alpaca
- loads candle data from Alpaca
- loads analyst/news intel
- builds technical features
- builds sentiment features
- asks the decision engine for BUY/SELL/HOLD
- applies the risk engine
- submits approved orders through the trader
- buffers executed trade summaries for Telegram
- exposes runtime diagnostics through /status

The scheduler is interval-based, not market-hours cron-based.
The relevant env vars are:
BOT_MARKET_INTERVAL_SECONDS
BOT_TELEGRAM_SUMMARY_INTERVAL_SECONDS
BOT_CLOSED_MARKET_INTERVAL_SECONDS

Scheduler reliability behavior:
- Jobs use max_instances=1, coalesce=True, and misfire_grace_time so a slow
  cycle cannot raise misfire errors or stack overlapping runs.
- The first cycle runs on the scheduler thread, not during FastAPI startup,
  so /health responds immediately and Docker healthchecks never fail at boot.
- The cycle checks the Alpaca market clock (GET /v2/clock, cached).
  When the market is closed, full evaluation runs only every
  BOT_CLOSED_MARKET_INTERVAL_SECONDS (default 600) instead of every cycle.
- A per-symbol try/except isolates failures: one bad symbol cannot kill the
  cycle. Errors appear in /status under last_symbol_errors.
- Intel provider failures degrade to cached/empty signals instead of
  aborting the cycle.
- Protective exits run before scored decisions: positions whose unrealized
  P/L breaches BOT_STOP_LOSS_PCT or BOT_TAKE_PROFIT_PCT get a SELL routed
  through the risk engine even if the decision engine says HOLD.
- A symbol that already traded this cycle is blocked from trading again in
  the same cycle ("Already traded this cycle").

3. backend/app/data/alpaca_client.py
Handles Alpaca account, positions, open orders, candles, and order submission.

Important behavior:
- If Alpaca credentials are missing, it falls back to mock portfolio/candles/orders.
- Mock order IDs look like mock-AMZN-...
- If you see mock order IDs, no real Alpaca order was submitted.
- It now fetches Alpaca open orders using GET /v2/orders with status=open.
- It now includes open_orders in portfolio state so pending/unfilled buy orders block duplicate buys.
- It logs rejected Alpaca order response bodies.
- It reuses one persistent httpx client (connection pooling) instead of a new
  TLS handshake per request.
- GETs retry up to BOT_HTTP_RETRY_ATTEMPTS times with backoff on 429/5xx and
  transport errors.
- get_clock() exposes the Alpaca market clock, cached BOT_CLOCK_CACHE_SECONDS.
  It fails closed: if the clock cannot be fetched and nothing is cached, the
  market is reported closed so orders stay blocked.
- cancel_all_orders() issues DELETE /v2/orders (exposed as POST /orders/cancel-all).
- Positions parsing includes unrealized_plpc and current_price for the
  protective exit logic.

Important candle behavior:
- It first fetches normal latest 1Min bars.
- If symbols have fewer than BOT_MIN_TECHNICAL_BARS, it fetches historical fallback bars.
- The fallback fetches each low-data symbol individually so one symbol cannot consume the shared result limit.
- /status shows last_candle_counts and last_candle_fallback_symbols.

4. backend/app/features/technical.py
Builds technical features from candles:
- latest_price
- momentum_5
- momentum_20
- momentum_60
- volatility_20
- atr_pct_14
- volume_ratio
- trend_strength
- rsi_14
- macd_histogram
- vwap_distance
- data_quality

If there are fewer than 21 candles, technical features are mostly neutral and data_quality is 0.
If data_quality is too low, decisions should remain HOLD.

Candle freshness:
- TechnicalFeatures includes candle_age_minutes.
- data_quality = bar count factor * freshness factor.
- Bars older than BOT_CANDLE_STALE_MINUTES decay linearly to a 0.5 floor, so
  overnight/stale data falls below the 0.65 data_quality_threshold and
  decisions stay HOLD, while /status still shows non-zero quality for
  diagnostics.
- EMA/MACD were fixed: EMAs are seeded with an SMA and MACD signal is a
  proper EMA-9 of the MACD line series (previously the math was biased).

5. backend/app/data/web_intel.py
Collects outside market information.
It currently uses:
- FinnhubAnalystProvider
- AlpacaNewsProvider

Social media is intentionally removed and must not impact trading decisions.

Caching and resilience:
- Analyst signals are cached BOT_ANALYST_CACHE_MINUTES (default 60) and news
  signals BOT_NEWS_CACHE_MINUTES (default 10). This cut ~26 HTTP calls per
  60-second cycle down to roughly one news refresh every 10 minutes and one
  analyst sweep per hour, which keeps cycles under the interval budget and
  respects Finnhub's per-minute rate limit.
- If a provider call fails, the last good cached result is served instead of
  failing the cycle. /status shows per-provider state in
  last_intel_provider_status (fresh / cached / stale-after-error / error-empty).

6. backend/app/data/providers/finnhub_analyst.py
Uses Finnhub recommendation trends.
It converts strongBuy/buy/hold/sell/strongSell into an analyst IntelSignal.

Requires:
BOT_FINNHUB_API_KEY
BOT_ENABLE_ANALYST_SIGNALS=true

7. backend/app/data/providers/alpaca_news.py
Uses Alpaca news.
It does not use social media.
It scores finance-specific news events such as:
- earnings beats/misses
- guidance raises/cuts
- analyst upgrades/downgrades
- legal/regulatory risk
- product/deal strength
- operational pressure

It weighs headline more than summary/content, handles negation, weights recency, and includes source confidence.

Requires existing Alpaca credentials.
Relevant env vars:
BOT_ENABLE_NEWS_SIGNALS=true
BOT_NEWS_LOOKBACK_HOURS=24
BOT_NEWS_MAX_ARTICLES=10

8. backend/app/features/sentiment.py
Builds sentiment features only from:
- analyst
- news

Social media is ignored.
The sentiment output includes:
- weighted_sentiment
- analyst_bias
- signal_confidence

9. backend/app/strategy/ (THE TRADING BRAIN - replaces the old decision engine)
Daily pullback strategy shared verbatim by live trading and the backtester.
See STRATEGY.md for rules, backtest results, and tuning knobs.
- Regime filter: SPY above 200-day SMA or no entries (and positions exit).
- Trend filter: stock above its own 200-day SMA.
- Entry: Wilder RSI(3) < 20 pullback, ranked by 6-month momentum with a
  small sentiment tilt for ordering only.
- Exits: RSI bounce / 5-day SMA reclaim, 10-day time stop, 2.5x ATR hard
  stop, regime flip. Intraday protective backstops at -10%/+15%.
- Sizing: risk 1.5% of equity to the stop, max 12% per position, never more
  than available cash (cycle-level cash ledger prevents margin).
Backtest (12mo, 5bps slippage): +21.6% vs SPY +20.7%, max DD -3.9%,
70.6% win rate, profit factor 2.03. Robust across parameter variants.

backend/backtest/ is the backtest harness:
python -m backend.backtest --months 12 --save   (report to backtest_reports/)
python -m backend.backtest --grid               (parameter sensitivity)
Data: Alpaca daily bars when credentials exist, else Yahoo's free chart API;
cached in .backtest_cache/.

GET /performance returns per-symbol realized P/L (FIFO from Alpaca fill
activities, last 120 days) plus open-position P/L; shown on the dashboard.

9b. backend/app/decision/engine.py (LEGACY)
The old minute-bar scoring engine. Its TradingDecision dataclass is still
the shared decision type, but DecisionEngine scoring no longer drives
trading. Its fatal flaw: momentum scalers calibrated for daily moves were
fed 1-minute bars, so scores almost never crossed thresholds and the bot
flatlined in cash.

Decision thresholds:
buy_threshold = 0.35
sell_threshold = -0.35
data_quality_threshold = 0.65
high_confidence_score = 0.50

The final score combines:
- technical score
- sentiment score
- volume boost
- volatility/ATR risk penalty

Sentiment score uses:
0.60 * weighted_sentiment + 0.40 * analyst_bias
then multiplies by signal_confidence.

Confidence is scaled differently for actual BUY/SELL decisions:
- HOLD confidence remains low/raw.
- BUY/SELL confidence is normalized against high_confidence_score.

This was fixed because previously a score like 0.416 produced confidence 0.416 and was blocked by BOT_MIN_CONFIDENCE=0.8 even though it was a valid BUY.

10. backend/app/risk/engine.py
Applies risk checks after a BUY/SELL/HOLD decision.

Risk blocks include:
- No trade action
- Market closed (unless BOT_ALLOW_AFTER_HOURS_TRADING=true)
- Confidence below threshold
- Daily drawdown limit reached
- No open position to sell
- Pending sell order already open
- Pending buy order already open
- Position already open
- Max open positions reached
- Cooldown after loss is active
- Already traded this cycle (scheduler-level, same-cycle duplicate guard)

Important fix:
The bot now treats pending open buy orders as exposure.
If Alpaca has an unfilled AMZN buy order, the bot will not place another AMZN buy.
Pending buy symbols also count toward BOT_MAX_OPEN_POSITIONS.

This was added after the bot queued hundreds of duplicate AMZN buys overnight while the market was closed.

11. backend/app/execution/trader.py
Submits market day orders to Alpaca.
It sizes buys based on:
- account cash
- equity
- max_position_pct
- decision confidence
- estimated latest price

For sells, it sells the current held quantity.

12. backend/app/notify/telegram.py
Sends Telegram notifications.
Current intended behavior:
- Do not send messages for HOLD or blocked trades.
- Send summary messages for actual submitted buy/sell trades.
- Telegram uses BOT_TELEGRAM_BOT_TOKEN and BOT_TELEGRAM_CHAT_ID.

Important env vars:

BOT_ENVIRONMENT=dev
BOT_API_HOST=0.0.0.0
BOT_API_PORT=8000

BOT_ALPACA_KEY=...
BOT_ALPACA_SECRET=...
BOT_ALPACA_BASE_URL=https://paper-api.alpaca.markets
BOT_ALPACA_DATA_URL=https://data.alpaca.markets
BOT_ALPACA_FEED=iex

BOT_FINNHUB_API_KEY=...
BOT_ENABLE_ANALYST_SIGNALS=true
BOT_ENABLE_NEWS_SIGNALS=true
BOT_NEWS_LOOKBACK_HOURS=24
BOT_NEWS_MAX_ARTICLES=10

BOT_MIN_TECHNICAL_BARS=61
BOT_TECHNICAL_FALLBACK_LOOKBACK_DAYS=7

BOT_SYMBOL_WATCHLIST=NVDA,TSLA,MSFT,GOOGL,AMZN,META,AMD,AVGO,PLTR,AAPL,LLY,NVO,JPM,V,MA,COST,WMT,XOM,CAT,UNH,LIN,PANW,ORCL,DE,KO
BOT_MARKET_INTERVAL_SECONDS=60
BOT_TELEGRAM_SUMMARY_INTERVAL_SECONDS=1800

BOT_MAX_POSITION_PCT=0.08
BOT_MAX_DAILY_DRAWDOWN_PCT=0.03
BOT_MAX_OPEN_POSITIONS=5
BOT_MIN_CONFIDENCE=0.8
BOT_COOLDOWN_MINUTES_AFTER_LOSS=10
BOT_ENABLE_AUTO_EXECUTION=true

BOT_ALLOW_AFTER_HOURS_TRADING=false
BOT_CLOSED_MARKET_INTERVAL_SECONDS=600
BOT_CLOCK_CACHE_SECONDS=60
BOT_ENABLE_PROTECTIVE_EXITS=true
BOT_STOP_LOSS_PCT=0.03
BOT_TAKE_PROFIT_PCT=0.06
BOT_ANALYST_CACHE_MINUTES=60
BOT_NEWS_CACHE_MINUTES=10
BOT_CANDLE_STALE_MINUTES=15
BOT_HTTP_RETRY_ATTEMPTS=3

BOT_TELEGRAM_BOT_TOKEN=...
BOT_TELEGRAM_CHAT_ID=...

BOT_BOTPRESS_VERIFY_TOKEN=optional_shared_secret

Botpress:
Botpress is optional. It is not the trading brain.
The backend contains the logic. Botpress only calls backend endpoints for explanations/manual workflow.
The bot can run without Botpress.

Important status fields:

last_run_at:
Last completed trading cycle timestamp.

last_error:
If not null, the scheduler hit an exception.

consecutive_error_count:
How many cycles in a row have failed. 0 means healthy.

last_cycle_duration_seconds:
Wall time of the last cycle. Should stay well below
BOT_MARKET_INTERVAL_SECONDS.

market_open / next_market_open / next_market_close:
Alpaca market clock state from the last cycle.

cycles_skipped_market_closed:
Cycles skipped because the market was closed and the closed-market
interval had not elapsed.

last_exit_decisions:
Protective stop-loss/take-profit exits evaluated in the last cycle, with
exit_reason, unrealized_plpc, risk outcome, and whether the sell was
submitted.

last_symbol_errors:
Per-symbol evaluation exceptions from the last cycle (normally empty).

last_intel_provider_status:
Per-provider cache state: fresh, cached, stale-after-error, or error-empty.

last_decision_count:
Usually 25, matching the watchlist size.

last_action_counts:
Example: {"HOLD":24,"BUY":1}
Shows whether the engine is producing actual trades.

last_risk_block_counts:
Shows why trades did not execute.
Example: {"No trade action":24,"Max open positions reached":1}

last_intel_counts:
Number of analyst/news signals per symbol.

last_signal_source_counts:
Example: {"analyst":25,"news":173}
Shows whether expert/news ingestion is working.

last_top_decisions:
Top-scoring symbols from the last cycle with:
- symbol
- action
- score
- confidence
- risk_allowed
- risk_reason
- intel_count
- pending_buy
- tech_score
- sentiment_score
- data_quality

last_candle_counts:
How many candles each symbol had after normal/fallback fetching.
Healthy symbols should usually have enough bars, often around 390 after fallback.

last_candle_fallback_symbols:
Symbols that needed historical fallback bars.

last_open_position_count:
How many Alpaca positions are currently open.

last_open_symbols:
Symbols currently held in Alpaca.

last_pending_buy_order_count:
Count of symbols with open/pending buy orders.

last_pending_buy_symbols:
Symbols with unfilled buy orders.

last_pending_buy_quantities:
Pending buy quantities by symbol.

max_open_positions:
Configured cap from BOT_MAX_OPEN_POSITIONS.

alpaca_credentials_loaded:
True means real Alpaca credentials are loaded.
False means the app is in mock mode.

auto_execution_enabled:
True means it can submit orders.

Common debugging interpretation:

If alpaca_credentials_loaded is false:
The bot is in mock mode. Fix BOT_ALPACA_KEY/BOT_ALPACA_SECRET and rebuild.

If last_signal_source_counts is empty or missing news/analyst:
Finnhub/Alpaca news may not be configured or provider calls may be failing.

If last_candle_counts are mostly 0:
Market data is not coming through. Check Alpaca data URL/feed/credentials.

If last_candle_counts are good but last_action_counts is all HOLD:
The strategy is running but scores are below BUY/SELL thresholds.

If BUY appears but risk_reason is Confidence below threshold:
Score was high enough for BUY but not strong enough for BOT_MIN_CONFIDENCE.

If BUY appears but risk_reason is Max open positions reached:
Alpaca already has too many open positions or pending buy symbols.

If BUY appears but risk_reason is Pending buy order already open:
There is already an unfilled Alpaca buy order for that symbol. This is intentional protection.

If Telegram is quiet:
That is expected unless actual submitted trades occur. It should not spam HOLDs or blocked trades.

Important past issues already fixed:

1. Mock trades looked real in Telegram.
Cause: missing Alpaca credentials returned mock order IDs.
Fix: diagnostics now show alpaca_credentials_loaded.

2. Botpress confusion.
Botpress is not required for trading. Backend and scheduler are the trading system.

3. Telegram spam.
Telegram was reduced to summarize actual submitted trades only.

4. Scheduler timing confusion.
The scheduler was switched back to interval-based timing using BOT_MARKET_INTERVAL_SECONDS.

5. Technical data quality was 0 premarket.
Cause: too few latest minute bars.
Fix: historical fallback bars using BOT_MIN_TECHNICAL_BARS and BOT_TECHNICAL_FALLBACK_LOOKBACK_DAYS.

6. Historical fallback gave AAPL bars but almost no bars for other symbols.
Cause: multi-symbol fallback request shared one limit.
Fix: fallback now fetches each low-data symbol individually.

7. BUY signal blocked by confidence.
Cause: confidence was raw abs(score), so score 0.416 gave confidence 0.416 and failed BOT_MIN_CONFIDENCE=0.8.
Fix: BUY/SELL confidence is normalized against high_confidence_score.

8. Bot queued hundreds of duplicate overnight buys.
Cause: risk checked filled positions but not open/unfilled orders.
Fix: Alpaca open orders are now included in portfolio state; pending buys block duplicate buys and count toward max positions.

9. Scheduler constantly failed / cycles overran the interval.
Cause: up to ~55 sequential HTTP requests per 60s cycle (25 individual
candle fallbacks overnight plus 25 Finnhub calls), each opening a new TLS
connection, with no misfire handling; the first cycle also blocked FastAPI
startup so Docker healthchecks failed and restarted the container.
Fix: persistent pooled httpx client, retries with backoff, intel TTL
caching, closed-market cycle throttling, coalesce/misfire_grace_time/
max_instances on jobs, and the first cycle moved off the startup path.

10. Orders submitted while the market was closed.
Cause: no market-hours awareness; market day orders queued overnight.
Fix: the Alpaca clock gates order submission. "Market closed" risk block
unless BOT_ALLOW_AFTER_HOURS_TRADING=true. The clock fails closed.

11. Decisions scored stale data like live data.
Cause: no candle freshness check; overnight fallback bars were hours old.
Fix: candle_age_minutes degrades data_quality past BOT_CANDLE_STALE_MINUTES,
pushing decisions to HOLD on stale data.

12. No exit logic and a duplicate-sell hazard.
Fix: protective stop-loss/take-profit exits via unrealized_plpc, a pending
sell order guard mirroring the buy guard, and a same-cycle duplicate trade
guard.

Testing:
Run:
python -m pytest backend\tests
python -m compileall backend

Current test coverage includes:
- Alpaca candle fallback behavior
- Alpaca news scoring
- Finnhub analyst scoring
- decision engine
- risk engine (including market-closed and pending-sell blocks)
- portfolio state pending order parsing
- simple end-to-end simulation path
- market clock parsing, caching, retry, and fail-closed behavior
- candle staleness quality decay and MACD sanity
- intel TTL caching and stale-on-error fallback
- protective exit decisions and closed-market cycle throttling

Important development guidelines for this project:

- Do not add social media influence back into decisions.
- News and analyst/expert opinions are allowed.
- Avoid fake/random signals.
- Avoid silently falling back to mock behavior without diagnostics.
- Risk controls should prefer blocking over duplicate exposure.
- Status output should explain why trades did or did not happen.
- Do not submit duplicate buys when an open buy order already exists.
- Be careful with after-hours/premarket behavior because market orders can queue.
- Keep Telegram quiet unless trades are actually submitted.
- Do not expose secrets in logs, screenshots, or committed files.

Current likely improvement areas:

1. Add persistent local state:
Store cycle history, submitted order IDs, and recent decisions so restarts do not erase context.

2. Improve news scoring:
Current news scoring is deterministic finance-event scoring. It is better than keyword counting but not a full ML/LLM sentiment model.

3. Backtesting:
The strategy should be backtested before trusting live funds.

4. Improve market regime checks:
Could avoid buying during broad market selloffs or high-volatility periods.

(Previously listed market-hours protection, a cancel-orders endpoint, and
sell/exit logic are now implemented: see the risk engine "Market closed"
block, POST /orders/cancel-all, and protective stop-loss/take-profit exits.)

When helping me, please inspect the actual files before making assumptions. Prefer small, safe backend changes with tests. If you change trading behavior, explain what risk guardrail changed and how to verify it from /status.
```
