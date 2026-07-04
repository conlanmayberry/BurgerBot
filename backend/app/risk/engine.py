from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from backend.app.decision.engine import TradingDecision


@dataclass
class PortfolioState:
    equity: float
    cash: float
    day_pnl_pct: float
    open_symbols: set[str]
    quantities: dict[str, int] | None = None
    pending_order_symbols: set[str] | None = None
    pending_buy_symbols: set[str] | None = None
    pending_sell_symbols: set[str] | None = None
    pending_buy_quantities: dict[str, int] | None = None
    unrealized_plpc: dict[str, float] | None = None
    unrealized_pl: dict[str, float] | None = None
    current_prices: dict[str, float] | None = None
    avg_entry_prices: dict[str, float] | None = None
    loss_streak_count: int = 0
    last_loss_at: datetime | None = None

    @classmethod
    def from_api_payload(cls, payload: dict) -> "PortfolioState":
        positions = payload.get("positions", [])
        open_orders = payload.get("open_orders", [])
        pending_order_symbols = {
            order.get("symbol", "").upper()
            for order in open_orders
            if order.get("symbol")
        }
        pending_buy_symbols = {
            order.get("symbol", "").upper()
            for order in open_orders
            if order.get("symbol") and str(order.get("side", "")).lower() == "buy"
        }
        pending_sell_symbols = {
            order.get("symbol", "").upper()
            for order in open_orders
            if order.get("symbol") and str(order.get("side", "")).lower() == "sell"
        }
        pending_buy_quantities: dict[str, int] = {}
        for order in open_orders:
            symbol = order.get("symbol", "").upper()
            if not symbol or str(order.get("side", "")).lower() != "buy":
                continue
            pending_buy_quantities[symbol] = pending_buy_quantities.get(symbol, 0) + cls._order_qty(order)
        return cls(
            equity=float(payload.get("equity", 0.0)),
            cash=float(payload.get("cash", 0.0)),
            day_pnl_pct=float(payload.get("day_pnl_pct", 0.0)),
            open_symbols={p.get("symbol", "").upper() for p in positions},
            quantities={
                p.get("symbol", "").upper(): int(float(p.get("qty", 0)))
                for p in positions
                if p.get("symbol")
            },
            pending_order_symbols=pending_order_symbols,
            pending_buy_symbols=pending_buy_symbols,
            pending_sell_symbols=pending_sell_symbols,
            pending_buy_quantities=pending_buy_quantities,
            unrealized_plpc={
                p.get("symbol", "").upper(): cls._to_float(p.get("unrealized_plpc"))
                for p in positions
                if p.get("symbol") and p.get("unrealized_plpc") is not None
            },
            unrealized_pl={
                p.get("symbol", "").upper(): cls._to_float(p.get("unrealized_pl"))
                for p in positions
                if p.get("symbol") and p.get("unrealized_pl") is not None
            },
            current_prices={
                p.get("symbol", "").upper(): cls._to_float(p.get("current_price"))
                for p in positions
                if p.get("symbol") and p.get("current_price") is not None
            },
            avg_entry_prices={
                p.get("symbol", "").upper(): cls._to_float(p.get("avg_entry_price"))
                for p in positions
                if p.get("symbol") and p.get("avg_entry_price") is not None
            },
        )

    def quantity_for(self, symbol: str) -> int:
        if not self.quantities:
            return 0
        return self.quantities.get(symbol.upper(), 0)

    def has_pending_buy(self, symbol: str) -> bool:
        if not self.pending_buy_symbols:
            return False
        return symbol.upper() in self.pending_buy_symbols

    def has_pending_sell(self, symbol: str) -> bool:
        if not self.pending_sell_symbols:
            return False
        return symbol.upper() in self.pending_sell_symbols

    def unrealized_plpc_for(self, symbol: str) -> float | None:
        if not self.unrealized_plpc:
            return None
        return self.unrealized_plpc.get(symbol.upper())

    def current_price_for(self, symbol: str) -> float:
        if not self.current_prices:
            return 0.0
        return self.current_prices.get(symbol.upper(), 0.0)

    def avg_entry_price_for(self, symbol: str) -> float:
        if not self.avg_entry_prices:
            return 0.0
        return self.avg_entry_prices.get(symbol.upper(), 0.0)

    def total_buy_exposure_symbols(self) -> set[str]:
        pending = self.pending_buy_symbols or set()
        return self.open_symbols | pending

    @staticmethod
    def _order_qty(order: dict) -> int:
        try:
            return int(float(order.get("qty") or 0))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _to_float(value: object) -> float:
        try:
            return float(value or 0.0)
        except (TypeError, ValueError):
            return 0.0


@dataclass
class RiskResult:
    allowed: bool
    reason: str


class RiskEngine:
    def __init__(
        self,
        max_position_pct: float,
        max_daily_drawdown_pct: float,
        max_open_positions: int,
        min_confidence: float,
        cooldown_minutes_after_loss: int,
    ) -> None:
        self.max_position_pct = max_position_pct
        self.max_daily_drawdown_pct = max_daily_drawdown_pct
        self.max_open_positions = max_open_positions
        self.min_confidence = min_confidence
        self.cooldown_minutes_after_loss = cooldown_minutes_after_loss

    def evaluate(self, decision: TradingDecision, state: PortfolioState, market_open: bool = True) -> RiskResult:
        if decision.action == "HOLD":
            return RiskResult(False, "No trade action")
        if not market_open:
            return RiskResult(False, "Market closed")
        if decision.confidence < self.min_confidence:
            return RiskResult(False, "Confidence below threshold")
        # The drawdown circuit breaker stops adding risk but must never block
        # reducing exposure: stop-loss sells have to work on a bad day.
        if decision.action == "BUY" and state.day_pnl_pct < -self.max_daily_drawdown_pct:
            return RiskResult(False, "Daily drawdown limit reached")
        if decision.action == "SELL" and state.quantity_for(decision.symbol) <= 0:
            return RiskResult(False, "No open position to sell")
        if decision.action == "SELL" and state.has_pending_sell(decision.symbol):
            return RiskResult(False, "Pending sell order already open")
        if decision.action == "BUY" and state.has_pending_buy(decision.symbol):
            return RiskResult(False, "Pending buy order already open")
        if decision.action == "BUY" and decision.symbol in state.open_symbols:
            return RiskResult(False, "Position already open")
        if decision.action == "BUY" and len(state.total_buy_exposure_symbols()) >= self.max_open_positions:
            return RiskResult(False, "Max open positions reached")
        if state.last_loss_at:
            cooldown_until = state.last_loss_at + timedelta(minutes=self.cooldown_minutes_after_loss)
            if datetime.now(tz=timezone.utc) < cooldown_until:
                return RiskResult(False, "Cooldown after loss is active")
        return RiskResult(True, "Approved")
