from datetime import datetime, timedelta, timezone

from backend.app.data.alpaca_client import Candle
from backend.app.features.technical import TechnicalFeatureBuilder


def _candles(count: int, end_age_minutes: float = 0.0, step: float = 0.1) -> list[Candle]:
    end = datetime.now(tz=timezone.utc) - timedelta(minutes=end_age_minutes)
    series = []
    price = 100.0
    for idx in range(count):
        price += step
        series.append(
            Candle(
                time=end - timedelta(minutes=count - idx),
                open=price - 0.05,
                high=price + 0.1,
                low=price - 0.15,
                close=price,
                volume=150_000,
            )
        )
    return series


def test_fresh_candles_have_full_freshness_quality():
    builder = TechnicalFeatureBuilder(stale_after_minutes=15)
    features = builder.build("NVDA", _candles(80, end_age_minutes=1))
    assert features.data_quality == 1.0
    assert features.candle_age_minutes < 15


def test_stale_candles_degrade_data_quality_below_decision_threshold():
    builder = TechnicalFeatureBuilder(stale_after_minutes=15)
    # Overnight-stale bars (10 hours old) must not be scored like live data.
    features = builder.build("NVDA", _candles(80, end_age_minutes=600))
    assert features.data_quality <= 0.5
    # Floor keeps diagnostics non-zero rather than reporting dead data.
    assert features.data_quality >= 0.45


def test_macd_histogram_positive_for_accelerating_uptrend():
    builder = TechnicalFeatureBuilder()
    end = datetime.now(tz=timezone.utc)
    series = []
    price = 100.0
    for idx in range(80):
        price += 0.02 + idx * 0.01  # accelerating gains
        series.append(
            Candle(
                time=end - timedelta(minutes=80 - idx),
                open=price - 0.05,
                high=price + 0.1,
                low=price - 0.1,
                close=price,
                volume=150_000,
            )
        )
    features = builder.build("NVDA", series)
    assert features.macd_histogram > 0


def test_too_few_candles_returns_zero_quality():
    builder = TechnicalFeatureBuilder()
    features = builder.build("NVDA", _candles(10))
    assert features.data_quality == 0.0
    assert features.rsi_14 == 50.0
