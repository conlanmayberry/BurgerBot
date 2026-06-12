from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from backend.app.data.providers.alpaca_news import AlpacaNewsProvider
from backend.app.data.providers.finnhub_analyst import FinnhubAnalystProvider
from backend.app.data.intel_models import IntelSignal
from backend.config.settings import Settings, get_settings


logger = logging.getLogger(__name__)


@dataclass
class _ProviderCache:
    fetched_at: datetime | None = None
    symbols_key: tuple[str, ...] = ()
    signals: dict[str, list[IntelSignal]] = field(default_factory=dict)


class WebIntelCollector:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        # (provider, name, cache TTL in minutes). Analyst recommendations move
        # slowly; news moves faster. Caching keeps cycles fast and avoids
        # hammering Finnhub's per-minute rate limit on every cycle.
        self.providers: list[tuple[object, str, int]] = [
            (
                FinnhubAnalystProvider(
                    api_key=self.settings.finnhub_api_key,
                    enabled=self.settings.enable_analyst_signals,
                ),
                "analyst",
                self.settings.analyst_cache_minutes,
            ),
            (
                AlpacaNewsProvider(
                    api_key=self.settings.alpaca_key,
                    api_secret=self.settings.alpaca_secret,
                    enabled=self.settings.enable_news_signals,
                    lookback_hours=self.settings.news_lookback_hours,
                    max_articles=self.settings.news_max_articles,
                ),
                "news",
                self.settings.news_cache_minutes,
            ),
        ]
        self._caches: dict[str, _ProviderCache] = {name: _ProviderCache() for _, name, _ in self.providers}
        self.last_provider_status: dict[str, str] = {}

    def collect(self, symbols: list[str]) -> dict[str, list[IntelSignal]]:
        collected: dict[str, list[IntelSignal]] = {symbol: [] for symbol in symbols}
        for provider, name, ttl_minutes in self.providers:
            provider_signals = self._collect_with_cache(provider, name, ttl_minutes, symbols)
            for symbol in symbols:
                collected[symbol].extend(provider_signals.get(symbol, []))
        return collected

    def _collect_with_cache(
        self,
        provider: object,
        name: str,
        ttl_minutes: int,
        symbols: list[str],
    ) -> dict[str, list[IntelSignal]]:
        cache = self._caches[name]
        symbols_key = tuple(sorted(symbols))
        now = datetime.now(tz=timezone.utc)
        if (
            cache.fetched_at is not None
            and cache.symbols_key == symbols_key
            and (now - cache.fetched_at).total_seconds() < ttl_minutes * 60
        ):
            self.last_provider_status[name] = "cached"
            return cache.signals
        try:
            signals = provider.collect(symbols)
        except Exception as exc:  # noqa: BLE001
            # A provider outage must not fail the trading cycle: serve the
            # last good result if symbols match, otherwise no signals.
            logger.warning("Intel provider %s failed: %s", name, exc)
            if cache.fetched_at is not None and cache.symbols_key == symbols_key:
                self.last_provider_status[name] = "stale-after-error"
                return cache.signals
            self.last_provider_status[name] = "error-empty"
            return {symbol: [] for symbol in symbols}
        cache.fetched_at = now
        cache.symbols_key = symbols_key
        cache.signals = signals
        self.last_provider_status[name] = "fresh"
        return signals
