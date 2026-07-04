from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from backend.app.data.alpaca_client import Candle
from backend.app.strategy import DailyPullbackStrategy, PositionContext, StrategyParams


@dataclass(frozen=True)
class BacktestConfig:
    starting_equity: float = 100_000.0
    max_open_positions: int = 12
    max_position_pct: float = 0.12
    slippage_bps: float = 5.0
    months: int = 12


@dataclass
class ClosedTrade:
    symbol: str
    entry_date: date
    exit_date: date
    entry_price: float
    exit_price: float
    qty: int
    pnl: float
    reason: str
    days_held: int


@dataclass
class _OpenPosition:
    symbol: str
    qty: int
    entry_price: float
    entry_date: date
    days_held: int = 0


@dataclass
class BacktestResult:
    config: BacktestConfig
    params: StrategyParams
    start_date: date
    end_date: date
    equity_curve: list[tuple[date, float]] = field(default_factory=list)
    trades: list[ClosedTrade] = field(default_factory=list)
    ending_positions: dict[str, dict] = field(default_factory=dict)
    metrics: dict = field(default_factory=dict)
    per_symbol: dict[str, dict] = field(default_factory=dict)


def run_backtest(
    bars_by_symbol: dict[str, list[Candle]],
    spy_bars: list[Candle],
    params: StrategyParams | None = None,
    config: BacktestConfig | None = None,
) -> BacktestResult:
    params = params or StrategyParams()
    config = config or BacktestConfig()
    strategy = DailyPullbackStrategy(params)
    slip = config.slippage_bps / 10_000.0

    spy_by_date = {bar.time.date(): idx for idx, bar in enumerate(spy_bars)}
    calendar = sorted(spy_by_date)
    if not calendar:
        raise ValueError("No SPY bars available for the benchmark calendar")
    sim_start = calendar[-1] - timedelta(days=round(config.months * 30.44))
    trading_days = [d for d in calendar if d >= sim_start]
    if len(trading_days) < 40:
        raise ValueError("Not enough trading days in the requested window")

    index_by_symbol_date: dict[str, dict[date, int]] = {
        symbol: {bar.time.date(): idx for idx, bar in enumerate(bars)}
        for symbol, bars in bars_by_symbol.items()
    }

    cash = config.starting_equity
    min_cash = cash
    positions: dict[str, _OpenPosition] = {}
    trades: list[ClosedTrade] = []
    equity_curve: list[tuple[date, float]] = []
    exposure_samples: list[float] = []
    pending_exits: list[tuple[str, str]] = []  # (symbol, reason)
    pending_entries: list[tuple[str, float, float | None]] = []  # (symbol, rank, stop)

    def bar_on(symbol: str, day: date) -> Candle | None:
        idx = index_by_symbol_date.get(symbol, {}).get(day)
        return bars_by_symbol[symbol][idx] if idx is not None else None

    for day in trading_days:
        # 1) Fill orders queued from yesterday's close at today's open.
        for symbol, reason in pending_exits:
            position = positions.get(symbol)
            today_bar = bar_on(symbol, day)
            if position is None or today_bar is None:
                continue
            fill = today_bar.open * (1.0 - slip)
            cash += position.qty * fill
            trades.append(
                ClosedTrade(
                    symbol=symbol,
                    entry_date=position.entry_date,
                    exit_date=day,
                    entry_price=position.entry_price,
                    exit_price=fill,
                    qty=position.qty,
                    pnl=(fill - position.entry_price) * position.qty,
                    reason=reason,
                    days_held=position.days_held,
                )
            )
            del positions[symbol]
        pending_exits = []

        for symbol, _rank, stop_price in sorted(pending_entries, key=lambda item: item[1], reverse=True):
            if len(positions) >= config.max_open_positions or symbol in positions:
                continue
            today_bar = bar_on(symbol, day)
            if today_bar is None:
                continue
            fill = today_bar.open * (1.0 + slip)
            equity_now = cash + sum(
                p.qty * (bar_on(s, day).close if bar_on(s, day) else p.entry_price)
                for s, p in positions.items()
            )
            qty = strategy.size_position(
                equity=equity_now,
                available_cash=cash,
                entry_price=fill,
                stop_price=stop_price,
                max_position_pct=config.max_position_pct,
            )
            if qty <= 0 or qty * fill > cash:
                continue
            cash -= qty * fill
            min_cash = min(min_cash, cash)
            positions[symbol] = _OpenPosition(symbol=symbol, qty=qty, entry_price=fill, entry_date=day)
        pending_entries = []

        # 2) Age positions on days their symbol actually traded.
        for symbol, position in positions.items():
            if bar_on(symbol, day) is not None:
                position.days_held += 1

        # 3) Compute signals at today's close for tomorrow's open.
        spy_idx = spy_by_date[day]
        spy_history = spy_bars[: spy_idx + 1]
        risk_on = strategy.market_risk_on(spy_history)
        for symbol, bars in bars_by_symbol.items():
            if symbol == "SPY":
                continue
            idx = index_by_symbol_date[symbol].get(day)
            if idx is None:
                continue
            history = bars[: idx + 1]
            held = positions.get(symbol)
            context = PositionContext(entry_price=held.entry_price, days_held=held.days_held) if held else None
            decision = strategy.evaluate_symbol(symbol, history, context, risk_on)
            if decision.action == "SELL" and held is not None:
                pending_exits.append((symbol, decision.reason))
            elif decision.action == "BUY" and held is None:
                pending_entries.append((symbol, decision.rank_score, decision.stop_price))

        # 4) Mark to market at close.
        position_value = 0.0
        for symbol, position in positions.items():
            day_bar = bar_on(symbol, day)
            mark = day_bar.close if day_bar is not None else position.entry_price
            position_value += position.qty * mark
        equity = cash + position_value
        equity_curve.append((day, equity))
        exposure_samples.append(position_value / equity if equity > 0 else 0.0)

    result = BacktestResult(
        config=config,
        params=params,
        start_date=trading_days[0],
        end_date=trading_days[-1],
        equity_curve=equity_curve,
        trades=trades,
    )
    for symbol, position in positions.items():
        last_bar = bars_by_symbol[symbol][-1]
        result.ending_positions[symbol] = {
            "qty": position.qty,
            "entry_price": round(position.entry_price, 2),
            "last_close": round(last_bar.close, 2),
            "unrealized_pnl": round((last_bar.close - position.entry_price) * position.qty, 2),
            "days_held": position.days_held,
        }
    result.metrics = _compute_metrics(result, spy_bars, exposure_samples, config)
    result.metrics["min_cash"] = round(min_cash, 2)
    result.per_symbol = _per_symbol_breakdown(result)
    return result


def _compute_metrics(
    result: BacktestResult,
    spy_bars: list[Candle],
    exposure_samples: list[float],
    config: BacktestConfig,
) -> dict:
    curve = [equity for _, equity in result.equity_curve]
    final_equity = curve[-1]
    total_return = final_equity / config.starting_equity - 1.0

    spy_window = [bar for bar in spy_bars if result.start_date <= bar.time.date() <= result.end_date]
    spy_return = (spy_window[-1].close / spy_window[0].open - 1.0) if len(spy_window) >= 2 else None

    peak = curve[0]
    max_drawdown = 0.0
    for equity in curve:
        peak = max(peak, equity)
        max_drawdown = min(max_drawdown, equity / peak - 1.0)

    closed = result.trades
    wins = [t.pnl for t in closed if t.pnl > 0]
    losses = [t.pnl for t in closed if t.pnl <= 0]
    unrealized = sum(p["unrealized_pnl"] for p in result.ending_positions.values())
    metrics = {
        "starting_equity": config.starting_equity,
        "final_equity": round(final_equity, 2),
        "total_return_pct": round(total_return * 100, 2),
        "spy_return_pct": round(spy_return * 100, 2) if spy_return is not None else None,
        "beats_spy": (total_return > spy_return) if spy_return is not None else None,
        "max_drawdown_pct": round(max_drawdown * 100, 2),
        "closed_trades": len(closed),
        "win_rate_pct": round(100 * len(wins) / len(closed), 1) if closed else None,
        "avg_win": round(sum(wins) / len(wins), 2) if wins else 0.0,
        "avg_loss": round(sum(losses) / len(losses), 2) if losses else 0.0,
        "profit_factor": round(sum(wins) / abs(sum(losses)), 2) if losses and sum(losses) != 0 else None,
        "realized_pnl": round(sum(t.pnl for t in closed), 2),
        "unrealized_pnl": round(unrealized, 2),
        "avg_days_held": round(sum(t.days_held for t in closed) / len(closed), 1) if closed else None,
        "avg_exposure_pct": round(100 * sum(exposure_samples) / len(exposure_samples), 1) if exposure_samples else 0.0,
        "open_positions_at_end": len(result.ending_positions),
    }
    return metrics


def _per_symbol_breakdown(result: BacktestResult) -> dict[str, dict]:
    breakdown: dict[str, dict] = {}
    for trade in result.trades:
        entry = breakdown.setdefault(
            trade.symbol,
            {"realized_pnl": 0.0, "trades": 0, "wins": 0, "unrealized_pnl": 0.0},
        )
        entry["realized_pnl"] += trade.pnl
        entry["trades"] += 1
        if trade.pnl > 0:
            entry["wins"] += 1
    for symbol, open_pos in result.ending_positions.items():
        entry = breakdown.setdefault(
            symbol,
            {"realized_pnl": 0.0, "trades": 0, "wins": 0, "unrealized_pnl": 0.0},
        )
        entry["unrealized_pnl"] += open_pos["unrealized_pnl"]
    for symbol, entry in breakdown.items():
        entry["realized_pnl"] = round(entry["realized_pnl"], 2)
        entry["unrealized_pnl"] = round(entry["unrealized_pnl"], 2)
        entry["total_pnl"] = round(entry["realized_pnl"] + entry["unrealized_pnl"], 2)
        entry["win_rate_pct"] = round(100 * entry["wins"] / entry["trades"], 1) if entry["trades"] else None
    return dict(sorted(breakdown.items(), key=lambda item: item[1]["total_pnl"], reverse=True))
