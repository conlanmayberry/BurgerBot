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


class Trader:
    def __init__(self, alpaca: AlpacaClient, enabled: bool = True) -> None:
        self.alpaca = alpaca
        self.enabled = enabled

    def execute(self, decision: TradingDecision, state: PortfolioState) -> TradeResult:
        if not self.enabled:
            return TradeResult(submitted=False, order_id="", reason="Auto-execution disabled")

        side = "buy" if decision.action == "BUY" else "sell"
        qty = self._estimate_quantity(state, decision.confidence)
        if qty <= 0:
            return TradeResult(submitted=False, order_id="", reason="Calculated quantity was zero")
        client_order_id = f"{decision.symbol}-{int(datetime.now(tz=timezone.utc).timestamp())}"
        payload = self.alpaca.submit_order(
            symbol=decision.symbol,
            side=side,
            qty=qty,
            client_order_id=client_order_id,
        )
        return TradeResult(submitted=True, order_id=str(payload.get("id", "")), reason="submitted")

    def _estimate_quantity(self, state: PortfolioState, confidence: float) -> int:
        budget = state.equity * min(0.08, 0.02 + confidence * 0.06)
        # Uses synthetic 100$ placeholder if real quote is unavailable in this phase.
        estimated_price = 100.0
        return int(budget / estimated_price)
