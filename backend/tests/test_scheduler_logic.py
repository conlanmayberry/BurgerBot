from datetime import datetime, timedelta, timezone

from backend.app.risk.engine import PortfolioState
from backend.app.scheduler import TradingScheduler
from backend.config.settings import Settings


def _scheduler(**settings_overrides) -> TradingScheduler:
    settings = Settings(
        enable_analyst_signals=False,
        enable_news_signals=False,
        **settings_overrides,
    )
    return TradingScheduler(settings=settings)


def _state_with_positions(plpc: dict[str, float], quantities: dict[str, int]) -> PortfolioState:
    return PortfolioState(
        equity=100_000,
        cash=50_000,
        day_pnl_pct=0.0,
        open_symbols=set(plpc),
        quantities=quantities,
        unrealized_plpc=plpc,
        current_prices={symbol: 100.0 for symbol in plpc},
    )


def test_stop_loss_breach_creates_sell_exit():
    scheduler = _scheduler(stop_loss_pct=0.03, take_profit_pct=0.06)
    state = _state_with_positions({"NVDA": -0.05}, {"NVDA": 10})

    exits = scheduler._build_exit_decisions(state)

    assert len(exits) == 1
    assert exits[0].symbol == "NVDA"
    assert exits[0].action == "SELL"
    assert exits[0].confidence == 1.0
    assert exits[0].rationale["exit_reason"] == "stop_loss"


def test_take_profit_breach_creates_sell_exit():
    scheduler = _scheduler(stop_loss_pct=0.03, take_profit_pct=0.06)
    state = _state_with_positions({"MSFT": 0.08}, {"MSFT": 4})

    exits = scheduler._build_exit_decisions(state)

    assert len(exits) == 1
    assert exits[0].rationale["exit_reason"] == "take_profit"


def test_position_within_bands_creates_no_exit():
    scheduler = _scheduler(stop_loss_pct=0.03, take_profit_pct=0.06)
    state = _state_with_positions({"AAPL": 0.01}, {"AAPL": 3})

    assert scheduler._build_exit_decisions(state) == []


def test_protective_exits_can_be_disabled():
    scheduler = _scheduler(enable_protective_exits=False)
    state = _state_with_positions({"NVDA": -0.50}, {"NVDA": 10})

    assert scheduler._build_exit_decisions(state) == []


def test_full_cycle_always_runs_when_market_open():
    scheduler = _scheduler()
    scheduler.runtime.last_run_at = datetime.now(tz=timezone.utc)

    assert scheduler._should_run_full_cycle(market_open=True)


def test_full_cycle_throttled_when_market_closed():
    scheduler = _scheduler(closed_market_interval_seconds=600)
    scheduler.runtime.last_run_at = datetime.now(tz=timezone.utc) - timedelta(seconds=120)

    assert not scheduler._should_run_full_cycle(market_open=False)


def test_full_cycle_resumes_after_closed_market_interval():
    scheduler = _scheduler(closed_market_interval_seconds=600)
    scheduler.runtime.last_run_at = datetime.now(tz=timezone.utc) - timedelta(seconds=900)

    assert scheduler._should_run_full_cycle(market_open=False)


def test_first_cycle_runs_even_when_market_closed():
    scheduler = _scheduler()

    assert scheduler._should_run_full_cycle(market_open=False)


def test_status_snapshot_includes_new_diagnostics():
    scheduler = _scheduler()
    status = scheduler.snapshot_status()

    for key in (
        "market_open",
        "last_cycle_duration_seconds",
        "consecutive_error_count",
        "cycles_skipped_market_closed",
        "last_exit_decisions",
        "allow_after_hours_trading",
        "protective_exits_enabled",
        "stop_loss_pct",
        "take_profit_pct",
    ):
        assert key in status
