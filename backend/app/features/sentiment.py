from __future__ import annotations

from dataclasses import dataclass
from statistics import mean

from backend.app.data.intel_models import IntelSignal


@dataclass
class SentimentFeatures:
    weighted_sentiment: float
    analyst_bias: float
    signal_confidence: float


class SentimentFeatureBuilder:
    def build(self, symbol: str, signals: list[IntelSignal]) -> SentimentFeatures:
        _ = symbol
        decision_signals = [signal for signal in signals if signal.source in {"analyst", "news"}]
        if not decision_signals:
            return SentimentFeatures(0.0, 0.0, 0.0)

        weighted_scores: list[float] = []
        confidences: list[float] = []
        analyst: list[float] = []
        for signal in decision_signals:
            weight = signal.relevance * signal.confidence
            weighted_scores.append(signal.sentiment * weight)
            confidences.append(signal.confidence)
            if signal.source == "analyst":
                analyst.append(signal.sentiment)

        norm = sum(max(0.001, s.relevance * s.confidence) for s in decision_signals)
        weighted_sentiment = sum(weighted_scores) / norm
        return SentimentFeatures(
            weighted_sentiment=max(-1.0, min(1.0, weighted_sentiment)),
            analyst_bias=mean(analyst) if analyst else 0.0,
            signal_confidence=mean(confidences),
        )
