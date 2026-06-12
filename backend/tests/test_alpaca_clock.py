import httpx

from backend.app.data.alpaca_client import AlpacaClient
from backend.config.settings import Settings


def _client_with_transport(settings: Settings, handler) -> AlpacaClient:
    client = AlpacaClient(settings)
    client._http_client = httpx.Client(
        timeout=10.0,
        headers=client.trade_headers,
        transport=httpx.MockTransport(handler),
    )
    return client


def test_clock_reports_open_in_mock_mode():
    client = AlpacaClient(Settings(alpaca_key="", alpaca_secret=""))
    clock = client.get_clock()
    assert clock["is_open"] is True


def test_clock_parses_alpaca_response():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"is_open": True, "next_open": "2026-06-15T13:30:00Z", "next_close": "2026-06-12T20:00:00Z"},
        )

    client = _client_with_transport(Settings(alpaca_key="key", alpaca_secret="secret"), handler)
    clock = client.get_clock()
    assert clock["is_open"] is True
    assert clock["next_close"] == "2026-06-12T20:00:00Z"


def test_clock_fails_closed_when_request_errors(monkeypatch):
    monkeypatch.setattr("backend.app.data.alpaca_client.time.sleep", lambda _: None)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"message": "server error"})

    client = _client_with_transport(Settings(alpaca_key="key", alpaca_secret="secret"), handler)
    clock = client.get_clock()
    assert clock["is_open"] is False


def test_clock_uses_cache_within_ttl():
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(200, json={"is_open": True, "next_open": None, "next_close": None})

    settings = Settings(alpaca_key="key", alpaca_secret="secret", clock_cache_seconds=60)
    client = _client_with_transport(settings, handler)
    client.get_clock()
    client.get_clock()
    assert calls["count"] == 1


def test_get_with_retry_recovers_from_transient_errors(monkeypatch):
    monkeypatch.setattr("backend.app.data.alpaca_client.time.sleep", lambda _: None)
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] < 3:
            return httpx.Response(429, json={"message": "rate limited"})
        return httpx.Response(200, json={"is_open": False, "next_open": None, "next_close": None})

    settings = Settings(alpaca_key="key", alpaca_secret="secret", http_retry_attempts=3)
    client = _client_with_transport(settings, handler)
    clock = client.get_clock()
    assert calls["count"] == 3
    assert clock["is_open"] is False
