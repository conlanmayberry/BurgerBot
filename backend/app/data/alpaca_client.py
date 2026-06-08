from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
import random

import httpx

from backend.config.settings import Settings


logger = logging.getLogger(__name__)


@dataclass
class Candle:
    time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


class AlpacaClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.trade_headers = {
            "APCA-API-KEY-ID": settings.alpaca_key,
            "APCA-API-SECRET-KEY": settings.alpaca_secret,
        }
        self.last_candle_counts: dict[str, int] = {}
        self.last_candle_fallback_symbols: list[str] = []

    @property
    def credentials_loaded(self) -> bool:
        return bool(self.settings.alpaca_key and self.settings.alpaca_secret)

    def get_portfolio_state(self) -> dict:
        # Falls back to safe defaults when paper API keys are not set.
        if not self.credentials_loaded:
            return {"equity": 100_000.0, "cash": 100_000.0, "day_pnl_pct": 0.0, "positions": [], "open_orders": []}
        with httpx.Client(timeout=10.0, headers=self.trade_headers) as client:
            account_response = client.get(f"{self.settings.alpaca_base_url}/v2/account")
            account_response.raise_for_status()
            positions_response = client.get(f"{self.settings.alpaca_base_url}/v2/positions")
            positions_response.raise_for_status()
            open_orders_response = client.get(
                f"{self.settings.alpaca_base_url}/v2/orders",
                params={"status": "open", "limit": 500, "nested": False},
            )
            open_orders_response.raise_for_status()
            account = account_response.json()
            positions = positions_response.json()
            open_orders = open_orders_response.json()
            return {
                "equity": float(account["equity"]),
                "cash": float(account["cash"]),
                "day_pnl_pct": float(account["equity"]) / float(account["last_equity"]) - 1.0,
                "positions": positions,
                "open_orders": open_orders,
            }

    def get_latest_candles(self, symbols: list[str], limit: int = 80) -> dict[str, list[Candle]]:
        if not self.credentials_loaded:
            result = {s: self._mock_candles(limit) for s in symbols}
            self.last_candle_counts = {symbol: len(candles) for symbol, candles in result.items()}
            self.last_candle_fallback_symbols = []
            return result

        result = self._request_bars(symbols=symbols, limit=limit)
        deficient_symbols = [
            symbol
            for symbol in symbols
            if len(result.get(symbol, [])) < self.settings.min_technical_bars
        ]
        if deficient_symbols:
            fallback_limit = max(limit, self.settings.min_technical_bars, 390)
            fallback_start = datetime.now(tz=timezone.utc) - timedelta(
                days=self.settings.technical_fallback_lookback_days
            )
            fallback_result: dict[str, list[Candle]] = {}
            for symbol in deficient_symbols:
                fallback_result.update(
                    self._request_bars(
                        symbols=[symbol],
                        limit=fallback_limit,
                        start=fallback_start,
                    )
                )
            for symbol in deficient_symbols:
                if len(fallback_result.get(symbol, [])) > len(result.get(symbol, [])):
                    result[symbol] = fallback_result[symbol]

        self.last_candle_counts = {symbol: len(result.get(symbol, [])) for symbol in symbols}
        self.last_candle_fallback_symbols = deficient_symbols
        if deficient_symbols:
            logger.info(
                "Used historical candle fallback for low-data symbols: %s counts=%s",
                deficient_symbols,
                {symbol: self.last_candle_counts.get(symbol, 0) for symbol in deficient_symbols},
            )
        return result

    def _request_bars(
        self,
        symbols: list[str],
        limit: int,
        start: datetime | None = None,
    ) -> dict[str, list[Candle]]:
        params = {
            "symbols": ",".join(symbols),
            "timeframe": "1Min",
            "limit": limit,
            "feed": self.settings.alpaca_feed,
        }
        if start is not None:
            params["start"] = start.isoformat()
        with httpx.Client(timeout=10.0, headers=self.trade_headers) as client:
            response = client.get(f"{self.settings.alpaca_data_url}/v2/stocks/bars", params=params)
            response.raise_for_status()
            payload = response.json()
        result: dict[str, list[Candle]] = {}
        for symbol, bars in payload.get("bars", {}).items():
            result[symbol] = self._candles_from_bars(bars)
        return result

    @staticmethod
    def _candles_from_bars(bars: list[dict]) -> list[Candle]:
        return sorted(
            [
                Candle(
                    time=datetime.fromisoformat(bar["t"].replace("Z", "+00:00")),
                    open=float(bar["o"]),
                    high=float(bar["h"]),
                    low=float(bar["l"]),
                    close=float(bar["c"]),
                    volume=float(bar["v"]),
                )
                for bar in bars
            ],
            key=lambda candle: candle.time,
        )

    def submit_order(self, symbol: str, side: str, qty: int, client_order_id: str) -> dict:
        if not self.credentials_loaded:
            return {"id": f"mock-{client_order_id}", "symbol": symbol, "qty": qty, "side": side, "status": "accepted"}
        payload = {
            "symbol": symbol,
            "qty": qty,
            "side": side.lower(),
            "type": "market",
            "time_in_force": "day",
            "client_order_id": client_order_id,
        }
        with httpx.Client(timeout=10.0, headers=self.trade_headers) as client:
            response = client.post(f"{self.settings.alpaca_base_url}/v2/orders", json=payload)
            if response.is_error:
                logger.error(
                    "Alpaca order rejected for %s %s x%s: status=%s body=%s",
                    side,
                    symbol,
                    qty,
                    response.status_code,
                    response.text,
                )
            response.raise_for_status()
            order = response.json()
            logger.info("Submitted Alpaca %s order for %s x%s: %s", side, symbol, qty, order.get("id", "n/a"))
            return order

    def _mock_candles(self, limit: int) -> list[Candle]:
        now = datetime.now(tz=timezone.utc).replace(second=0, microsecond=0)
        series: list[Candle] = []
        price = 100.0 + random.uniform(-2.0, 2.0)
        for idx in range(limit):
            ts = now - timedelta(minutes=limit - idx)
            drift = random.uniform(-0.4, 0.4)
            open_price = price
            close_price = max(1.0, price + drift)
            high = max(open_price, close_price) + random.uniform(0.0, 0.2)
            low = min(open_price, close_price) - random.uniform(0.0, 0.2)
            volume = random.uniform(100_000, 400_000)
            series.append(Candle(ts, open_price, high, low, close_price, volume))
            price = close_price
        return series
