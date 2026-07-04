from datetime import datetime, timedelta, timezone

from backend.app.data.alpaca_client import Candle
from backend.app.strategy import DailyPullbackStrategy, PositionContext, StrategyParams


def _bars_from_closes(closes: list[float]) -> list[Candle]:
    start = datetime.now(tz=timezone.utc) - timedelta(days=len(closes))
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


def _uptrend(days: int = 240, start: float = 100.0, daily_gain: float = 0.003) -> list[float]:
    closes = [start]
    for _ in range(days - 1):
        closes.append(closes[-1] * (1.0 + daily_gain))
    return closes


def test_buy_signal_on_pullback_in_uptrend():
    strategy = DailyPullbackStrategy()
    closes = _uptrend(240)
    closes += [closes[-1] * 0.97, closes[-1] * 0.94, closes[-1] * 0.92]  # sharp 3-day dip
    decision = strategy.evaluate_symbol("NVDA", _bars_from_closes(closes), None, risk_on=True)
    assert decision.action == "BUY"
    assert decision.reason == "entry_pullback"
    assert decision.stop_price is not None and decision.stop_price < closes[-1]
    assert decision.rank_score > 0


def test_no_entry_when_regime_off():
    strategy = DailyPullbackStrategy()
    closes = _uptrend(240)
    closes += [closes[-1] * 0.97, closes[-1] * 0.94, closes[-1] * 0.92]
    decision = strategy.evaluate_symbol("NVDA", _bars_from_closes(closes), None, risk_on=False)
    assert decision.action == "HOLD"
    assert decision.reason == "regime_off"


def test_no_entry_without_pullback():
    strategy = DailyPullbackStrategy()
    decision = strategy.evaluate_symbol("NVDA", _bars_from_closes(_uptrend(243)), None, risk_on=True)
    assert decision.action == "HOLD"
    assert decision.reason == "no_pullback"


def test_no_entry_below_trend():
    strategy = DailyPullbackStrategy()
    closes = _uptrend(220)
    closes += [closes[-1] * (0.985 ** i) for i in range(1, 24)]  # extended slide below trend
    decision = strategy.evaluate_symbol("NVDA", _bars_from_closes(closes), None, risk_on=True)
    assert decision.action == "HOLD"
    assert decision.reason in ("below_trend", "insufficient_indicators")


def test_exit_on_bounce():
    strategy = DailyPullbackStrategy()
    closes = _uptrend(240)
    closes += [closes[-1] * 1.02, closes[-1] * 1.05, closes[-1] * 1.08]  # strong bounce
    position = PositionContext(entry_price=closes[-4], days_held=2)
    decision = strategy.evaluate_symbol("NVDA", _bars_from_closes(closes), position, risk_on=True)
    assert decision.action == "SELL"
    assert decision.reason == "exit_bounce"


def test_exit_on_time_stop():
    strategy = DailyPullbackStrategy(StrategyParams(max_hold_days=10))
    closes = _uptrend(240)
    flat = closes[-1] * 0.97
    closes += [flat * (1 + (0.0004 if i % 2 else -0.0004)) for i in range(8)]  # drifting sideways
    position = PositionContext(entry_price=flat, days_held=11)
    decision = strategy.evaluate_symbol("NVDA", _bars_from_closes(closes), position, risk_on=True)
    assert decision.action == "SELL"
    assert decision.reason in ("exit_time", "exit_bounce")


def test_exit_on_hard_stop():
    strategy = DailyPullbackStrategy()
    closes = _uptrend(240)
    entry = closes[-1]
    closes += [entry * 0.97, entry * 0.92, entry * 0.86]  # crash straight through the stop
    position = PositionContext(entry_price=entry, days_held=3)
    decision = strategy.evaluate_symbol("NVDA", _bars_from_closes(closes), position, risk_on=True)
    assert decision.action == "SELL"
    assert decision.reason == "exit_stop"


def test_exit_when_regime_flips_off():
    strategy = DailyPullbackStrategy()
    closes = _uptrend(240)
    flat = closes[-1] * 0.98
    closes += [flat, flat * 0.999]
    position = PositionContext(entry_price=flat, days_held=2)
    decision = strategy.evaluate_symbol("NVDA", _bars_from_closes(closes), position, risk_on=False)
    assert decision.action == "SELL"
    assert decision.reason == "exit_regime"


def test_sizing_respects_cash_risk_and_cap():
    strategy = DailyPullbackStrategy(StrategyParams(risk_per_trade_pct=0.015))
    # Risk budget: 100k * 1.5% = $1500; stop distance $5 -> 300 shares by risk.
    qty = strategy.size_position(
        equity=100_000, available_cash=100_000, entry_price=100.0, stop_price=95.0, max_position_pct=0.12
    )
    assert qty == 120  # capped by 12% position limit (12k / $100)

    qty_cash_bound = strategy.size_position(
        equity=100_000, available_cash=5_000, entry_price=100.0, stop_price=95.0, max_position_pct=0.12
    )
    assert qty_cash_bound == 49  # 5000*0.98/100

    assert strategy.size_position(100_000, 0.0, 100.0, 95.0, 0.12) == 0
    assert strategy.size_position(100_000, -3_879.0, 100.0, 95.0, 0.12) == 0  # negative cash never buys
