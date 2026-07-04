from datetime import datetime, timedelta, timezone

from backend.app.data.alpaca_client import AlpacaClient, Candle
from backend.app.scheduler import TradingScheduler
from backend.config.settings import Settings


def _daily_bars(closes: list[float]) -> list[Candle]:
    # Last bar lands yesterday so it survives the partial-bar trim.
    end = datetime.now(tz=timezone.utc).replace(hour=5, minute=0, second=0, microsecond=0) - timedelta(days=1)
    start = end - timedelta(days=len(closes) - 1)
    return [
        Candle(
            time=start + timedelta(days=idx),
            open=close * 0.999,
            high=close * 1.006,
            low=close * 0.994,
            close=close,
            volume=1_000_000,
        )
        for idx, close in enumerate(closes)
    ]


def _uptrend(days: int = 300) -> list[float]:
    closes = [100.0]
    for _ in range(days - 1):
        closes.append(closes[-1] * 1.002)
    return closes


def _dip_series(days: int = 300) -> list[float]:
    closes = _uptrend(days - 3)
    closes += [closes[-1] * 0.97, closes[-1] * 0.94, closes[-1] * 0.92]
    return closes


class FakeAlpaca(AlpacaClient):
    def __init__(self, settings: Settings, cash: float = 100_000.0) -> None:
        super().__init__(settings)
        self.cash = cash
        self.submitted: list[dict] = []

    def get_clock(self) -> dict:
        return {"is_open": True, "next_open": None, "next_close": None}

    def get_portfolio_state(self) -> dict:
        return {
            "equity": 100_000.0,
            "cash": self.cash,
            "day_pnl_pct": 0.0,
            "positions": [],
            "open_orders": [],
        }

    def get_daily_candles(self, symbols: list[str], lookback_days: int = 340) -> dict[str, list[Candle]]:
        result: dict[str, list[Candle]] = {}
        for symbol in symbols:
            if symbol == "SPY":
                result[symbol] = _daily_bars(_uptrend())
            elif symbol == "DIPX":
                result[symbol] = _daily_bars(_dip_series())
            else:
                result[symbol] = _daily_bars(_uptrend())
        return result

    def get_fill_activities(self, after_days: int = 120) -> list[dict]:
        return []

    def submit_order(self, symbol: str, side: str, qty: int, client_order_id: str) -> dict:
        self.submitted.append({"symbol": symbol, "side": side, "qty": qty})
        return {"id": f"fake-{symbol}", "symbol": symbol, "qty": qty, "side": side, "status": "accepted"}


def _scheduler(cash: float = 100_000.0) -> tuple[TradingScheduler, FakeAlpaca]:
    settings = Settings(
        alpaca_key="key",
        alpaca_secret="secret",
        symbol_watchlist="DIPX,FLAT",
        enable_analyst_signals=False,
        enable_news_signals=False,
        enable_auto_execution=True,
        min_confidence=0.7,
    )
    scheduler = TradingScheduler(settings=settings)
    fake = FakeAlpaca(settings, cash=cash)
    scheduler.alpaca = fake
    scheduler.trader.alpaca = fake
    return scheduler, fake


def test_cycle_buys_the_dip_with_risk_sized_quantity():
    scheduler, fake = _scheduler()
    scheduler._run_cycle()

    assert scheduler.runtime.last_error is None
    assert scheduler.runtime.market_risk_on is True
    assert scheduler.runtime.last_action_counts.get("BUY", 0) == 1
    buys = [o for o in fake.submitted if o["side"] == "buy"]
    assert len(buys) == 1
    assert buys[0]["symbol"] == "DIPX"
    assert buys[0]["qty"] > 0
    # Order must fit inside available cash - the old sizing bug bought on margin.
    dip_close = scheduler.runtime.last_top_decisions[0]
    assert scheduler.runtime.last_cycle_executed_trade_count == 1
    reasons = scheduler.runtime.last_strategy_reason_counts
    assert reasons.get("entry_pullback") == 1
    assert reasons.get("no_pullback", 0) >= 1


def test_cycle_never_buys_with_negative_cash():
    scheduler, fake = _scheduler(cash=-3_879.09)
    scheduler._run_cycle()

    assert scheduler.runtime.last_error is None
    buys = [o for o in fake.submitted if o["side"] == "buy"]
    assert buys == []
    assert scheduler.runtime.last_risk_block_counts.get("Insufficient cash for position size", 0) == 1


def test_cycle_reports_daily_bar_freshness():
    scheduler, _ = _scheduler()
    scheduler._run_cycle()

    assert scheduler.runtime.last_fresh_candle_symbol_count == 2
    assert scheduler.runtime.last_candle_counts["DIPX"] > 200
    assert scheduler.runtime.per_symbol_performance == {}
