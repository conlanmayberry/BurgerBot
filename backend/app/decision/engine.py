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
    buy_threshold = 0.35
    sell_threshold = -0.35
    data_quality_threshold = 0.65

    def decide(self, symbol: str, technical: TechnicalFeatures, sentiment: SentimentFeatures) -> TradingDecision:
        momentum_score = (
            0.25 * self._scale(technical.momentum_5, 0.03)
            + 0.35 * self._scale(technical.momentum_20, 0.08)
            + 0.20 * self._scale(technical.momentum_60, 0.16)
            + 0.20 * self._scale(technical.trend_strength, 0.04)
        )
        rsi_score = self._rsi_score(technical.rsi_14)
        macd_score = self._scale(technical.macd_histogram / max(technical.latest_price, 0.01), 0.01)
        vwap_score = self._scale(technical.vwap_distance, 0.04)
        volume_boost = max(0.0, min(0.12, (technical.volume_ratio - 1.0) * 0.08))
        tech_score = 0.55 * momentum_score + 0.18 * rsi_score + 0.17 * macd_score + 0.10 * vwap_score

        sentiment_score = 0.0
        if sentiment.signal_confidence >= 0.5:
            sentiment_score = (
                0.60 * sentiment.weighted_sentiment
                + 0.40 * sentiment.analyst_bias
            ) * sentiment.signal_confidence

        risk_penalty = min(0.45, technical.atr_pct_14 * 6.0 + technical.volatility_20 * 3.0)
        raw_score = tech_score + 0.25 * sentiment_score
        score = raw_score + volume_boost if raw_score > 0 else raw_score - volume_boost
        if score > 0:
            score = max(0.0, score - risk_penalty)
        elif score < 0:
            score = min(0.0, score + risk_penalty)
        confidence = max(0.0, min(1.0, abs(score) * technical.data_quality))

        if score > self.buy_threshold and technical.data_quality >= self.data_quality_threshold:
            action = "BUY"
        elif score < self.sell_threshold and technical.data_quality >= self.data_quality_threshold:
            action = "SELL"
        else:
            action = "HOLD"

        return TradingDecision(
            symbol=symbol,
            action=action,
            confidence=confidence,
            score=score,
            rationale={
                "momentum_score": round(momentum_score, 4),
                "rsi_score": round(rsi_score, 4),
                "macd_score": round(macd_score, 4),
                "vwap_score": round(vwap_score, 4),
                "tech_score": round(tech_score, 4),
                "sentiment_score": round(sentiment_score, 4),
                "weighted_sentiment": round(sentiment.weighted_sentiment, 4),
                "analyst_bias": round(sentiment.analyst_bias, 4),
                "sentiment_confidence": round(sentiment.signal_confidence, 4),
                "volume_boost": round(volume_boost, 4),
                "risk_penalty": round(risk_penalty, 4),
                "volatility_20": round(technical.volatility_20, 4),
                "atr_pct_14": round(technical.atr_pct_14, 4),
                "rsi_14": round(technical.rsi_14, 2),
                "data_quality": round(technical.data_quality, 2),
                "buy_threshold": self.buy_threshold,
                "sell_threshold": self.sell_threshold,
                "data_quality_threshold": self.data_quality_threshold,
            },
        )

    def _scale(self, value: float, expected_range: float) -> float:
        if expected_range <= 0:
            return 0.0
        return max(-1.0, min(1.0, value / expected_range))

    def _rsi_score(self, rsi: float) -> float:
        if rsi >= 70:
            return -min(1.0, (rsi - 70) / 20)
        if rsi <= 30:
            return min(1.0, (30 - rsi) / 20)
        return max(-1.0, min(1.0, (rsi - 50) / 20))
