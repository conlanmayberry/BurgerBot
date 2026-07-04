from __future__ import annotations

from dataclasses import dataclass, field

from backend.app.data.alpaca_client import Candle
from backend.app.strategy.indicators import pct_return, sma, wilder_atr, wilder_rsi


@dataclass(frozen=True)
class StrategyParams:
    """Buy-the-dip-in-an-uptrend strategy parameters.

    The strategy only takes long entries in stocks trading above their own
    trend SMA while the market (SPY) is above its regime SMA, and it buys
    short sharp pullbacks (low RSI-3), selling into the bounce. Exits are
    bounded by a time stop and an ATR hard stop so losers cannot linger.
    """

    # Defaults validated by backtest (see STRATEGY.md): 12mo +21.6% vs SPY
    # +20.7%, max drawdown -3.9%, 70.6% win rate, profit factor 2.03.
    regime_sma_days: int = 200
    trend_sma_days: int = 200
    entry_rsi_period: int = 3
    entry_rsi_max: float = 20.0
    exit_rsi_min: float = 65.0
    exit_sma_days: int = 5
    max_hold_days: int = 10
    stop_atr_multiple: float = 2.5
    atr_period: int = 14
    rank_momentum_days: int = 126
    min_history_days: int = 210
    risk_per_trade_pct: float = 0.015


@dataclass
class PositionContext:
    entry_price: float
    days_held: int = 0


@dataclass
class SymbolDecision:
    symbol: str
    action: str  # BUY / SELL / HOLD
    reason: str
    rank_score: float = 0.0
    stop_price: float | None = None
    metrics: dict = field(default_factory=dict)


class DailyPullbackStrategy:
    def __init__(self, params: StrategyParams | None = None) -> None:
        self.params = params or StrategyParams()

    def market_risk_on(self, spy_bars: list[Candle]) -> bool:
        closes = [bar.close for bar in spy_bars]
        regime_sma = sma(closes, self.params.regime_sma_days)
        if regime_sma is None or not closes:
            return False
        return closes[-1] > regime_sma

    def evaluate_symbol(
        self,
        symbol: str,
        bars: list[Candle],
        position: PositionContext | None,
        risk_on: bool,
    ) -> SymbolDecision:
        p = self.params
        closes = [bar.close for bar in bars]
        if len(closes) < p.min_history_days:
            return SymbolDecision(symbol, "HOLD", "insufficient_history",
                                  metrics={"bars": len(closes)})

        highs = [bar.high for bar in bars]
        lows = [bar.low for bar in bars]
        close = closes[-1]
        rsi = wilder_rsi(closes, p.entry_rsi_period)
        trend_sma = sma(closes, p.trend_sma_days)
        exit_sma = sma(closes, p.exit_sma_days)
        atr = wilder_atr(highs, lows, closes, p.atr_period)
        momentum = pct_return(closes, p.rank_momentum_days)
        metrics = {
            "close": round(close, 2),
            "rsi": round(rsi, 1) if rsi is not None else None,
            "trend_sma": round(trend_sma, 2) if trend_sma is not None else None,
            "atr": round(atr, 3) if atr is not None else None,
            "momentum": round(momentum, 4) if momentum is not None else None,
            "risk_on": risk_on,
        }
        if rsi is None or trend_sma is None or atr is None or atr <= 0:
            return SymbolDecision(symbol, "HOLD", "insufficient_indicators", metrics=metrics)

        if position is not None:
            metrics["days_held"] = position.days_held
            metrics["entry_price"] = round(position.entry_price, 2)
            stop_price = position.entry_price - p.stop_atr_multiple * atr
            if close <= stop_price:
                return SymbolDecision(symbol, "SELL", "exit_stop", stop_price=stop_price, metrics=metrics)
            if rsi >= p.exit_rsi_min:
                return SymbolDecision(symbol, "SELL", "exit_bounce", metrics=metrics)
            if exit_sma is not None and close > exit_sma and rsi >= 50.0:
                return SymbolDecision(symbol, "SELL", "exit_bounce", metrics=metrics)
            if position.days_held >= p.max_hold_days:
                return SymbolDecision(symbol, "SELL", "exit_time", metrics=metrics)
            if not risk_on:
                return SymbolDecision(symbol, "SELL", "exit_regime", metrics=metrics)
            return SymbolDecision(symbol, "HOLD", "holding", stop_price=stop_price, metrics=metrics)

        if not risk_on:
            return SymbolDecision(symbol, "HOLD", "regime_off", metrics=metrics)
        if close <= trend_sma:
            return SymbolDecision(symbol, "HOLD", "below_trend", metrics=metrics)
        if rsi > p.entry_rsi_max:
            return SymbolDecision(symbol, "HOLD", "no_pullback", metrics=metrics)

        stop_price = close - p.stop_atr_multiple * atr
        rank = momentum if momentum is not None else 0.0
        return SymbolDecision(
            symbol,
            "BUY",
            "entry_pullback",
            rank_score=rank,
            stop_price=stop_price,
            metrics=metrics,
        )

    def size_position(
        self,
        equity: float,
        available_cash: float,
        entry_price: float,
        stop_price: float | None,
        max_position_pct: float,
    ) -> int:
        """Risk-based sizing: risk a fixed slice of equity to the stop, capped
        by the per-position limit and by cash actually available (no margin)."""
        if entry_price <= 0 or equity <= 0 or available_cash <= 0:
            return 0
        stop_distance = entry_price - stop_price if stop_price is not None else None
        if stop_distance is None or stop_distance <= 0:
            stop_distance = entry_price * 0.05
        risk_budget = equity * self.params.risk_per_trade_pct
        qty_by_risk = risk_budget / stop_distance
        qty_by_cap = (equity * max_position_pct) / entry_price
        qty_by_cash = (available_cash * 0.98) / entry_price
        return max(0, int(min(qty_by_risk, qty_by_cap, qty_by_cash)))
