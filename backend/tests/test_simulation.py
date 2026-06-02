from backend.app.decision.engine import DecisionEngine
from backend.app.features.sentiment import SentimentFeatureBuilder
from backend.app.features.technical import TechnicalFeatureBuilder
from backend.app.data.alpaca_client import Candle
from backend.app.data.intel_models import IntelSignal

from datetime import datetime, timedelta, timezone


def test_simple_intraday_simulation_path():
    decision_engine = DecisionEngine()
    technical_builder = TechnicalFeatureBuilder()
    sentiment_builder = SentimentFeatureBuilder()

    now = datetime.now(tz=timezone.utc)
    candles = []
    price = 100.0
    for i in range(40):
        price += 0.2
        candles.append(
            Candle(
                time=now - timedelta(minutes=40 - i),
                open=price - 0.1,
                high=price + 0.2,
                low=price - 0.3,
                close=price,
                volume=200_000 + i * 500,
            )
        )
    signals = [
        IntelSignal("news", 0.5, 0.9, 0.8, now, "Positive catalyst"),
        IntelSignal("social", 0.4, 0.7, 0.7, now, "Strong social trend"),
        IntelSignal("analyst", 0.6, 0.8, 0.9, now, "Analyst upgrade"),
    ]

    technical = technical_builder.build("NVDA", candles)
    sentiment = sentiment_builder.build("NVDA", signals)
    decision = decision_engine.decide("NVDA", technical, sentiment)
    assert decision.action in {"BUY", "HOLD"}
