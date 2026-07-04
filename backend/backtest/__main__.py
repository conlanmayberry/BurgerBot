from __future__ import annotations

import argparse
import json
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path

from backend.backtest.data import load_daily_bars
from backend.backtest.engine import BacktestConfig, BacktestResult, run_backtest
from backend.app.strategy import StrategyParams
from backend.config.settings import Settings


REPORT_DIR = Path(__file__).resolve().parents[2] / "backtest_reports"


def main() -> None:
    parser = argparse.ArgumentParser(description="BurgerBot strategy backtest")
    parser.add_argument("--months", type=int, default=12, help="Simulation window in months")
    parser.add_argument("--equity", type=float, default=100_000.0, help="Starting equity")
    parser.add_argument("--positions", type=int, default=8, help="Max concurrent positions")
    parser.add_argument("--grid", action="store_true", help="Run a small parameter grid comparison")
    parser.add_argument("--save", action="store_true", help="Save a JSON report to backtest_reports/")
    args = parser.parse_args()

    settings = Settings()
    symbols = settings.symbols
    lookback_days = args.months * 31 + 340  # indicator warm-up history
    print(f"Loading daily bars for {len(symbols)} symbols + SPY ({lookback_days} calendar days)...")
    bars = load_daily_bars(symbols + ["SPY"], lookback_days, settings)
    spy_bars = bars.get("SPY", [])
    symbol_bars = {s: b for s, b in bars.items() if s != "SPY" and b}
    missing = [s for s in symbols if not bars.get(s)]
    if missing:
        print(f"WARNING: no data for {missing}")
    print(f"Loaded {len(symbol_bars)} symbols; SPY bars: {len(spy_bars)}")

    config = BacktestConfig(
        starting_equity=args.equity,
        max_open_positions=args.positions,
        months=args.months,
    )

    if args.grid:
        _run_grid(symbol_bars, spy_bars, config)
        return

    result = run_backtest(symbol_bars, spy_bars, StrategyParams(), config)
    _print_report(result)
    if args.save:
        path = _save_report(result)
        print(f"\nReport saved: {path}")


def _run_grid(symbol_bars, spy_bars, config: BacktestConfig) -> None:
    base = StrategyParams()
    variants: list[tuple[str, StrategyParams]] = [
        ("baseline rsi<25 hold10 stop2.5", base),
        ("rsi<20 (deeper dips)", replace(base, entry_rsi_max=20.0)),
        ("rsi<30 (shallower dips)", replace(base, entry_rsi_max=30.0)),
        ("exit rsi>60 (earlier exit)", replace(base, exit_rsi_min=60.0)),
        ("exit rsi>70 (later exit)", replace(base, exit_rsi_min=70.0)),
        ("hold max 7", replace(base, max_hold_days=7)),
        ("hold max 15", replace(base, max_hold_days=15)),
        ("stop 2.0 ATR", replace(base, stop_atr_multiple=2.0)),
        ("stop 3.0 ATR", replace(base, stop_atr_multiple=3.0)),
        ("trend sma 50", replace(base, trend_sma_days=50)),
        ("trend sma 200", replace(base, trend_sma_days=200)),
        ("risk 0.5%", replace(base, risk_per_trade_pct=0.005)),
        ("risk 1.0%", replace(base, risk_per_trade_pct=0.010)),
    ]
    print(f"\n{'variant':38} {'return%':>8} {'spy%':>7} {'maxDD%':>7} {'trades':>7} {'win%':>6} {'PF':>6}")
    print("-" * 84)
    for label, params in variants:
        result = run_backtest(symbol_bars, spy_bars, params, config)
        m = result.metrics
        print(
            f"{label:38} {m['total_return_pct']:>8.2f} {m['spy_return_pct'] or 0:>7.2f} "
            f"{m['max_drawdown_pct']:>7.2f} {m['closed_trades']:>7} "
            f"{m['win_rate_pct'] or 0:>6.1f} {m['profit_factor'] or 0:>6.2f}"
        )


def _print_report(result: BacktestResult) -> None:
    m = result.metrics
    print(f"\n=== BurgerBot backtest {result.start_date} -> {result.end_date} ===")
    print(f"Strategy params: {asdict(result.params)}")
    print(f"Config: positions<={result.config.max_open_positions}, "
          f"max_position_pct={result.config.max_position_pct}, slippage={result.config.slippage_bps}bps")
    print()
    print(f"  Final equity:      ${m['final_equity']:>12,.2f}  (start ${m['starting_equity']:,.0f})")
    print(f"  Total return:      {m['total_return_pct']:>8.2f}%")
    print(f"  SPY buy & hold:    {m['spy_return_pct']:>8.2f}%   beats SPY: {m['beats_spy']}")
    print(f"  Max drawdown:      {m['max_drawdown_pct']:>8.2f}%")
    print(f"  Closed trades:     {m['closed_trades']:>5}   win rate: {m['win_rate_pct']}%")
    print(f"  Avg win / loss:    ${m['avg_win']:,.2f} / ${m['avg_loss']:,.2f}   profit factor: {m['profit_factor']}")
    print(f"  Realized PnL:      ${m['realized_pnl']:,.2f}   unrealized: ${m['unrealized_pnl']:,.2f}")
    print(f"  Avg days held:     {m['avg_days_held']}   avg exposure: {m['avg_exposure_pct']}%")
    print(f"  Open at end:       {m['open_positions_at_end']}")
    print("\n  Per-symbol P/L (total = realized + open):")
    print(f"  {'symbol':8} {'total':>10} {'realized':>10} {'open':>10} {'trades':>7} {'win%':>6}")
    for symbol, row in result.per_symbol.items():
        print(f"  {symbol:8} {row['total_pnl']:>10,.2f} {row['realized_pnl']:>10,.2f} "
              f"{row['unrealized_pnl']:>10,.2f} {row['trades']:>7} {row['win_rate_pct'] or '-':>6}")


def _save_report(result: BacktestResult) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = REPORT_DIR / f"backtest_{stamp}.json"
    payload = {
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "start_date": result.start_date.isoformat(),
        "end_date": result.end_date.isoformat(),
        "params": asdict(result.params),
        "config": asdict(result.config),
        "metrics": result.metrics,
        "per_symbol": result.per_symbol,
        "trades": [
            {
                "symbol": t.symbol,
                "entry_date": t.entry_date.isoformat(),
                "exit_date": t.exit_date.isoformat(),
                "entry_price": round(t.entry_price, 4),
                "exit_price": round(t.exit_price, 4),
                "qty": t.qty,
                "pnl": round(t.pnl, 2),
                "reason": t.reason,
                "days_held": t.days_held,
            }
            for t in result.trades
        ],
        "equity_curve": [[d.isoformat(), round(e, 2)] for d, e in result.equity_curve],
        "ending_positions": result.ending_positions,
    }
    path.write_text(json.dumps(payload, indent=1))
    return path


if __name__ == "__main__":
    main()
