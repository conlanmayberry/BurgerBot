from backend.app.decision.engine import DecisionEngine
from backend.app.features.sentiment import SentimentFeatures
from backend.app.features.technical import TechnicalFeatures


def test_decision_engine_buy_signal():
    engine = DecisionEngine()
    technical = TechnicalFeatures(
        latest_price=185.0,
        momentum_5=0.03,
        momentum_20=0.07,
        momentum_60=0.12,
        volatility_20=0.01,
        atr_pct_14=0.012,
        volume_ratio=1.8,
        trend_strength=0.04,
        rsi_14=62.0,
        macd_histogram=1.2,
        vwap_distance=0.025,
        data_quality=1.0,
    )
    sentiment = SentimentFeatures(
        weighted_sentiment=0.8,
        analyst_bias=0.6,
        signal_confidence=0.9,
    )

    decision = engine.decide("AAPL", technical, sentiment)
    assert decision.action == "BUY"
    assert decision.confidence >= 0.6


def test_decision_engine_hold_signal():
    engine = DecisionEngine()
    technical = TechnicalFeatures(
        latest_price=420.0,
        momentum_5=0.0,
        momentum_20=0.0,
        momentum_60=0.0,
        volatility_20=0.01,
        atr_pct_14=0.01,
        volume_ratio=1.0,
        trend_strength=0.0,
        rsi_14=50.0,
        macd_histogram=0.0,
        vwap_distance=0.0,
        data_quality=1.0,
    )
    sentiment = SentimentFeatures(
        weighted_sentiment=0.0,
        analyst_bias=0.0,
        signal_confidence=0.0,
    )

    decision = engine.decide("MSFT", technical, sentiment)
    assert decision.action == "HOLD"


def test_decision_engine_buy_confidence_scales_above_trade_threshold():
    engine = DecisionEngine()
    technical = TechnicalFeatures(
        latest_price=185.0,
        momentum_5=0.03,
        momentum_20=0.07,
        momentum_60=0.12,
        volatility_20=0.01,
        atr_pct_14=0.012,
        volume_ratio=1.4,
        trend_strength=0.04,
        rsi_14=62.0,
        macd_histogram=1.2,
        vwap_distance=0.025,
        data_quality=1.0,
    )
    sentiment = SentimentFeatures(
        weighted_sentiment=0.5,
        analyst_bias=0.5,
        signal_confidence=0.8,
    )

    decision = engine.decide("PANW", technical, sentiment)

    assert decision.action == "BUY"
    assert decision.confidence >= 0.8


def test_volatile_selloff_still_produces_sell_signal():
    engine = DecisionEngine()
    # Sharp downtrend WITH spiking volatility/ATR - the exact scenario where
    # the old symmetric risk penalty muted sell scores back toward HOLD.
    technical = TechnicalFeatures(
        latest_price=185.0,
        momentum_5=-0.03,
        momentum_20=-0.08,
        momentum_60=-0.15,
        volatility_20=0.05,
        atr_pct_14=0.05,
        volume_ratio=2.0,
        trend_strength=-0.04,
        rsi_14=24.0,
        macd_histogram=-2.0,
        vwap_distance=-0.04,
        data_quality=1.0,
    )
    sentiment = SentimentFeatures(
        weighted_sentiment=-0.7,
        analyst_bias=-0.5,
        signal_confidence=0.9,
    )

    decision = engine.decide("ORCL", technical, sentiment)

    assert decision.action == "SELL"
    assert decision.score < engine.sell_threshold
    assert decision.confidence >= 0.7
