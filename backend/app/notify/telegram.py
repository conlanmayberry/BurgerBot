from __future__ import annotations

import httpx

from backend.app.decision.engine import TradingDecision
from backend.app.execution.trader import TradeResult


class TelegramNotifier:
    def __init__(self, bot_token: str, chat_id: str) -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id

    def notify_trade(self, decision: TradingDecision, trade: TradeResult) -> None:
        text = (
            f"Trade update\n"
            f"Symbol: {decision.symbol}\n"
            f"Action: {decision.action}\n"
            f"Confidence: {decision.confidence:.2f}\n"
            f"Submitted: {trade.submitted}\n"
            f"Order ID: {trade.order_id or 'n/a'}"
        )
        self._send(text)

    def notify_blocked(self, decision: TradingDecision, reason: str) -> None:
        text = (
            f"Trade blocked\n"
            f"Symbol: {decision.symbol}\n"
            f"Action: {decision.action}\n"
            f"Confidence: {decision.confidence:.2f}\n"
            f"Reason: {reason}"
        )
        self._send(text)

    def _send(self, text: str) -> None:
        if not self.bot_token or not self.chat_id:
            return
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        try:
            with httpx.Client(timeout=5.0) as client:
                client.post(url, json={"chat_id": self.chat_id, "text": text})
        except Exception:
            return
