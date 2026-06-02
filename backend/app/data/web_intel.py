from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class IntelSignal:
    source: str
    sentiment: float
    relevance: float
    confidence: float
    timestamp: datetime
    headline: str


class WebIntelCollector:
    """Neutral collector until real news/social providers are wired in."""

    def collect(self, symbols: list[str]) -> dict[str, list[IntelSignal]]:
        return {symbol: [] for symbol in symbols}
