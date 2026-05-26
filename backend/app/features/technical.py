from __future__ import annotations

from dataclasses import dataclass
from statistics import mean

from backend.app.data.alpaca_client import Candle


@dataclass
class TechnicalFeatures:
    momentum_5: float
    momentum_20: float
    volatility_20: float
    volume_ratio: float
    trend_strength: float


class TechnicalFeatureBuilder:
    def build(self, symbol: str, candles: list[Candle]) -> TechnicalFeatures:
        _ = symbol
        if len(candles) < 21:
            return TechnicalFeatures(0.0, 0.0, 0.0, 1.0, 0.0)

        closes = [c.close for c in candles]
        volumes = [c.volume for c in candles]
        momentum_5 = closes[-1] / closes[-6] - 1.0
        momentum_20 = closes[-1] / closes[-21] - 1.0
        returns = [(closes[i] / closes[i - 1] - 1.0) for i in range(1, len(closes))]
        recent_returns = returns[-20:]
        volatility_20 = (sum(r * r for r in recent_returns) / max(1, len(recent_returns))) ** 0.5
        avg_volume_20 = mean(volumes[-20:])
        volume_ratio = volumes[-1] / avg_volume_20 if avg_volume_20 > 0 else 1.0
        sma_10 = mean(closes[-10:])
        sma_20 = mean(closes[-20:])
        trend_strength = (sma_10 / sma_20 - 1.0) if sma_20 > 0 else 0.0
        return TechnicalFeatures(
            momentum_5=momentum_5,
            momentum_20=momentum_20,
            volatility_20=volatility_20,
            volume_ratio=volume_ratio,
            trend_strength=trend_strength,
        )
