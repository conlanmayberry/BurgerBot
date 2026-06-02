from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx

from backend.app.data.intel_models import IntelSignal


logger = logging.getLogger(__name__)


class FinnhubAnalystProvider:
    base_url = "https://finnhub.io/api/v1/stock/recommendation"

    def __init__(
        self,
        api_key: str = "",
        enabled: bool = True,
        timeout_seconds: float = 10.0,
    ) -> None:
        self.api_key = api_key
        self.enabled = enabled
        self.timeout_seconds = timeout_seconds

    def collect(self, symbol: str) -> list[IntelSignal]:
        if not self.enabled or not self.api_key:
            return []

        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                response = client.get(
                    self.base_url,
                    params={"symbol": symbol, "token": self.api_key},
                )
                response.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Finnhub analyst request failed for %s: %s", symbol, exc)
            return []

        payload = response.json()
        return self._signals_from_payload(symbol, payload)

    def _signals_from_payload(self, symbol: str, payload: list[dict]) -> list[IntelSignal]:
        if not payload:
            return []

        latest_entry = max(payload, key=self._entry_sort_key)
        signal = self._signal_from_entry(symbol, latest_entry)
        if signal is None:
            return []
        return [signal]

    def _signal_from_entry(self, symbol: str, entry: dict) -> IntelSignal | None:
        strong_buy = self._to_int(entry.get("strongBuy"))
        buy = self._to_int(entry.get("buy"))
        hold = self._to_int(entry.get("hold"))
        sell = self._to_int(entry.get("sell"))
        strong_sell = self._to_int(entry.get("strongSell"))
        total = strong_buy + buy + hold + sell + strong_sell
        if total <= 0:
            return None

        score = (
            strong_buy
            + 0.5 * buy
            - 0.5 * sell
            - strong_sell
        ) / total

        confidence = min(1.0, 0.35 + (total / 25.0))
        relevance = min(1.0, 0.4 + (total / 30.0))
        period = self._parse_period(entry.get("period"))

        headline = (
            f"Analyst consensus for {symbol}: "
            f"{strong_buy} strong buy, {buy} buy, {hold} hold, "
            f"{sell} sell, {strong_sell} strong sell"
        )

        return IntelSignal(
            source="analyst",
            sentiment=max(-1.0, min(1.0, score)),
            relevance=relevance,
            confidence=confidence,
            timestamp=period,
            headline=headline,
        )

    @staticmethod
    def _entry_sort_key(entry: dict) -> datetime:
        return FinnhubAnalystProvider._parse_period(entry.get("period"))

    @staticmethod
    def _parse_period(value: str | None) -> datetime:
        if not value:
            return datetime.now(tz=timezone.utc)
        try:
            return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
        except ValueError:
            logger.warning("Unexpected Finnhub analyst period format: %s", value)
            return datetime.now(tz=timezone.utc)

    @staticmethod
    def _to_int(value: object) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0
