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
        )

    def quantity_for(self, symbol: str) -> int:
        if not self.quantities:
            return 0
        return self.quantities.get(symbol.upper(), 0)

    def has_pending_buy(self, symbol: str) -> bool:
        if not self.pending_buy_symbols:
            return False
        return symbol.upper() in self.pending_buy_symbols

    def total_buy_exposure_symbols(self) -> set[str]:
        pending = self.pending_buy_symbols or set()
        return self.open_symbols | pending

    @staticmethod
    def _order_qty(order: dict) -> int:
        try:
            return int(float(order.get("qty") or 0))
        except (TypeError, ValueError):
            return 0


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

    def evaluate(self, decision: TradingDecision, state: PortfolioState) -> RiskResult:
        if decision.action == "HOLD":
            return RiskResult(False, "No trade action")
        if decision.confidence < self.min_confidence:
            return RiskResult(False, "Confidence below threshold")
        if state.day_pnl_pct < -self.max_daily_drawdown_pct:
            return RiskResult(False, "Daily drawdown limit reached")
        if decision.action == "SELL" and state.quantity_for(decision.symbol) <= 0:
            return RiskResult(False, "No open position to sell")
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
