from backend.app.data.intel_models import IntelSignal
from backend.app.data.web_intel import WebIntelCollector, _ProviderCache
from backend.config.settings import Settings

from datetime import datetime, timezone


class CountingProvider:
    def __init__(self, fail: bool = False) -> None:
        self.calls = 0
        self.fail = fail

    def collect(self, symbols: list[str]) -> dict[str, list[IntelSignal]]:
        self.calls += 1
        if self.fail:
            raise RuntimeError("provider down")
        return {
            symbol: [
                IntelSignal(
                    source="analyst",
                    sentiment=0.5,
                    relevance=0.8,
                    confidence=0.9,
                    timestamp=datetime.now(tz=timezone.utc),
                    headline=f"{symbol} signal",
                )
            ]
            for symbol in symbols
        }


def _collector_with(provider: CountingProvider, ttl_minutes: int = 60) -> WebIntelCollector:
    collector = WebIntelCollector(Settings())
    collector.providers = [(provider, "analyst", ttl_minutes)]
    collector._caches = {"analyst": _ProviderCache()}
    return collector


def test_collect_uses_cache_within_ttl():
    provider = CountingProvider()
    collector = _collector_with(provider, ttl_minutes=60)

    first = collector.collect(["NVDA", "MSFT"])
    second = collector.collect(["NVDA", "MSFT"])

    assert provider.calls == 1
    assert collector.last_provider_status["analyst"] == "cached"
    assert len(first["NVDA"]) == 1
    assert second == first


def test_collect_refetches_when_symbols_change():
    provider = CountingProvider()
    collector = _collector_with(provider, ttl_minutes=60)

    collector.collect(["NVDA"])
    collector.collect(["NVDA", "MSFT"])

    assert provider.calls == 2


def test_provider_failure_serves_stale_cache():
    provider = CountingProvider()
    collector = _collector_with(provider, ttl_minutes=0)  # always expired

    good = collector.collect(["NVDA"])
    provider.fail = True
    stale = collector.collect(["NVDA"])

    assert collector.last_provider_status["analyst"] == "stale-after-error"
    assert stale == good


def test_provider_failure_without_cache_returns_empty():
    provider = CountingProvider(fail=True)
    collector = _collector_with(provider, ttl_minutes=60)

    result = collector.collect(["NVDA"])

    assert result == {"NVDA": []}
    assert collector.last_provider_status["analyst"] == "error-empty"
