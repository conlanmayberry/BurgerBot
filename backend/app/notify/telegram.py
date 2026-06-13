from __future__ import annotations

import logging
import time
from collections import defaultdict

import httpx

from backend.app.decision.engine import TradingDecision
from backend.app.execution.trader import TradeResult


logger = logging.getLogger(__name__)


class TelegramNotifier:
    def __init__(self, bot_token: str, chat_id: str) -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id
        self._check_cache: dict | None = None
        self._check_cached_at: float = 0.0

    def check_connection(self, ttl_seconds: float = 300.0) -> dict:
        """Verify the bot token against Telegram's getMe endpoint, cached so
        dashboard polling does not hammer the API."""
        if not self.bot_token or not self.chat_id:
            return {"status": "off", "detail": "Bot token or chat id not configured"}
        now = time.monotonic()
        if self._check_cache is not None and now - self._check_cached_at < ttl_seconds:
            return self._check_cache
        url = f"https://api.telegram.org/bot{self.bot_token}/getMe"
        try:
            with httpx.Client(timeout=5.0) as client:
                response = client.get(url)
                response.raise_for_status()
                payload = response.json()
            username = payload.get("result", {}).get("username", "unknown")
            result = {"status": "ok", "detail": f"Connected as @{username}"}
        except Exception as exc:  # noqa: BLE001
            logger.warning("Telegram connection check failed: %s", exc)
            result = {"status": "error", "detail": f"getMe failed: {exc}"}
        self._check_cache = result
        self._check_cached_at = now
        return result

    def notify_trade_summary(self, trades: list[tuple[TradingDecision, TradeResult]]) -> None:
        if not trades:
            return
        totals: dict[str, int] = defaultdict(int)
        action_groups: dict[str, list[str]] = defaultdict(list)
        for decision, trade in trades:
            totals[decision.action] += trade.qty
            action_groups[decision.action].append(f"{decision.symbol} x{trade.qty}")

        lines = ["Trade summary", ""]
        if action_groups.get("BUY"):
            lines.append("Bought:")
            lines.extend(f"- {item}" for item in action_groups["BUY"])
            lines.append(f"Total bought: {totals['BUY']} shares")
            lines.append("")
        if action_groups.get("SELL"):
            lines.append("Sold:")
            lines.extend(f"- {item}" for item in action_groups["SELL"])
            lines.append(f"Total sold: {totals['SELL']} shares")
        text = "\n".join(lines)
        self._send(text)

    def _send(self, text: str) -> None:
        if not self.bot_token or not self.chat_id:
            logger.info("Telegram notification skipped because bot token or chat id is missing")
            return
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        try:
            with httpx.Client(timeout=5.0) as client:
                response = client.post(url, json={"chat_id": self.chat_id, "text": text})
                response.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Telegram notification failed: %s", exc)
            return
