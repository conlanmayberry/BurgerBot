from backend.app.data.providers.alpaca_news import AlpacaNewsProvider


def test_alpaca_news_provider_turns_positive_headline_into_signal():
    provider = AlpacaNewsProvider(api_key="key", api_secret="secret")
    article = {
        "headline": "NVDA beats earnings and raises guidance",
        "summary": "Strong results and a positive outlook",
        "symbols": ["NVDA", "QQQ"],
        "created_at": "2026-06-02T12:00:00Z",
    }

    signal = provider._signal_from_article("NVDA", article)

    assert signal is not None
    assert signal.source == "news"
    assert signal.sentiment > 0
    assert signal.relevance > 0
    assert signal.confidence > 0
    assert signal.headline.startswith("NVDA:")


def test_alpaca_news_provider_ignores_neutral_article():
    provider = AlpacaNewsProvider(api_key="key", api_secret="secret")
    article = {
        "headline": "Company publishes quarterly update",
        "summary": "Routine disclosure",
        "symbols": ["NVDA"],
        "created_at": "2026-06-02T12:00:00Z",
    }

    assert provider._signal_from_article("NVDA", article) is None


def test_alpaca_news_provider_turns_downgrade_into_negative_signal():
    provider = AlpacaNewsProvider(api_key="key", api_secret="secret")
    article = {
        "headline": "Analyst downgrades NVDA and cuts price target",
        "summary": "The firm cited margin pressure and weaker demand.",
        "source": "Benzinga",
        "symbols": ["NVDA"],
        "created_at": "2026-06-02T12:00:00Z",
    }

    signal = provider._signal_from_article("NVDA", article)

    assert signal is not None
    assert signal.sentiment < 0
    assert "analyst_downgrade" in signal.headline


def test_alpaca_news_provider_handles_negated_guidance_language():
    provider = AlpacaNewsProvider(api_key="key", api_secret="secret")
    article = {
        "headline": "NVDA does not raise guidance after earnings report",
        "summary": "Management kept its outlook unchanged.",
        "source": "Benzinga",
        "symbols": ["NVDA"],
        "created_at": "2026-06-02T12:00:00Z",
    }

    signal = provider._signal_from_article("NVDA", article)

    assert signal is not None
    assert signal.sentiment < 0
