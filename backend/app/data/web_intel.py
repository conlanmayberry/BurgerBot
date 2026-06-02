from __future__ import annotations

from backend.app.data.providers.alpaca_news import AlpacaNewsProvider
from backend.app.data.providers.finnhub_analyst import FinnhubAnalystProvider
from backend.app.data.intel_models import IntelSignal
from backend.config.settings import Settings, get_settings


class WebIntelCollector:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.providers = [
            FinnhubAnalystProvider(
                api_key=self.settings.finnhub_api_key,
                enabled=self.settings.enable_analyst_signals,
            ),
            AlpacaNewsProvider(
                api_key=self.settings.alpaca_key,
                api_secret=self.settings.alpaca_secret,
                enabled=self.settings.enable_news_signals,
                lookback_hours=self.settings.news_lookback_hours,
                max_articles=self.settings.news_max_articles,
            )
        ]

    def collect(self, symbols: list[str]) -> dict[str, list[IntelSignal]]:
        collected: dict[str, list[IntelSignal]] = {symbol: [] for symbol in symbols}
        for provider in self.providers:
            provider_signals = provider.collect(symbols)
            for symbol in symbols:
                collected[symbol].extend(provider_signals.get(symbol, []))
        return collected
