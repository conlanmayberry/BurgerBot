from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from backend.app.data.alpaca_client import AlpacaClient
from backend.app.data.web_intel import WebIntelCollector
from backend.app.decision.engine import DecisionEngine, TradingDecision
from backend.app.execution.trader import TradeResult, Trader
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
    last_cycle_executed_trade_count: int = 0
    total_executed_trade_count: int = 0
    total_bought_shares: int = 0
    total_sold_shares: int = 0
    last_action_counts: dict[str, int] = field(default_factory=dict)
    last_risk_block_counts: dict[str, int] = field(default_factory=dict)
    last_intel_counts: dict[str, int] = field(default_factory=dict)
    last_signal_source_counts: dict[str, int] = field(default_factory=dict)
    last_top_decisions: list[dict] = field(default_factory=list)


class TradingScheduler:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.runtime = RuntimeState()
        self.trade_buffer: list[tuple[TradingDecision, TradeResult]] = []
        self.scheduler = BackgroundScheduler(timezone="UTC")
        self.alpaca = AlpacaClient(settings)
        self.web_intel = WebIntelCollector(settings)
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
        self.trader = Trader(
            alpaca=self.alpaca,
            max_position_pct=settings.max_position_pct,
            enabled=settings.enable_auto_execution,
        )
        self.notifier = TelegramNotifier(
            bot_token=settings.telegram_bot_token,
            chat_id=settings.telegram_chat_id,
        )

    def start(self) -> None:
        trade_trigger = IntervalTrigger(seconds=self.settings.market_interval_seconds)
        summary_trigger = IntervalTrigger(seconds=self.settings.telegram_summary_interval_seconds)
        self.scheduler.add_job(self._run_cycle, trigger=trade_trigger, id="trading-cycle", replace_existing=True)
        self.scheduler.add_job(
            self._flush_trade_summary,
            trigger=summary_trigger,
            id="trade-summary",
            replace_existing=True,
        )
        self.scheduler.start()
        self._run_cycle()
        logger.info(
            "Scheduler started with %s second trade interval and %s second summary interval",
            self.settings.market_interval_seconds,
            self.settings.telegram_summary_interval_seconds,
        )
        logger.info(
            "Alpaca credentials loaded: %s; auto-execution enabled: %s",
            self.alpaca.credentials_loaded,
            self.trader.enabled,
        )

    def shutdown(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)

    def snapshot_status(self) -> dict:
        return {
            "last_run_at": self.runtime.last_run_at.isoformat() if self.runtime.last_run_at else None,
            "last_error": self.runtime.last_error,
            "last_decision_count": len(self.runtime.last_decisions),
            "last_cycle_executed_trade_count": self.runtime.last_cycle_executed_trade_count,
            "total_executed_trade_count": self.runtime.total_executed_trade_count,
            "total_bought_shares": self.runtime.total_bought_shares,
            "total_sold_shares": self.runtime.total_sold_shares,
            "last_action_counts": self.runtime.last_action_counts,
            "last_risk_block_counts": self.runtime.last_risk_block_counts,
            "last_intel_counts": self.runtime.last_intel_counts,
            "last_signal_source_counts": self.runtime.last_signal_source_counts,
            "last_top_decisions": self.runtime.last_top_decisions,
            "scheduler_running": self.scheduler.running,
            "alpaca_credentials_loaded": self.alpaca.credentials_loaded,
            "auto_execution_enabled": self.trader.enabled,
            "market_interval_seconds": self.settings.market_interval_seconds,
            "telegram_summary_interval_seconds": self.settings.telegram_summary_interval_seconds,
            "min_confidence": self.settings.min_confidence,
            "buy_threshold": self.decision_engine.buy_threshold,
            "sell_threshold": self.decision_engine.sell_threshold,
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
            executed_trades: list[tuple[TradingDecision, TradeResult]] = []
            action_counts: Counter[str] = Counter()
            risk_block_counts: Counter[str] = Counter()
            signal_source_counts: Counter[str] = Counter()
            decision_diagnostics: list[dict] = []
            portfolio = self.alpaca.get_portfolio_state()
            state = PortfolioState.from_api_payload(portfolio)
            candles = self.alpaca.get_latest_candles(self.settings.symbols)
            intel = self.web_intel.collect(self.settings.symbols)
            intel_counts = {symbol: len(intel.get(symbol, [])) for symbol in self.settings.symbols}
            for signals in intel.values():
                signal_source_counts.update(signal.source for signal in signals)
            for symbol in self.settings.symbols:
                tech_features = self.technical.build(symbol, candles.get(symbol, []))
                sent_features = self.sentiment.build(symbol, intel.get(symbol, []))
                decision = self.decision_engine.decide(symbol, tech_features, sent_features)
                action_counts[decision.action] += 1
                risk_result = self.risk_engine.evaluate(decision, state)
                if risk_result.allowed:
                    order_result = self.trader.execute(decision, state, estimated_price=tech_features.latest_price)
                    if order_result.submitted:
                        executed_trades.append((decision, order_result))
                    else:
                        risk_block_counts[order_result.reason] += 1
                else:
                    risk_block_counts[risk_result.reason] += 1
                decision_diagnostics.append(
                    {
                        "symbol": symbol,
                        "action": decision.action,
                        "score": round(decision.score, 4),
                        "confidence": round(decision.confidence, 4),
                        "risk_allowed": risk_result.allowed,
                        "risk_reason": risk_result.reason,
                        "intel_count": intel_counts.get(symbol, 0),
                        "tech_score": decision.rationale.get("tech_score"),
                        "sentiment_score": decision.rationale.get("sentiment_score"),
                        "data_quality": decision.rationale.get("data_quality"),
                    }
                )
                decisions.append(decision)
            if executed_trades:
                self.trade_buffer.extend(executed_trades)
            self.runtime.last_cycle_executed_trade_count = len(executed_trades)
            self.runtime.total_executed_trade_count += len(executed_trades)
            for decision, trade in executed_trades:
                if decision.action == "BUY":
                    self.runtime.total_bought_shares += trade.qty
                elif decision.action == "SELL":
                    self.runtime.total_sold_shares += trade.qty
            self.runtime.last_decisions = decisions
            self.runtime.last_action_counts = dict(action_counts)
            self.runtime.last_risk_block_counts = dict(risk_block_counts)
            self.runtime.last_intel_counts = intel_counts
            self.runtime.last_signal_source_counts = dict(signal_source_counts)
            self.runtime.last_top_decisions = sorted(
                decision_diagnostics,
                key=lambda item: abs(float(item["score"])),
                reverse=True,
            )[:8]
            self.runtime.last_run_at = datetime.now(tz=timezone.utc)
            self.runtime.last_error = None
            logger.info(
                "Trading cycle complete: actions=%s risk_blocks=%s intel_sources=%s top_decisions=%s",
                dict(action_counts),
                dict(risk_block_counts),
                dict(signal_source_counts),
                self.runtime.last_top_decisions[:3],
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Trading cycle failed: %s", exc)
            self.runtime.last_error = str(exc)

    def _flush_trade_summary(self) -> None:
        if not self.trade_buffer:
            return
        pending_trades = self.trade_buffer[:]
        self.trade_buffer = []
        self.notifier.notify_trade_summary(pending_trades)
