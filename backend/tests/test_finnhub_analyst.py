from backend.app.data.providers.finnhub_analyst import FinnhubAnalystProvider


def test_finnhub_analyst_provider_maps_recommendation_counts_to_signal():
    provider = FinnhubAnalystProvider(api_key="test")
    payload = [
        {
            "period": "2026-04-01",
            "strongBuy": 4,
            "buy": 8,
            "hold": 3,
            "sell": 1,
            "strongSell": 0,
        },
        {
            "period": "2026-05-01",
            "strongBuy": 5,
            "buy": 9,
            "hold": 4,
            "sell": 1,
            "strongSell": 1,
        },
    ]

    signals = provider._signals_from_payload("NVDA", payload)

    assert len(signals) == 1
    signal = signals[0]
    assert signal.source == "analyst"
    assert signal.sentiment > 0
    assert signal.relevance > 0
    assert signal.confidence > 0
    assert "Analyst consensus for NVDA" in signal.headline


def test_finnhub_analyst_provider_returns_no_signal_for_empty_payload():
    provider = FinnhubAnalystProvider(api_key="test")

    assert provider._signals_from_payload("NVDA", []) == []
