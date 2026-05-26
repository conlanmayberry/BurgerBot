from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from backend.app.data.alpaca_client import AlpacaClient
from backend.app.data.web_intel import WebIntelCollector
from backend.app.decision.engine import DecisionEngine, TradingDecision
from backend.app.execution.trader import Trader
from backend.app.features.sentiment import SentimentFeatureBuilder
from backend.app.features.technical import TechnicalFeatureBuilder
from backend.app.notify.telegram import TelegramNotifier
from backend.app.risk.engine import PortfolioState, RiskEngine
from backend.config.settings import Settings


logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


@dataclass
class RuntimeState:
    last_run_at: datetime | None = None
    last_error: str | None = None
    last_decisions: list[TradingDecision] = field(default_factory=list)


class TradingScheduler:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.runtime = RuntimeState()
        self.scheduler = BackgroundScheduler(timezone="UTC")
        self.alpaca = AlpacaClient(settings)
        self.web_intel = WebIntelCollector()
        self.technical = TechnicalFeatureBuilder()
        self.sentiment = SentimentFeatureBuilder()
        self.decision_engine = DecisionEngine()
        self.risk_engine = RiskEngine(
            max_position_pct=settings.max_position_pct,
            max_daily_drawdown_pct=settings.max_daily_drawdown_pct,
            max_open_positions=settings.max_open_positions,
            min_confidence=settings.min_confidence,
            cooldown_minutes_after_loss=settings.cooldown_minutes_after_loss,
        )
        self.trader = Trader(alpaca=self.alpaca, enabled=settings.enable_auto_execution)
        self.notifier = TelegramNotifier(
            bot_token=settings.telegram_bot_token,
            chat_id=settings.telegram_chat_id,
        )

    def start(self) -> None:
        trigger = IntervalTrigger(seconds=self.settings.market_interval_seconds)
        self.scheduler.add_job(self._run_cycle, trigger=trigger, id="trading-cycle", replace_existing=True)
        self.scheduler.start()
        logger.info("Scheduler started with %s second interval", self.settings.market_interval_seconds)

    def shutdown(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)

    def snapshot_status(self) -> dict:
        return {
            "last_run_at": self.runtime.last_run_at.isoformat() if self.runtime.last_run_at else None,
            "last_error": self.runtime.last_error,
            "last_decision_count": len(self.runtime.last_decisions),
            "scheduler_running": self.scheduler.running,
        }

    def build_symbol_explanation(self, symbol: str) -> dict:
        normalized_symbol = symbol.strip().upper()
        if not normalized_symbol:
            raise ValueError("Symbol is required")
        portfolio = self.alpaca.get_portfolio_state()
        state = PortfolioState.from_api_payload(portfolio)
        candles = self.alpaca.get_latest_candles([normalized_symbol])
        intel = self.web_intel.collect([normalized_symbol])
        tech_features = self.technical.build(normalized_symbol, candles.get(normalized_symbol, []))
        sent_features = self.sentiment.build(normalized_symbol, intel.get(normalized_symbol, []))
        decision = self.decision_engine.decide(normalized_symbol, tech_features, sent_features)
        risk_result = self.risk_engine.evaluate(decision, state)
        return {
            "symbol": normalized_symbol,
            "decision": decision.to_payload(),
            "risk": {"allowed": risk_result.allowed, "reason": risk_result.reason},
            "portfolio": {
                "equity": state.equity,
                "cash": state.cash,
                "day_pnl_pct": state.day_pnl_pct,
                "open_symbols": sorted(state.open_symbols),
            },
        }

    def _run_cycle(self) -> None:
        try:
            decisions: list[TradingDecision] = []
            portfolio = self.alpaca.get_portfolio_state()
            state = PortfolioState.from_api_payload(portfolio)
            candles = self.alpaca.get_latest_candles(self.settings.symbols)
            intel = self.web_intel.collect(self.settings.symbols)
            for symbol in self.settings.symbols:
                tech_features = self.technical.build(symbol, candles.get(symbol, []))
                sent_features = self.sentiment.build(symbol, intel.get(symbol, []))
                decision = self.decision_engine.decide(symbol, tech_features, sent_features)
                risk_result = self.risk_engine.evaluate(decision, state)
                if risk_result.allowed:
                    order_result = self.trader.execute(decision, state)
                    self.notifier.notify_trade(decision, order_result)
                else:
                    self.notifier.notify_blocked(decision, risk_result.reason)
                decisions.append(decision)
            self.runtime.last_decisions = decisions
            self.runtime.last_run_at = datetime.now(tz=timezone.utc)
            self.runtime.last_error = None
        except Exception as exc:  # noqa: BLE001
            logger.exception("Trading cycle failed: %s", exc)
            self.runtime.last_error = str(exc)
