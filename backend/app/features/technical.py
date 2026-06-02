from __future__ import annotations

from dataclasses import dataclass
from statistics import mean

from backend.app.data.alpaca_client import Candle


@dataclass
class TechnicalFeatures:
    latest_price: float
    momentum_5: float
    momentum_20: float
    momentum_60: float
    volatility_20: float
    atr_pct_14: float
    volume_ratio: float
    trend_strength: float
    rsi_14: float
    macd_histogram: float
    vwap_distance: float
    data_quality: float


class TechnicalFeatureBuilder:
    def build(self, symbol: str, candles: list[Candle]) -> TechnicalFeatures:
        _ = symbol
        if len(candles) < 21:
            return TechnicalFeatures(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 50.0, 0.0, 0.0, 0.0)

        closes = [c.close for c in candles]
        volumes = [c.volume for c in candles]
        latest_price = closes[-1]
        momentum_5 = closes[-1] / closes[-6] - 1.0
        momentum_20 = closes[-1] / closes[-21] - 1.0
        momentum_60 = closes[-1] / closes[-61] - 1.0 if len(closes) >= 61 else momentum_20
        returns = [(closes[i] / closes[i - 1] - 1.0) for i in range(1, len(closes))]
        recent_returns = returns[-20:]
        volatility_20 = (sum(r * r for r in recent_returns) / max(1, len(recent_returns))) ** 0.5
        true_ranges = [
            max(candles[i].high - candles[i].low, abs(candles[i].high - candles[i - 1].close), abs(candles[i].low - candles[i - 1].close))
            for i in range(1, len(candles))
        ]
        atr_14 = mean(true_ranges[-14:]) if true_ranges else 0.0
        atr_pct_14 = atr_14 / latest_price if latest_price > 0 else 0.0
        avg_volume_20 = mean(volumes[-20:])
        volume_ratio = volumes[-1] / avg_volume_20 if avg_volume_20 > 0 else 1.0
        sma_10 = mean(closes[-10:])
        sma_20 = mean(closes[-20:])
        trend_strength = (sma_10 / sma_20 - 1.0) if sma_20 > 0 else 0.0
        rsi_14 = self._rsi(closes[-15:])
        ema_12 = self._ema(closes[-26:], 12)
        ema_26 = self._ema(closes[-26:], 26)
        macd_line = ema_12 - ema_26
        macd_signal = self._ema([self._ema(closes[max(0, idx - 26) : idx + 1], 12) - self._ema(closes[max(0, idx - 26) : idx + 1], 26) for idx in range(max(0, len(closes) - 9), len(closes))], 9)
        macd_histogram = macd_line - macd_signal
        total_volume = sum(volumes)
        vwap = sum(c.close * c.volume for c in candles) / total_volume if total_volume > 0 else latest_price
        vwap_distance = latest_price / vwap - 1.0 if vwap > 0 else 0.0
        data_quality = min(1.0, len(candles) / 80)
        return TechnicalFeatures(
            latest_price=latest_price,
            momentum_5=momentum_5,
            momentum_20=momentum_20,
            momentum_60=momentum_60,
            volatility_20=volatility_20,
            atr_pct_14=atr_pct_14,
            volume_ratio=volume_ratio,
            trend_strength=trend_strength,
            rsi_14=rsi_14,
            macd_histogram=macd_histogram,
            vwap_distance=vwap_distance,
            data_quality=data_quality,
        )

    def _ema(self, values: list[float], period: int) -> float:
        if not values:
            return 0.0
        alpha = 2 / (period + 1)
        ema = values[0]
        for value in values[1:]:
            ema = value * alpha + ema * (1 - alpha)
        return ema

    def _rsi(self, closes: list[float]) -> float:
        if len(closes) < 2:
            return 50.0
        gains = []
        losses = []
        for idx in range(1, len(closes)):
            change = closes[idx] - closes[idx - 1]
            gains.append(max(0.0, change))
            losses.append(max(0.0, -change))
        avg_gain = mean(gains) if gains else 0.0
        avg_loss = mean(losses) if losses else 0.0
        if avg_loss == 0:
            return 100.0 if avg_gain > 0 else 50.0
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))
