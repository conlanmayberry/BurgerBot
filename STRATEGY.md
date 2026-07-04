# BurgerBot Trading Strategy

## What it does

BurgerBot runs a **daily pullback strategy**: buy short, sharp dips in stocks
that are in long-term uptrends, while the overall market is healthy, and sell
into the bounce. Every rule is computed on **completed daily bars** — the
same data the backtest was validated on. The 60-second live cycle is only
there to notice signals and manage exits promptly; it does not day-trade.

The rules, in order:

1. **Market regime filter** — entries are allowed only while SPY closes above
   its 200-day moving average. When SPY is below it (RISK-OFF), no new buys
   happen and existing positions are exited.
2. **Trend filter** — a stock is only buyable while it trades above its own
   200-day moving average.
3. **Entry trigger** — Wilder RSI(3) below 20: a statistically sharp 2-3 day
   pullback inside an intact uptrend.
4. **Ranking** — when there are more signals than slots, candidates are
   ranked by 6-month momentum (buy the dip in the *leaders*), with a small
   analyst/news sentiment tilt (±0.1) as a tiebreaker only.
5. **Exits** — whichever comes first:
   - **Bounce**: RSI(3) recovers above 65, or price closes back above its
     5-day average with RSI ≥ 50 (mean reversion completed);
   - **Time stop**: 10 trading days without a bounce;
   - **Hard stop**: close below entry price minus 2.5 × ATR(14);
   - **Regime exit**: SPY falls below its 200-day average.
   Live trading adds intraday protective backstops (-10% disaster stop,
   +15% windfall take) that don't interfere with the strategy's normal exits.
6. **Position sizing** — risk-based: each position risks 1.5% of equity to
   its stop, capped at 12% of equity per position, and **never more than
   available cash** (no margin, ever). Fewer than 12 concurrent positions.

## Backtest results (free daily data, 5 bps slippage per side, next-open fills)

Official run: 2025-07-02 → 2026-07-02, $100k start, 25-symbol watchlist.

| Metric | Strategy | SPY buy & hold |
|---|---|---|
| Total return | **+21.58%** | +20.66% |
| Max drawdown | **-3.90%** | (much deeper) |
| Win rate | **70.6%** (180 trades) | — |
| Profit factor | **2.03** | — |
| Avg days held | 3.9 | — |
| Avg exposure | 31.5% | 100% |

Robustness across windows (same parameters, no re-tuning):

| Window | Strategy | SPY | Beats SPY | Max DD | Win rate | PF |
|---|---|---|---|---|---|---|
| 6 months | +10.62% | +8.39% | ✅ | -3.27% | 68.5% | 1.93 |
| 12 months | +21.58% | +20.66% | ✅ | -3.90% | 70.6% | 2.03 |
| 18 months | +12.81% | +26.25% | ❌ (still positive) | -11.35% | 67.6% | 1.37 |

Parameter sensitivity: every variant tested (entry RSI 20/25/30, stops
2.0-3.0 ATR, holds 7-15 days, trend 50/100/200) was **profitable with a
65-69% win rate** — the edge does not depend on one magic number.

## Honest caveats

- **Past performance does not guarantee future results.** This validates the
  strategy has had a real, robust edge — not that it always will.
- The 18-month window includes the early-2025 correction, where the strategy
  protected capital poorly relative to its usual profile (-11% DD) and
  trailed a strong SPY rebound. Expect underperformance vs. buy-and-hold in
  roaring bull recoveries; the compensation is the shallow drawdowns.
- Average exposure is ~30%: most of the account sits in cash waiting for
  high-quality dips. This is by design. Do not "fix" it by loosening the
  entry filter without re-running the backtest.
- Validate on paper for 2-3 weeks before considering real money.

## Re-running the backtest

```bash
python -m backend.backtest --months 12 --save     # full report, saved to backtest_reports/
python -m backend.backtest --months 12 --grid     # parameter sensitivity table
```

Uses Alpaca historical data when credentials are configured, otherwise
Yahoo's free daily API (no key needed). Data is cached in `.backtest_cache/`.

## Tuning knobs (env vars / dashboard Settings)

| Env var | Default | Meaning |
|---|---|---|
| `BOT_RISK_PER_TRADE_PCT` | 0.015 | Equity fraction risked to the stop per position |
| `BOT_STRATEGY_ENTRY_RSI_MAX` | 20 | Dip depth required to enter (lower = pickier) |
| `BOT_STRATEGY_TREND_SMA_DAYS` | 200 | Stock trend filter |
| `BOT_STRATEGY_MAX_HOLD_DAYS` | 10 | Time stop |
| `BOT_STRATEGY_STOP_ATR_MULTIPLE` | 2.5 | Hard stop distance |
| `BOT_MAX_POSITION_PCT` | 0.12 | Per-position cap |
| `BOT_MAX_OPEN_POSITIONS` | 12 | Concurrent position cap |
| `BOT_STOP_LOSS_PCT` | 0.10 | Intraday disaster backstop |
| `BOT_TAKE_PROFIT_PCT` | 0.15 | Intraday windfall backstop |

`risk_per_trade_pct` and `entry_rsi_max` can also be changed live from the
dashboard Settings panel. If you change strategy parameters, re-run the
backtest first.
