from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from backend.app.data.alpaca_client import AlpacaClient
from backend.app.decision.engine import TradingDecision
from backend.app.risk.engine import PortfolioState


@dataclass
class TradeResult:
    submitted: bool
    order_id: str
    reason: str
    qty: int


class Trader:
    def __init__(self, alpaca: AlpacaClient, max_position_pct: float, enabled: bool = True) -> None:
        self.alpaca = alpaca
        self.max_position_pct = max_position_pct
        self.enabled = enabled

    def execute(self, decision: TradingDecision, state: PortfolioState, estimated_price: float) -> TradeResult:
        if not self.enabled:
            return TradeResult(submitted=False, order_id="", reason="Auto-execution disabled", qty=0)
        if estimated_price <= 0:
            return TradeResult(submitted=False, order_id="", reason="Estimated price unavailable", qty=0)

        side = "buy" if decision.action == "BUY" else "sell"
        qty = self._estimate_quantity(state, decision.symbol, decision.action, decision.confidence, estimated_price)
        if qty <= 0:
            return TradeResult(submitted=False, order_id="", reason="Calculated quantity was zero", qty=0)
        client_order_id = f"{decision.symbol}-{int(datetime.now(tz=timezone.utc).timestamp())}"
        payload = self.alpaca.submit_order(
            symbol=decision.symbol,
            side=side,
            qty=qty,
            client_order_id=client_order_id,
        )
        return TradeResult(submitted=True, order_id=str(payload.get("id", "")), reason="submitted", qty=qty)

    def _estimate_quantity(
        self,
        state: PortfolioState,
        symbol: str,
        action: str,
        confidence: float,
        estimated_price: float,
    ) -> int:
        if action == "SELL":
            return state.quantity_for(symbol)
        allocation_pct = min(self.max_position_pct, 0.02 + confidence * max(0.0, self.max_position_pct - 0.02))
        budget = min(state.cash * 0.95, state.equity * allocation_pct)
        return int(budget / estimated_price)
