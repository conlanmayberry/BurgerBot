from backend.app.data.alpaca_client import AlpacaClient
from backend.app.decision.engine import TradingDecision
from backend.app.execution.trader import Trader
from backend.app.risk.engine import PortfolioState, RiskEngine
from backend.config.settings import Settings


class RecordingAlpaca(AlpacaClient):
    def __init__(self) -> None:
        super().__init__(Settings(alpaca_key="", alpaca_secret=""))
        self.submitted: list[dict] = []

    def submit_order(self, symbol: str, side: str, qty: int, client_order_id: str) -> dict:
        self.submitted.append({"symbol": symbol, "side": side, "qty": qty})
        return {"id": f"order-{symbol}", "symbol": symbol, "qty": qty, "side": side, "status": "accepted"}


def _sell_decision(symbol: str = "ORCL", confidence: float = 0.9) -> TradingDecision:
    return TradingDecision(symbol=symbol, action="SELL", confidence=confidence, score=-0.45, rationale={})


def _state(**overrides) -> PortfolioState:
    defaults = dict(
        equity=95_000,
        cash=10_000,
        day_pnl_pct=-0.06,  # deep drawdown day - sells must still work
        open_symbols={"ORCL"},
        quantities={"ORCL": 12},
        current_prices={"ORCL": 150.0},
    )
    defaults.update(overrides)
    return PortfolioState(**defaults)


def test_sell_executes_full_position_even_during_drawdown():
    risk = RiskEngine(
        max_position_pct=0.08,
        max_daily_drawdown_pct=0.03,
        max_open_positions=15,
        min_confidence=0.7,
        cooldown_minutes_after_loss=10,
    )
    alpaca = RecordingAlpaca()
    trader = Trader(alpaca=alpaca, max_position_pct=0.08, enabled=True)
    decision = _sell_decision()
    state = _state()

    risk_result = risk.evaluate(decision, state)
    assert risk_result.allowed, risk_result.reason

    trade = trader.execute(decision, state, estimated_price=150.0)
    assert trade.submitted
    assert trade.qty == 12
    assert alpaca.submitted == [{"symbol": "ORCL", "side": "sell", "qty": 12}]


def test_sell_blocked_when_market_closed():
    risk = RiskEngine(
        max_position_pct=0.08,
        max_daily_drawdown_pct=0.03,
        max_open_positions=15,
        min_confidence=0.7,
        cooldown_minutes_after_loss=10,
    )
    result = risk.evaluate(_sell_decision(), _state(), market_open=False)
    assert not result.allowed
    assert result.reason == "Market closed"


def test_sell_blocked_when_nothing_held():
    risk = RiskEngine(
        max_position_pct=0.08,
        max_daily_drawdown_pct=0.03,
        max_open_positions=15,
        min_confidence=0.7,
        cooldown_minutes_after_loss=10,
    )
    state = _state(open_symbols=set(), quantities={})
    result = risk.evaluate(_sell_decision(), state)
    assert not result.allowed
    assert result.reason == "No open position to sell"
