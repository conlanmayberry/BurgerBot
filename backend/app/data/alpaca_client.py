from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import random

import httpx

from backend.config.settings import Settings


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

    @property
    def credentials_loaded(self) -> bool:
        return bool(self.settings.alpaca_key and self.settings.alpaca_secret)

    def get_portfolio_state(self) -> dict:
        # Falls back to safe defaults when paper API keys are not set.
        if not self.credentials_loaded:
            return {"equity": 100_000.0, "cash": 100_000.0, "day_pnl_pct": 0.0, "positions": []}
        with httpx.Client(timeout=10.0, headers=self.trade_headers) as client:
            account = client.get(f"{self.settings.alpaca_base_url}/v2/account").json()
            positions = client.get(f"{self.settings.alpaca_base_url}/v2/positions").json()
            return {
                "equity": float(account["equity"]),
                "cash": float(account["cash"]),
                "day_pnl_pct": float(account["equity"]) / float(account["last_equity"]) - 1.0,
                "positions": positions,
            }

    def get_latest_candles(self, symbols: list[str], limit: int = 80) -> dict[str, list[Candle]]:
        if not self.credentials_loaded:
            return {s: self._mock_candles(limit) for s in symbols}
        params = {"symbols": ",".join(symbols), "timeframe": "1Min", "limit": limit}
        with httpx.Client(timeout=10.0, headers=self.trade_headers) as client:
            response = client.get(f"{self.settings.alpaca_data_url}/v2/stocks/bars", params=params)
            payload = response.json()
        result: dict[str, list[Candle]] = {}
        for symbol, bars in payload.get("bars", {}).items():
            result[symbol] = [
                Candle(
                    time=datetime.fromisoformat(b["t"].replace("Z", "+00:00")),
                    open=float(b["o"]),
                    high=float(b["h"]),
                    low=float(b["l"]),
                    close=float(b["c"]),
                    volume=float(b["v"]),
                )
                for b in bars
            ]
        return result

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
            response.raise_for_status()
            return response.json()

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
