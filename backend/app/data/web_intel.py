from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import random


@dataclass
class IntelSignal:
    source: str
    sentiment: float
    relevance: float
    confidence: float
    timestamp: datetime
    headline: str


class WebIntelCollector:
    """
    Placeholder collector for phase 1.
    Replace this with real providers (news APIs, social sentiment, analyst feeds).
    """

    def collect(self, symbols: list[str]) -> dict[str, list[IntelSignal]]:
        now = datetime.now(tz=timezone.utc)
        data: dict[str, list[IntelSignal]] = {}
        for symbol in symbols:
            data[symbol] = [
                IntelSignal(
                    source="news",
                    sentiment=random.uniform(-1.0, 1.0),
                    relevance=random.uniform(0.4, 1.0),
                    confidence=random.uniform(0.4, 1.0),
                    timestamp=now,
                    headline=f"{symbol} market coverage update",
                ),
                IntelSignal(
                    source="social",
                    sentiment=random.uniform(-1.0, 1.0),
                    relevance=random.uniform(0.2, 1.0),
                    confidence=random.uniform(0.2, 0.9),
                    timestamp=now,
                    headline=f"{symbol} social sentiment pulse",
                ),
                IntelSignal(
                    source="analyst",
                    sentiment=random.uniform(-1.0, 1.0),
                    relevance=random.uniform(0.6, 1.0),
                    confidence=random.uniform(0.6, 1.0),
                    timestamp=now,
                    headline=f"{symbol} analyst commentary",
                ),
            ]
        return data
