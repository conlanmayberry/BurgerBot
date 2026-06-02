from __future__ import annotations

from dataclasses import dataclass
from statistics import mean

from backend.app.data.intel_models import IntelSignal


@dataclass
class SentimentFeatures:
    weighted_sentiment: float
    analyst_bias: float
    social_bias: float
    signal_confidence: float


class SentimentFeatureBuilder:
    def build(self, symbol: str, signals: list[IntelSignal]) -> SentimentFeatures:
        _ = symbol
        if not signals:
            return SentimentFeatures(0.0, 0.0, 0.0, 0.0)

        weighted_scores: list[float] = []
        confidences: list[float] = []
        analyst: list[float] = []
        social: list[float] = []
        for signal in signals:
            weight = signal.relevance * signal.confidence
            weighted_scores.append(signal.sentiment * weight)
            confidences.append(signal.confidence)
            if signal.source == "analyst":
                analyst.append(signal.sentiment)
            if signal.source == "social":
                social.append(signal.sentiment)

        norm = sum(max(0.001, s.relevance * s.confidence) for s in signals)
        weighted_sentiment = sum(weighted_scores) / norm
        return SentimentFeatures(
            weighted_sentiment=max(-1.0, min(1.0, weighted_sentiment)),
            analyst_bias=mean(analyst) if analyst else 0.0,
            social_bias=mean(social) if social else 0.0,
            signal_confidence=mean(confidences),
        )
