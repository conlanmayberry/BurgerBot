from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
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
    candle_age_minutes: float = 0.0


class TechnicalFeatureBuilder:
    def __init__(self, stale_after_minutes: int = 15) -> None:
        self.stale_after_minutes = max(1, stale_after_minutes)

    def build(self, symbol: str, candles: list[Candle]) -> TechnicalFeatures:
        _ = symbol
        if len(candles) < 21:
            return TechnicalFeatures(
                latest_price=0.0,
                momentum_5=0.0,
                momentum_20=0.0,
                momentum_60=0.0,
                volatility_20=0.0,
                atr_pct_14=0.0,
                volume_ratio=1.0,
                trend_strength=0.0,
                rsi_14=50.0,
                macd_histogram=0.0,
                vwap_distance=0.0,
                data_quality=0.0,
                candle_age_minutes=0.0,
            )

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
        macd_histogram = self._macd_histogram(closes)
        total_volume = sum(volumes)
        vwap = sum(c.close * c.volume for c in candles) / total_volume if total_volume > 0 else latest_price
        vwap_distance = latest_price / vwap - 1.0 if vwap > 0 else 0.0
        candle_age_minutes = self._candle_age_minutes(candles)
        bar_factor = min(1.0, len(candles) / 80)
        data_quality = bar_factor * self._freshness_factor(candle_age_minutes)
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
            candle_age_minutes=candle_age_minutes,
        )

    @staticmethod
    def _candle_age_minutes(candles: list[Candle]) -> float:
        last_time = candles[-1].time
        if last_time.tzinfo is None:
            last_time = last_time.replace(tzinfo=timezone.utc)
        age_seconds = (datetime.now(tz=timezone.utc) - last_time).total_seconds()
        return max(0.0, age_seconds / 60.0)

    def _freshness_factor(self, age_minutes: float) -> float:
        # Full quality while the last bar is recent; degrades linearly to a
        # 0.5 floor so stale (overnight/broken-feed) data drops below the
        # decision engine's data_quality_threshold and decisions stay HOLD,
        # while /status diagnostics still show non-zero quality.
        if age_minutes <= self.stale_after_minutes:
            return 1.0
        decay_window = self.stale_after_minutes * 5.0
        overage = age_minutes - self.stale_after_minutes
        return max(0.5, 1.0 - 0.5 * (overage / decay_window))

    @staticmethod
    def _ema_series(values: list[float], period: int) -> list[float]:
        if len(values) < period:
            return []
        alpha = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        series = [ema]
        for value in values[period:]:
            ema = value * alpha + ema * (1 - alpha)
            series.append(ema)
        return series

    def _macd_histogram(self, closes: list[float]) -> float:
        ema_12 = self._ema_series(closes, 12)
        ema_26 = self._ema_series(closes, 26)
        if not ema_12 or not ema_26:
            return 0.0
        macd_series = [fast - slow for fast, slow in zip(ema_12[-len(ema_26):], ema_26)]
        signal_series = self._ema_series(macd_series, 9)
        if signal_series:
            macd_signal = signal_series[-1]
        else:
            macd_signal = mean(macd_series)
        return macd_series[-1] - macd_signal

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
