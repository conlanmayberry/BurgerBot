from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from backend.app.decision.engine import TradingDecision


@dataclass
class PortfolioState:
    equity: float
    cash: float
    day_pnl_pct: float
    open_symbols: set[str]
    loss_streak_count: int = 0
    last_loss_at: datetime | None = None

    @classmethod
    def from_api_payload(cls, payload: dict) -> "PortfolioState":
        positions = payload.get("positions", [])
        return cls(
            equity=float(payload.get("equity", 0.0)),
            cash=float(payload.get("cash", 0.0)),
            day_pnl_pct=float(payload.get("day_pnl_pct", 0.0)),
            open_symbols={p.get("symbol", "").upper() for p in positions},
        )


@dataclass
class RiskResult:
    allowed: bool
    reason: str


class RiskEngine:
    def __init__(
        self,
        max_position_pct: float,
        max_daily_drawdown_pct: float,
        max_open_positions: int,
        min_confidence: float,
        cooldown_minutes_after_loss: int,
    ) -> None:
        self.max_position_pct = max_position_pct
        self.max_daily_drawdown_pct = max_daily_drawdown_pct
        self.max_open_positions = max_open_positions
        self.min_confidence = min_confidence
        self.cooldown_minutes_after_loss = cooldown_minutes_after_loss

    def evaluate(self, decision: TradingDecision, state: PortfolioState) -> RiskResult:
        if decision.action == "HOLD":
            return RiskResult(False, "No trade action")
        if decision.confidence < self.min_confidence:
            return RiskResult(False, "Confidence below threshold")
        if state.day_pnl_pct < -self.max_daily_drawdown_pct:
            return RiskResult(False, "Daily drawdown limit reached")
        if decision.action == "BUY" and len(state.open_symbols) >= self.max_open_positions:
            return RiskResult(False, "Max open positions reached")
        if state.last_loss_at:
            cooldown_until = state.last_loss_at + timedelta(minutes=self.cooldown_minutes_after_loss)
            if datetime.now(tz=timezone.utc) < cooldown_until:
                return RiskResult(False, "Cooldown after loss is active")
        return RiskResult(True, "Approved")
