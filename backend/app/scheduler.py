from __future__ import annotations

import logging
import time as time_module
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
    consecutive_error_count: int = 0
    last_cycle_duration_seconds: float | None = None
    market_open: bool | None = None
    next_market_open: str | None = None
    next_market_close: str | None = None
    cycles_skipped_market_closed: int = 0
    last_decisions: list[TradingDecision] = field(default_factory=list)
    last_cycle_executed_trade_count: int = 0
    total_executed_trade_count: int = 0
    total_bought_shares: int = 0
    total_sold_shares: int = 0
    last_action_counts: dict[str, int] = field(default_factory=dict)
    last_risk_block_counts: dict[str, int] = field(default_factory=dict)
    last_intel_counts: dict[str, int] = field(default_factory=dict)
    last_signal_source_counts: dict[str, int] = field(default_factory=dict)
    last_intel_provider_status: dict[str, str] = field(default_factory=dict)
    last_top_decisions: list[dict] = field(default_factory=list)
    last_exit_decisions: list[dict] = field(default_factory=list)
    last_symbol_errors: dict[str, str] = field(default_factory=dict)
    last_candle_counts: dict[str, int] = field(default_factory=dict)
    last_candle_fallback_symbols: list[str] = field(default_factory=list)
    last_open_position_count: int = 0
    last_open_symbols: list[str] = field(default_factory=list)
    last_pending_buy_order_count: int = 0
    last_pending_buy_symbols: list[str] = field(default_factory=list)
    last_pending_buy_quantities: dict[str, int] = field(default_factory=dict)


class TradingScheduler:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.runtime = RuntimeState()
        self.trade_buffer: list[tuple[TradingDecision, TradeResult]] = []
        self.scheduler = BackgroundScheduler(timezone="UTC")
        self.alpaca = AlpacaClient(settings)
        self.web_intel = WebIntelCollector(settings)
        self.technical = TechnicalFeatureBuilder(stale_after_minutes=settings.candle_stale_minutes)
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
        # coalesce + misfire_grace_time keep a slow cycle from raising misfire
        # errors and piling up backlogged runs; max_instances=1 prevents
        # overlapping cycles. next_run_time fires the first cycle on the
        # scheduler thread so app startup (and /health) is never blocked.
        self.scheduler.add_job(
            self._run_cycle,
            trigger=trade_trigger,
            id="trading-cycle",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=max(30, self.settings.market_interval_seconds),
            next_run_time=datetime.now(tz=timezone.utc),
        )
        self.scheduler.add_job(
            self._flush_trade_summary,
            trigger=summary_trigger,
            id="trade-summary",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=max(60, self.settings.telegram_summary_interval_seconds // 2),
        )
        self.scheduler.start()
        logger.info(
            "Scheduler started with %s second trade interval and %s second summary interval",
            self.settings.market_interval_seconds,
            self.settings.telegram_summary_interval_seconds,
        )
        logger.info(
            "Alpaca credentials loaded: %s; auto-execution enabled: %s; after-hours trading allowed: %s",
            self.alpaca.credentials_loaded,
            self.trader.enabled,
            self.settings.allow_after_hours_trading,
        )

    def shutdown(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
        self.alpaca.close()

    def snapshot_status(self) -> dict:
        return {
            "last_run_at": self.runtime.last_run_at.isoformat() if self.runtime.last_run_at else None,
            "last_error": self.runtime.last_error,
            "consecutive_error_count": self.runtime.consecutive_error_count,
            "last_cycle_duration_seconds": self.runtime.last_cycle_duration_seconds,
            "market_open": self.runtime.market_open,
            "next_market_open": self.runtime.next_market_open,
            "next_market_close": self.runtime.next_market_close,
            "cycles_skipped_market_closed": self.runtime.cycles_skipped_market_closed,
            "last_decision_count": len(self.runtime.last_decisions),
            "last_cycle_executed_trade_count": self.runtime.last_cycle_executed_trade_count,
            "total_executed_trade_count": self.runtime.total_executed_trade_count,
            "total_bought_shares": self.runtime.total_bought_shares,
            "total_sold_shares": self.runtime.total_sold_shares,
            "last_action_counts": self.runtime.last_action_counts,
            "last_risk_block_counts": self.runtime.last_risk_block_counts,
            "last_intel_counts": self.runtime.last_intel_counts,
            "last_signal_source_counts": self.runtime.last_signal_source_counts,
            "last_intel_provider_status": self.runtime.last_intel_provider_status,
            "last_top_decisions": self.runtime.last_top_decisions,
            "last_exit_decisions": self.runtime.last_exit_decisions,
            "last_symbol_errors": self.runtime.last_symbol_errors,
            "last_candle_counts": self.runtime.last_candle_counts,
            "last_candle_fallback_symbols": self.runtime.last_candle_fallback_symbols,
            "last_open_position_count": self.runtime.last_open_position_count,
            "last_open_symbols": self.runtime.last_open_symbols,
            "last_pending_buy_order_count": self.runtime.last_pending_buy_order_count,
            "last_pending_buy_symbols": self.runtime.last_pending_buy_symbols,
            "last_pending_buy_quantities": self.runtime.last_pending_buy_quantities,
            "max_open_positions": self.settings.max_open_positions,
            "scheduler_running": self.scheduler.running,
            "alpaca_credentials_loaded": self.alpaca.credentials_loaded,
            "auto_execution_enabled": self.trader.enabled,
            "allow_after_hours_trading": self.settings.allow_after_hours_trading,
            "protective_exits_enabled": self.settings.enable_protective_exits,
            "stop_loss_pct": self.settings.stop_loss_pct,
            "take_profit_pct": self.settings.take_profit_pct,
            "market_interval_seconds": self.settings.market_interval_seconds,
            "closed_market_interval_seconds": self.settings.closed_market_interval_seconds,
            "telegram_summary_interval_seconds": self.settings.telegram_summary_interval_seconds,
            "min_technical_bars": self.settings.min_technical_bars,
            "technical_fallback_lookback_days": self.settings.technical_fallback_lookback_days,
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
        clock = self.alpaca.get_clock()
        trading_allowed = bool(clock.get("is_open")) or self.settings.allow_after_hours_trading
        candles = self.alpaca.get_latest_candles([normalized_symbol])
        intel = self.web_intel.collect([normalized_symbol])
        tech_features = self.technical.build(normalized_symbol, candles.get(normalized_symbol, []))
        sent_features = self.sentiment.build(normalized_symbol, intel.get(normalized_symbol, []))
        decision = self.decision_engine.decide(normalized_symbol, tech_features, sent_features)
        risk_result = self.risk_engine.evaluate(decision, state, market_open=trading_allowed)
        return {
            "symbol": normalized_symbol,
            "market_open": bool(clock.get("is_open")),
            "decision": decision.to_payload(),
            "risk": {"allowed": risk_result.allowed, "reason": risk_result.reason},
            "portfolio": {
                "equity": state.equity,
                "cash": state.cash,
                "day_pnl_pct": state.day_pnl_pct,
                "open_symbols": sorted(state.open_symbols),
            },
        }

    def cancel_all_open_orders(self) -> list[dict]:
        return self.alpaca.cancel_all_orders()

    def _should_run_full_cycle(self, market_open: bool) -> bool:
        # When the market is closed nothing tradeable changes, so run the
        # heavy evaluation at a reduced cadence to keep load and rate-limit
        # pressure low. Orders are blocked by the risk engine regardless.
        if market_open or self.settings.allow_after_hours_trading:
            return True
        if self.runtime.last_run_at is None:
            return True
        elapsed = (datetime.now(tz=timezone.utc) - self.runtime.last_run_at).total_seconds()
        return elapsed >= self.settings.closed_market_interval_seconds

    def _build_exit_decisions(self, state: PortfolioState) -> list[TradingDecision]:
        if not self.settings.enable_protective_exits:
            return []
        exits: list[TradingDecision] = []
        for symbol in sorted(state.open_symbols):
            plpc = state.unrealized_plpc_for(symbol)
            if plpc is None or state.quantity_for(symbol) <= 0:
                continue
            if plpc <= -self.settings.stop_loss_pct:
                exit_reason = "stop_loss"
            elif plpc >= self.settings.take_profit_pct:
                exit_reason = "take_profit"
            else:
                continue
            exits.append(
                TradingDecision(
                    symbol=symbol,
                    action="SELL",
                    confidence=1.0,
                    score=-1.0 if exit_reason == "stop_loss" else 1.0,
                    rationale={
                        "exit_reason": exit_reason,
                        "unrealized_plpc": round(plpc, 4),
                        "stop_loss_pct": self.settings.stop_loss_pct,
                        "take_profit_pct": self.settings.take_profit_pct,
                    },
                )
            )
        return exits

    def _run_cycle(self) -> None:
        cycle_started = time_module.monotonic()
        try:
            clock = self.alpaca.get_clock()
            market_open = bool(clock.get("is_open", False))
            self.runtime.market_open = market_open
            self.runtime.next_market_open = clock.get("next_open")
            self.runtime.next_market_close = clock.get("next_close")
            if not self._should_run_full_cycle(market_open):
                self.runtime.cycles_skipped_market_closed += 1
                return
            trading_allowed = market_open or self.settings.allow_after_hours_trading

            decisions: list[TradingDecision] = []
            executed_trades: list[tuple[TradingDecision, TradeResult]] = []
            executed_symbols: set[str] = set()
            action_counts: Counter[str] = Counter()
            risk_block_counts: Counter[str] = Counter()
            signal_source_counts: Counter[str] = Counter()
            decision_diagnostics: list[dict] = []
            exit_diagnostics: list[dict] = []
            symbol_errors: dict[str, str] = {}

            portfolio = self.alpaca.get_portfolio_state()
            state = PortfolioState.from_api_payload(portfolio)
            self.runtime.last_open_position_count = len(state.open_symbols)
            self.runtime.last_open_symbols = sorted(state.open_symbols)
            self.runtime.last_pending_buy_symbols = sorted(state.pending_buy_symbols or set())
            self.runtime.last_pending_buy_order_count = len(state.pending_buy_symbols or set())
            self.runtime.last_pending_buy_quantities = state.pending_buy_quantities or {}

            candles = self.alpaca.get_latest_candles(self.settings.symbols)
            self.runtime.last_candle_counts = self.alpaca.last_candle_counts
            self.runtime.last_candle_fallback_symbols = self.alpaca.last_candle_fallback_symbols

            # Intel failures degrade to empty signals inside the collector;
            # they must never abort the cycle.
            intel = self.web_intel.collect(self.settings.symbols)
            self.runtime.last_intel_provider_status = dict(self.web_intel.last_provider_status)
            intel_counts = {symbol: len(intel.get(symbol, [])) for symbol in self.settings.symbols}
            for signals in intel.values():
                signal_source_counts.update(signal.source for signal in signals)

            # Protective exits run before scored decisions so a breached
            # stop-loss is acted on even if the decision engine says HOLD.
            for exit_decision in self._build_exit_decisions(state):
                risk_result = self.risk_engine.evaluate(exit_decision, state, market_open=trading_allowed)
                exit_record = {
                    "symbol": exit_decision.symbol,
                    "exit_reason": exit_decision.rationale.get("exit_reason"),
                    "unrealized_plpc": exit_decision.rationale.get("unrealized_plpc"),
                    "risk_allowed": risk_result.allowed,
                    "risk_reason": risk_result.reason,
                    "submitted": False,
                }
                if risk_result.allowed:
                    estimated_price = state.current_price_for(exit_decision.symbol)
                    order_result = self.trader.execute(exit_decision, state, estimated_price=estimated_price)
                    exit_record["submitted"] = order_result.submitted
                    if order_result.submitted:
                        executed_trades.append((exit_decision, order_result))
                        executed_symbols.add(exit_decision.symbol)
                    else:
                        exit_record["risk_reason"] = order_result.reason
                exit_diagnostics.append(exit_record)

            for symbol in self.settings.symbols:
                try:
                    tech_features = self.technical.build(symbol, candles.get(symbol, []))
                    sent_features = self.sentiment.build(symbol, intel.get(symbol, []))
                    decision = self.decision_engine.decide(symbol, tech_features, sent_features)
                except Exception as exc:  # noqa: BLE001
                    logger.exception("Decision evaluation failed for %s: %s", symbol, exc)
                    symbol_errors[symbol] = str(exc)
                    risk_block_counts["Symbol evaluation error"] += 1
                    continue
                action_counts[decision.action] += 1
                if symbol in executed_symbols:
                    risk_result_allowed = False
                    risk_result_reason = "Already traded this cycle"
                else:
                    risk_result = self.risk_engine.evaluate(decision, state, market_open=trading_allowed)
                    risk_result_allowed = risk_result.allowed
                    risk_result_reason = risk_result.reason
                if risk_result_allowed:
                    order_result = self.trader.execute(decision, state, estimated_price=tech_features.latest_price)
                    if order_result.submitted:
                        executed_trades.append((decision, order_result))
                        executed_symbols.add(symbol)
                    else:
                        risk_block_counts[order_result.reason] += 1
                else:
                    risk_block_counts[risk_result_reason] += 1
                decision_diagnostics.append(
                    {
                        "symbol": symbol,
                        "action": decision.action,
                        "score": round(decision.score, 4),
                        "confidence": round(decision.confidence, 4),
                        "risk_allowed": risk_result_allowed,
                        "risk_reason": risk_result_reason,
                        "intel_count": intel_counts.get(symbol, 0),
                        "pending_buy": state.has_pending_buy(symbol),
                        "tech_score": decision.rationale.get("tech_score"),
                        "sentiment_score": decision.rationale.get("sentiment_score"),
                        "data_quality": decision.rationale.get("data_quality"),
                        "candle_age_minutes": decision.rationale.get("candle_age_minutes"),
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
            self.runtime.last_exit_decisions = exit_diagnostics
            self.runtime.last_symbol_errors = symbol_errors
            self.runtime.last_top_decisions = sorted(
                decision_diagnostics,
                key=lambda item: abs(float(item["score"])),
                reverse=True,
            )[:8]
            self.runtime.last_run_at = datetime.now(tz=timezone.utc)
            self.runtime.last_error = None
            self.runtime.consecutive_error_count = 0
            logger.info(
                "Trading cycle complete in %.1fs: market_open=%s actions=%s risk_blocks=%s intel_sources=%s top_decisions=%s",
                time_module.monotonic() - cycle_started,
                market_open,
                dict(action_counts),
                dict(risk_block_counts),
                dict(signal_source_counts),
                self.runtime.last_top_decisions[:3],
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Trading cycle failed: %s", exc)
            self.runtime.last_error = str(exc)
            self.runtime.consecutive_error_count += 1
        finally:
            self.runtime.last_cycle_duration_seconds = round(time_module.monotonic() - cycle_started, 2)

    def _flush_trade_summary(self) -> None:
        if not self.trade_buffer:
            return
        pending_trades = self.trade_buffer[:]
        self.trade_buffer = []
        self.notifier.notify_trade_summary(pending_trades)
