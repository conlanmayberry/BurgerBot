from __future__ import annotations

import logging
from collections import deque
from datetime import datetime, timezone


logger = logging.getLogger(__name__)


def build_symbol_performance(fills: list[dict]) -> dict[str, dict]:
    """Per-symbol realized P/L from Alpaca FILL activities using FIFO lot
    matching. Fills must be oldest-first. Returns, per symbol:
    realized_pnl, closed_trades, wins, win_rate_pct, bought_qty, sold_qty,
    last_buy_at (ISO timestamp of the most recent buy fill)."""
    lots: dict[str, deque[tuple[float, float]]] = {}
    stats: dict[str, dict] = {}

    for fill in fills:
        symbol = str(fill.get("symbol", "")).upper()
        side = str(fill.get("side", "")).lower()
        try:
            qty = float(fill.get("qty") or 0)
            price = float(fill.get("price") or 0)
        except (TypeError, ValueError):
            continue
        if not symbol or qty <= 0 or price <= 0:
            continue
        entry = stats.setdefault(
            symbol,
            {
                "realized_pnl": 0.0,
                "closed_trades": 0,
                "wins": 0,
                "bought_qty": 0.0,
                "sold_qty": 0.0,
                "last_buy_at": None,
            },
        )
        symbol_lots = lots.setdefault(symbol, deque())
        if side == "buy":
            symbol_lots.append((qty, price))
            entry["bought_qty"] += qty
            entry["last_buy_at"] = _fill_time(fill)
        elif side in ("sell", "sell_short"):
            entry["sold_qty"] += qty
            remaining = qty
            trade_pnl = 0.0
            while remaining > 1e-9 and symbol_lots:
                lot_qty, lot_price = symbol_lots[0]
                matched = min(remaining, lot_qty)
                trade_pnl += (price - lot_price) * matched
                remaining -= matched
                if matched >= lot_qty - 1e-9:
                    symbol_lots.popleft()
                else:
                    symbol_lots[0] = (lot_qty - matched, lot_price)
            entry["realized_pnl"] += trade_pnl
            entry["closed_trades"] += 1
            if trade_pnl > 0:
                entry["wins"] += 1

    for symbol, entry in stats.items():
        entry["realized_pnl"] = round(entry["realized_pnl"], 2)
        entry["win_rate_pct"] = (
            round(100.0 * entry["wins"] / entry["closed_trades"], 1) if entry["closed_trades"] else None
        )
    return stats


def last_buy_dates(performance: dict[str, dict]) -> dict[str, datetime]:
    result: dict[str, datetime] = {}
    for symbol, entry in performance.items():
        raw = entry.get("last_buy_at")
        if not raw:
            continue
        try:
            parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        result[symbol] = parsed.astimezone(timezone.utc)
    return result


def _fill_time(fill: dict) -> str | None:
    value = fill.get("transaction_time")
    return str(value) if value else None
