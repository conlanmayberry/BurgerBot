from backend.app.decision.engine import TradingDecision
from backend.app.risk.engine import PortfolioState, RiskEngine


def _decision(action: str, confidence: float) -> TradingDecision:
    return TradingDecision(symbol="AAPL", action=action, confidence=confidence, score=0.2, rationale={})


def test_reject_low_confidence():
    engine = RiskEngine(
        max_position_pct=0.08,
        max_daily_drawdown_pct=0.03,
        max_open_positions=5,
        min_confidence=0.6,
        cooldown_minutes_after_loss=10,
    )
    state = PortfolioState(equity=100_000, cash=80_000, day_pnl_pct=0.0, open_symbols=set())
    result = engine.evaluate(_decision("BUY", 0.5), state)
    assert not result.allowed


def test_reject_drawdown():
    engine = RiskEngine(
        max_position_pct=0.08,
        max_daily_drawdown_pct=0.03,
        max_open_positions=5,
        min_confidence=0.6,
        cooldown_minutes_after_loss=10,
    )
    state = PortfolioState(equity=100_000, cash=80_000, day_pnl_pct=-0.05, open_symbols=set())
    result = engine.evaluate(_decision("BUY", 0.8), state)
    assert not result.allowed


def test_approve_valid_trade():
    engine = RiskEngine(
        max_position_pct=0.08,
        max_daily_drawdown_pct=0.03,
        max_open_positions=5,
        min_confidence=0.6,
        cooldown_minutes_after_loss=10,
    )
    state = PortfolioState(equity=100_000, cash=80_000, day_pnl_pct=0.01, open_symbols={"MSFT"})
    result = engine.evaluate(_decision("BUY", 0.9), state)
    assert result.allowed
