from datetime import datetime, timedelta, timezone

from backend.app.data.alpaca_client import Candle
from backend.backtest.engine import BacktestConfig, run_backtest
from backend.app.strategy import StrategyParams


def _bars(closes: list[float], start: datetime) -> list[Candle]:
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


def _make_series(days: int, dip_at: int | None = None) -> list[float]:
    closes = [100.0]
    for idx in range(1, days):
        drift = 1.002
        if dip_at is not None and dip_at <= idx < dip_at + 3:
            drift = 0.972  # sharp three-day dip
        elif dip_at is not None and dip_at + 3 <= idx < dip_at + 8:
            drift = 1.018  # V-shaped recovery
        closes.append(closes[-1] * drift)
    return closes


def test_backtest_produces_profitable_v_shape_round_trip():
    start = datetime.now(tz=timezone.utc) - timedelta(days=400)
    days = 380
    spy = _bars(_make_series(days), start)
    dip_symbol = _bars(_make_series(days, dip_at=300), start)
    steady = _bars(_make_series(days), start)

    result = run_backtest(
        {"DIPX": dip_symbol, "FLAT": steady},
        spy,
        StrategyParams(),
        BacktestConfig(months=4),
    )

    assert result.metrics["closed_trades"] >= 1
    dip_trades = [t for t in result.trades if t.symbol == "DIPX"]
    assert dip_trades, "expected the engineered dip to be traded"
    assert sum(t.pnl for t in dip_trades) > 0, "V-shaped dip round trip should be profitable"
    assert result.metrics["final_equity"] > 0
    assert result.equity_curve, "equity curve should be recorded"


def test_backtest_metrics_shape_and_per_symbol_report():
    start = datetime.now(tz=timezone.utc) - timedelta(days=400)
    days = 380
    spy = _bars(_make_series(days), start)
    dip_symbol = _bars(_make_series(days, dip_at=290), start)

    result = run_backtest({"DIPX": dip_symbol}, spy, StrategyParams(), BacktestConfig(months=4))

    for key in (
        "total_return_pct", "spy_return_pct", "max_drawdown_pct", "closed_trades",
        "win_rate_pct", "profit_factor", "avg_exposure_pct", "final_equity",
    ):
        assert key in result.metrics
    if result.trades:
        assert "DIPX" in result.per_symbol
        row = result.per_symbol["DIPX"]
        assert row["trades"] == len([t for t in result.trades if t.symbol == "DIPX"])
        assert "total_pnl" in row


def test_backtest_never_spends_more_than_cash():
    # Tiny account: sizing must clamp orders to available cash (no margin).
    start = datetime.now(tz=timezone.utc) - timedelta(days=400)
    days = 380
    spy = _bars(_make_series(days), start)
    symbols = {f"S{i}": _bars(_make_series(days, dip_at=290 + i), start) for i in range(4)}

    result = run_backtest(
        symbols,
        spy,
        StrategyParams(risk_per_trade_pct=0.05),  # oversized risk to force the cash clamp
        BacktestConfig(starting_equity=5_000, months=4, max_position_pct=0.5),
    )

    assert all(equity > 0 for _, equity in result.equity_curve)
    assert result.metrics["min_cash"] >= 0.0, "cash went negative during the simulation"
