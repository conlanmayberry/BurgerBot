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
