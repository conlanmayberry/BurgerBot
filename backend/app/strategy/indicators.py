from __future__ import annotations

from statistics import mean


def sma(values: list[float], period: int) -> float | None:
    if period <= 0 or len(values) < period:
        return None
    return mean(values[-period:])


def wilder_rsi(closes: list[float], period: int) -> float | None:
    """Standard Wilder-smoothed RSI. Returns None with insufficient data."""
    if period <= 0 or len(closes) < period + 1:
        return None
    changes = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [max(0.0, c) for c in changes]
    losses = [max(0.0, -c) for c in changes]
    avg_gain = mean(gains[:period])
    avg_loss = mean(losses[:period])
    for i in range(period, len(changes)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def wilder_atr(highs: list[float], lows: list[float], closes: list[float], period: int) -> float | None:
    """Wilder-smoothed Average True Range."""
    n = min(len(highs), len(lows), len(closes))
    if period <= 0 or n < period + 1:
        return None
    true_ranges = [
        max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        for i in range(1, n)
    ]
    atr = mean(true_ranges[:period])
    for tr in true_ranges[period:]:
        atr = (atr * (period - 1) + tr) / period
    return atr


def pct_return(closes: list[float], days: int) -> float | None:
    if days <= 0 or len(closes) < days + 1 or closes[-days - 1] <= 0:
        return None
    return closes[-1] / closes[-days - 1] - 1.0
