from __future__ import annotations

from dataclasses import asdict, dataclass

from backend.app.features.sentiment import SentimentFeatures
from backend.app.features.technical import TechnicalFeatures


@dataclass
class TradingDecision:
    symbol: str
    action: str
    confidence: float
    score: float
    rationale: dict

    def to_payload(self) -> dict:
        return asdict(self)


class DecisionEngine:
    def decide(self, symbol: str, technical: TechnicalFeatures, sentiment: SentimentFeatures) -> TradingDecision:
        tech_score = (
            0.35 * technical.momentum_5
            + 0.35 * technical.momentum_20
            + 0.20 * technical.trend_strength
            + 0.10 * min(1.0, technical.volume_ratio - 1.0)
        )
        risk_penalty = min(0.5, technical.volatility_20 * 2.0)
        sentiment_score = (
            0.6 * sentiment.weighted_sentiment
            + 0.2 * sentiment.analyst_bias
            + 0.2 * sentiment.social_bias
        ) * sentiment.signal_confidence
        score = tech_score + 0.8 * sentiment_score - risk_penalty
        confidence = max(0.0, min(1.0, abs(score) * 2.2))

        if score > 0.12:
            action = "BUY"
        elif score < -0.12:
            action = "SELL"
        else:
            action = "HOLD"

        return TradingDecision(
            symbol=symbol,
            action=action,
            confidence=confidence,
            score=score,
            rationale={
                "tech_score": round(tech_score, 4),
                "sentiment_score": round(sentiment_score, 4),
                "risk_penalty": round(risk_penalty, 4),
                "volatility_20": round(technical.volatility_20, 4),
            },
        )
