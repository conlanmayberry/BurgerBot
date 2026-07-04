from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from backend.app.data.alpaca_client import Candle
from backend.config.settings import Settings


logger = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).resolve().parents[2] / ".backtest_cache"
CACHE_MAX_AGE_HOURS = 20.0


def load_daily_bars(
    symbols: list[str],
    lookback_days: int,
    settings: Settings | None = None,
) -> dict[str, list[Candle]]:
    """Daily OHLCV per symbol, newest last. Uses Alpaca when credentials are
    available, otherwise Yahoo's free chart API. Results are cached on disk
    so repeated backtest runs do not re-download."""
    settings = settings or Settings()
    result: dict[str, list[Candle]] = {}
    use_alpaca = bool(settings.alpaca_key and settings.alpaca_secret)
    for symbol in symbols:
        cached = _read_cache(symbol, lookback_days)
        if cached is not None:
            result[symbol] = cached
            continue
        try:
            if use_alpaca:
                bars = _fetch_alpaca_daily(symbol, lookback_days, settings)
            else:
                bars = _fetch_yahoo_daily(symbol, lookback_days)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Daily bar fetch failed for %s: %s", symbol, exc)
            bars = []
        if bars:
            _write_cache(symbol, lookback_days, bars)
        result[symbol] = bars
        time.sleep(0.25)  # be polite to free APIs
    return result


def _fetch_yahoo_daily(symbol: str, lookback_days: int) -> list[Candle]:
    period2 = int(datetime.now(tz=timezone.utc).timestamp())
    period1 = int((datetime.now(tz=timezone.utc) - timedelta(days=lookback_days)).timestamp())
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    params = {"period1": period1, "period2": period2, "interval": "1d", "events": "history"}
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    with httpx.Client(timeout=15.0, headers=headers) as client:
        response = client.get(url, params=params)
        response.raise_for_status()
        payload = response.json()
    results = (payload.get("chart") or {}).get("result") or []
    if not results:
        return []
    chart = results[0]
    timestamps = chart.get("timestamp") or []
    quote = ((chart.get("indicators") or {}).get("quote") or [{}])[0]
    opens = quote.get("open") or []
    highs = quote.get("high") or []
    lows = quote.get("low") or []
    closes = quote.get("close") or []
    volumes = quote.get("volume") or []
    bars: list[Candle] = []
    for i, ts in enumerate(timestamps):
        values = (
            _at(opens, i), _at(highs, i), _at(lows, i), _at(closes, i), _at(volumes, i),
        )
        if any(v is None for v in values[:4]):
            continue
        bars.append(
            Candle(
                time=datetime.fromtimestamp(ts, tz=timezone.utc),
                open=float(values[0]),
                high=float(values[1]),
                low=float(values[2]),
                close=float(values[3]),
                volume=float(values[4] or 0.0),
            )
        )
    bars.sort(key=lambda bar: bar.time)
    return bars


def _fetch_alpaca_daily(symbol: str, lookback_days: int, settings: Settings) -> list[Candle]:
    start = datetime.now(tz=timezone.utc) - timedelta(days=lookback_days)
    headers = {
        "APCA-API-KEY-ID": settings.alpaca_key,
        "APCA-API-SECRET-KEY": settings.alpaca_secret,
    }
    params = {
        "symbols": symbol,
        "timeframe": "1Day",
        "start": start.isoformat(),
        "limit": 10_000,
        "adjustment": "split",
        "feed": settings.alpaca_feed,
        "sort": "asc",
    }
    with httpx.Client(timeout=15.0, headers=headers) as client:
        response = client.get(f"{settings.alpaca_data_url}/v2/stocks/bars", params=params)
        response.raise_for_status()
        payload = response.json()
    raw = (payload.get("bars") or {}).get(symbol, [])
    bars = [
        Candle(
            time=datetime.fromisoformat(bar["t"].replace("Z", "+00:00")),
            open=float(bar["o"]),
            high=float(bar["h"]),
            low=float(bar["l"]),
            close=float(bar["c"]),
            volume=float(bar["v"]),
        )
        for bar in raw
    ]
    bars.sort(key=lambda bar: bar.time)
    return bars


def _at(values: list, index: int):
    return values[index] if index < len(values) else None


def _cache_path(symbol: str, lookback_days: int) -> Path:
    return CACHE_DIR / f"{symbol.upper()}_{lookback_days}d.json"


def _read_cache(symbol: str, lookback_days: int) -> list[Candle] | None:
    path = _cache_path(symbol, lookback_days)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text())
        fetched_at = datetime.fromisoformat(payload["fetched_at"])
        if (datetime.now(tz=timezone.utc) - fetched_at).total_seconds() > CACHE_MAX_AGE_HOURS * 3600:
            return None
        return [
            Candle(
                time=datetime.fromisoformat(bar["t"]),
                open=bar["o"], high=bar["h"], low=bar["l"], close=bar["c"], volume=bar["v"],
            )
            for bar in payload["bars"]
        ]
    except Exception:  # noqa: BLE001
        return None


def _write_cache(symbol: str, lookback_days: int, bars: list[Candle]) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "fetched_at": datetime.now(tz=timezone.utc).isoformat(),
        "bars": [
            {"t": bar.time.isoformat(), "o": bar.open, "h": bar.high, "l": bar.low, "c": bar.close, "v": bar.volume}
            for bar in bars
        ],
    }
    _cache_path(symbol, lookback_days).write_text(json.dumps(payload))
