from datetime import datetime, timedelta, timezone

from backend.app.data.alpaca_client import AlpacaClient, Candle
from backend.config.settings import Settings


class FakeAlpacaClient(AlpacaClient):
    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.calls: list[tuple[list[str], int, datetime | None]] = []

    def _request_bars(
        self,
        symbols: list[str],
        limit: int,
        start: datetime | None = None,
    ) -> dict[str, list[Candle]]:
        self.calls.append((symbols, limit, start))
        if start is None:
            return {"NVDA": self._series(3), "MSFT": self._series(80)}
        return {symbol: self._series(80) for symbol in symbols}

    def _series(self, count: int) -> list[Candle]:
        now = datetime.now(tz=timezone.utc)
        return [
            Candle(
                time=now - timedelta(minutes=count - idx),
                open=100.0,
                high=101.0,
                low=99.0,
                close=100.0 + idx * 0.01,
                volume=100_000,
            )
            for idx in range(count)
        ]


def test_get_latest_candles_uses_historical_fallback_for_low_data_symbols():
    settings = Settings(
        alpaca_key="key",
        alpaca_secret="secret",
        min_technical_bars=61,
        technical_fallback_lookback_days=7,
    )
    client = FakeAlpacaClient(settings)

    candles = client.get_latest_candles(["NVDA", "MSFT"])

    assert len(candles["NVDA"]) == 80
    assert len(candles["MSFT"]) == 80
    assert client.last_candle_fallback_symbols == ["NVDA"]
    assert client.last_candle_counts == {"NVDA": 80, "MSFT": 80}
    assert len(client.calls) == 2
    assert client.calls[1][0] == ["NVDA"]


def test_get_latest_candles_fetches_each_low_data_symbol_individually():
    settings = Settings(
        alpaca_key="key",
        alpaca_secret="secret",
        min_technical_bars=61,
        technical_fallback_lookback_days=7,
    )
    client = FakeAlpacaClient(settings)

    client.get_latest_candles(["NVDA", "TSLA", "MSFT"])

    fallback_calls = [call for call in client.calls if call[2] is not None]
    assert [call[0] for call in fallback_calls] == [["NVDA"], ["TSLA"]]
