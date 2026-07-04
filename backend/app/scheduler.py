from __future__ import annotations

import logging
import time as time_module
from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from backend.app.data.alpaca_client import AlpacaClient, Candle
from backend.app.data.web_intel import WebIntelCollector
from backend.app.decision.engine import TradingDecision
from backend.app.execution.trader import TradeResult, Trader
from backend.app.features.sentiment import SentimentFeatureBuilder
from backend.app.notify.telegram import TelegramNotifier
from backend.app.performance import build_symbol_performance, last_buy_dates
from backend.app.risk.engine import PortfolioState, RiskEngine
from backend.app.strategy import DailyPullbackStrategy, PositionContext, StrategyParams
from backend.config.settings import Settings


logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

MARKET_TZ = ZoneInfo("America/New_York")
DAILY_BAR_FRESH_DAYS = 4  # newest completed daily bar should be this recent


@dataclass
class RuntimeState:
    last_run_at: datetime | None = None
    last_error: str | None = None
    consecutive_error_count: int = 0
    last_cycle_duration_seconds: float | None = None
    market_open: bool | None = None
    market_risk_on: bool | None = None
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
    last_strategy_reason_counts: dict[str, int] = field(default_factory=dict)
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
    last_equity: float | None = None
    last_cash: float | None = None
    last_day_pnl_pct: float | None = None
    last_unrealized_plpc: dict[str, float] = field(default_factory=dict)
    last_unrealized_pl: dict[str, float] = field(default_factory=dict)
    last_fresh_candle_symbol_count: int = 0
    last_max_candle_age_minutes: float | None = None
    per_symbol_performance: dict[str, dict] = field(default_factory=dict)


class TradingScheduler:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.runtime = RuntimeState()
        self.trade_buffer: list[tuple[TradingDecision, TradeResult]] = []
        self.scheduler = BackgroundScheduler(timezone="UTC")
        self.alpaca = AlpacaClient(settings)
        self.web_intel = WebIntelCollector(settings)
        self.sentiment = SentimentFeatureBuilder()
        self.strategy = DailyPullbackStrategy(
            StrategyParams(
                trend_sma_days=settings.strategy_trend_sma_days,
                entry_rsi_max=settings.strategy_entry_rsi_max,
                max_hold_days=settings.strategy_max_hold_days,
                stop_atr_multiple=settings.strategy_stop_atr_multiple,
                risk_per_trade_pct=settings.risk_per_trade_pct,
            )
        )
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
        self._daily_bars_cache: dict[str, list[Candle]] | None = None
        self._daily_bars_cached_at: datetime | None = None
        self._fills_cache: list[dict] | None = None
        self._fills_cached_at: datetime | None = None

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
            "Scheduler started: %ss trade interval, %ss summary interval, strategy=%s",
            self.settings.market_interval_seconds,
            self.settings.telegram_summary_interval_seconds,
            self.strategy.params,
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
            "market_risk_on": self.runtime.market_risk_on,
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
            "last_strategy_reason_counts": self.runtime.last_strategy_reason_counts,
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
            "last_equity": self.runtime.last_equity,
            "last_cash": self.runtime.last_cash,
            "last_day_pnl_pct": self.runtime.last_day_pnl_pct,
            "last_unrealized_plpc": self.runtime.last_unrealized_plpc,
            "last_fresh_candle_symbol_count": self.runtime.last_fresh_candle_symbol_count,
            "last_max_candle_age_minutes": self.runtime.last_max_candle_age_minutes,
            "candle_stale_minutes": self.settings.candle_stale_minutes,
            "watchlist_size": len(self.settings.symbols),
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
            "strategy": {
                "name": "daily_pullback",
                "regime_sma_days": self.strategy.params.regime_sma_days,
                "trend_sma_days": self.strategy.params.trend_sma_days,
                "entry_rsi_max": self.strategy.params.entry_rsi_max,
                "exit_rsi_min": self.strategy.params.exit_rsi_min,
                "max_hold_days": self.strategy.params.max_hold_days,
                "stop_atr_multiple": self.strategy.params.stop_atr_multiple,
                "risk_per_trade_pct": self.strategy.params.risk_per_trade_pct,
            },
        }

    def performance_report(self) -> dict:
        realized = self.runtime.per_symbol_performance
        combined: dict[str, dict] = {}
        for symbol, row in realized.items():
            combined[symbol] = {
                "realized_pnl": row["realized_pnl"],
                "closed_trades": row["closed_trades"],
                "win_rate_pct": row["win_rate_pct"],
                "unrealized_pnl": 0.0,
            }
        for symbol in self.runtime.last_open_symbols:
            entry = combined.setdefault(
                symbol, {"realized_pnl": 0.0, "closed_trades": 0, "win_rate_pct": None, "unrealized_pnl": 0.0}
            )
            entry["open"] = True
            entry["unrealized_plpc"] = self.runtime.last_unrealized_plpc.get(symbol)
            entry["unrealized_pnl"] = self.runtime.last_unrealized_pl.get(symbol, 0.0)
        for symbol, entry in combined.items():
            entry["total_pnl"] = round(entry["realized_pnl"] + entry.get("unrealized_pnl", 0.0), 2)
        ordered = dict(sorted(combined.items(), key=lambda item: item[1]["total_pnl"], reverse=True))
        return {
            "per_symbol": ordered,
            "total_realized_pnl": round(sum(row["realized_pnl"] for row in combined.values()), 2),
            "closed_trades": sum(row["closed_trades"] for row in combined.values()),
            "note": "Realized P/L is FIFO-matched from Alpaca fill history (last 120 days).",
        }

    def build_symbol_explanation(self, symbol: str) -> dict:
        normalized_symbol = symbol.strip().upper()
        if not normalized_symbol:
            raise ValueError("Symbol is required")
        portfolio = self.alpaca.get_portfolio_state()
        state = PortfolioState.from_api_payload(portfolio)
        clock = self.alpaca.get_clock()
        trading_allowed = bool(clock.get("is_open")) or self.settings.allow_after_hours_trading
        bars = self.alpaca.get_daily_candles([normalized_symbol, "SPY"], self.settings.daily_bars_lookback_days)
        symbol_bars = self._completed_daily_bars(bars.get(normalized_symbol, []))
        spy_bars = self._completed_daily_bars(bars.get("SPY", []))
        risk_on = self.strategy.market_risk_on(spy_bars)
        context = self._position_context(normalized_symbol, state, symbol_bars, {})
        strategy_decision = self.strategy.evaluate_symbol(normalized_symbol, symbol_bars, context, risk_on)
        decision = self._to_trading_decision(strategy_decision)
        risk_result = self.risk_engine.evaluate(decision, state, market_open=trading_allowed)
        return {
            "symbol": normalized_symbol,
            "market_open": bool(clock.get("is_open")),
            "market_risk_on": risk_on,
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

    def connection_report(self) -> dict:
        """Per-service health for the dashboard. Statuses: ok, warn, error, off."""
        now = datetime.now(tz=timezone.utc)
        components: list[dict] = []

        if not self.alpaca.credentials_loaded:
            components.append(
                {"id": "alpaca_trading", "name": "Alpaca Trading", "status": "off",
                 "detail": "Mock mode - no API credentials loaded"}
            )
        elif self.alpaca.last_portfolio_success_at is None:
            components.append(
                {"id": "alpaca_trading", "name": "Alpaca Trading", "status": "warn",
                 "detail": "Credentials loaded, no successful account fetch yet"}
            )
        else:
            age = (now - self.alpaca.last_portfolio_success_at).total_seconds()
            stale_after = max(180, self.settings.market_interval_seconds * 3)
            if not self.runtime.market_open:
                stale_after = max(stale_after, self.settings.closed_market_interval_seconds * 2)
            if age <= stale_after:
                detail = f"Account synced {int(age)}s ago"
                status = "ok"
            else:
                detail = f"Last successful account fetch {int(age // 60)} min ago"
                status = "error"
            components.append(
                {"id": "alpaca_trading", "name": "Alpaca Trading", "status": status, "detail": detail}
            )

        symbol_count = len(self.settings.symbols)
        fresh = self.runtime.last_fresh_candle_symbol_count
        counted = sum(1 for count in self.runtime.last_candle_counts.values() if count > 0)
        if not self.runtime.last_candle_counts:
            components.append(
                {"id": "market_data", "name": "Market Data (daily bars)", "status": "warn",
                 "detail": "No daily bar data fetched yet"}
            )
        elif counted == 0:
            components.append(
                {"id": "market_data", "name": "Market Data (daily bars)", "status": "error",
                 "detail": "No symbols returned any daily bars"}
            )
        elif fresh < symbol_count:
            components.append(
                {"id": "market_data", "name": "Market Data (daily bars)", "status": "warn",
                 "detail": f"Only {fresh}/{symbol_count} symbols have a recent completed daily bar"}
            )
        else:
            components.append(
                {"id": "market_data", "name": "Market Data (daily bars)", "status": "ok",
                 "detail": f"{counted}/{symbol_count} symbols with history ({fresh} recent)"}
            )

        components.append(self._intel_component("analyst", "Finnhub Analysts",
                                                self.settings.enable_analyst_signals and bool(self.settings.finnhub_api_key)))
        components.append(self._intel_component("news", "Alpaca News",
                                                self.settings.enable_news_signals and self.alpaca.credentials_loaded))

        telegram = self.notifier.check_connection()
        components.append(
            {"id": "telegram", "name": "Telegram", "status": telegram["status"], "detail": telegram["detail"]}
        )

        if not self.scheduler.running:
            components.append(
                {"id": "scheduler", "name": "Trading Scheduler", "status": "error", "detail": "Scheduler is not running"}
            )
        elif self.runtime.last_error:
            components.append(
                {"id": "scheduler", "name": "Trading Scheduler", "status": "error",
                 "detail": f"{self.runtime.consecutive_error_count} consecutive errors: {self.runtime.last_error}"}
            )
        elif self.runtime.last_run_at is None:
            components.append(
                {"id": "scheduler", "name": "Trading Scheduler", "status": "warn", "detail": "No cycle completed yet"}
            )
        else:
            cycle_age = (now - self.runtime.last_run_at).total_seconds()
            expected = self.settings.market_interval_seconds * 3
            if not self.runtime.market_open:
                expected = max(expected, self.settings.closed_market_interval_seconds + self.settings.market_interval_seconds * 2)
            if cycle_age <= expected:
                components.append(
                    {"id": "scheduler", "name": "Trading Scheduler", "status": "ok",
                     "detail": f"Last cycle {int(cycle_age)}s ago ({self.runtime.last_cycle_duration_seconds}s duration)"}
                )
            else:
                components.append(
                    {"id": "scheduler", "name": "Trading Scheduler", "status": "error",
                     "detail": f"No completed cycle for {int(cycle_age // 60)} min"}
                )

        overall = "ok"
        if any(c["status"] == "error" for c in components):
            overall = "error"
        elif any(c["status"] == "warn" for c in components):
            overall = "warn"
        return {
            "overall": overall,
            "market_open": self.runtime.market_open,
            "checked_at": now.isoformat(),
            "components": components,
        }

    def _intel_component(self, provider_id: str, name: str, configured: bool) -> dict:
        if not configured:
            return {"id": provider_id, "name": name, "status": "off", "detail": "Disabled or missing credentials"}
        provider_status = self.runtime.last_intel_provider_status.get(provider_id)
        if provider_status in ("fresh", "cached"):
            count = self.runtime.last_signal_source_counts.get(provider_id, 0)
            return {"id": provider_id, "name": name, "status": "ok",
                    "detail": f"{count} signals last cycle ({provider_status})"}
        if provider_status == "stale-after-error":
            return {"id": provider_id, "name": name, "status": "warn",
                    "detail": "Provider failing, serving cached signals"}
        if provider_status == "error-empty":
            return {"id": provider_id, "name": name, "status": "error",
                    "detail": "Provider failing with no cached signals"}
        return {"id": provider_id, "name": name, "status": "warn", "detail": "No cycle completed yet"}

    def current_runtime_settings(self) -> dict:
        return {
            "min_confidence": self.risk_engine.min_confidence,
            "max_open_positions": self.risk_engine.max_open_positions,
            "max_position_pct": self.risk_engine.max_position_pct,
            "max_daily_drawdown_pct": self.risk_engine.max_daily_drawdown_pct,
            "stop_loss_pct": self.settings.stop_loss_pct,
            "take_profit_pct": self.settings.take_profit_pct,
            "risk_per_trade_pct": self.strategy.params.risk_per_trade_pct,
            "entry_rsi_max": self.strategy.params.entry_rsi_max,
            "market_interval_seconds": self.settings.market_interval_seconds,
            "closed_market_interval_seconds": self.settings.closed_market_interval_seconds,
            "allow_after_hours_trading": self.settings.allow_after_hours_trading,
            "enable_protective_exits": self.settings.enable_protective_exits,
            "enable_auto_execution": self.trader.enabled,
        }

    def apply_runtime_settings(self, changes: dict) -> dict:
        """Apply validated setting changes to the live engines. Changes take
        effect immediately but are not persisted to .env - a container
        restart reverts to the environment configuration."""
        for key, value in changes.items():
            if value is None:
                continue
            if key == "min_confidence":
                self.settings.min_confidence = value
                self.risk_engine.min_confidence = value
            elif key == "max_open_positions":
                self.settings.max_open_positions = value
                self.risk_engine.max_open_positions = value
            elif key == "max_position_pct":
                self.settings.max_position_pct = value
                self.risk_engine.max_position_pct = value
                self.trader.max_position_pct = value
            elif key == "max_daily_drawdown_pct":
                self.settings.max_daily_drawdown_pct = value
                self.risk_engine.max_daily_drawdown_pct = value
            elif key == "stop_loss_pct":
                self.settings.stop_loss_pct = value
            elif key == "take_profit_pct":
                self.settings.take_profit_pct = value
            elif key == "risk_per_trade_pct":
                self.settings.risk_per_trade_pct = value
                self.strategy = DailyPullbackStrategy(replace(self.strategy.params, risk_per_trade_pct=value))
            elif key == "entry_rsi_max":
                self.settings.strategy_entry_rsi_max = value
                self.strategy = DailyPullbackStrategy(replace(self.strategy.params, entry_rsi_max=value))
            elif key == "allow_after_hours_trading":
                self.settings.allow_after_hours_trading = value
            elif key == "enable_protective_exits":
                self.settings.enable_protective_exits = value
            elif key == "enable_auto_execution":
                self.settings.enable_auto_execution = value
                self.trader.enabled = value
            elif key == "closed_market_interval_seconds":
                self.settings.closed_market_interval_seconds = value
            elif key == "market_interval_seconds":
                self.settings.market_interval_seconds = value
                if self.scheduler.running and self.scheduler.get_job("trading-cycle"):
                    self.scheduler.reschedule_job(
                        "trading-cycle", trigger=IntervalTrigger(seconds=value)
                    )
            logger.info("Runtime setting updated: %s=%s", key, value)
        return self.current_runtime_settings()

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

    def _get_daily_bars(self) -> dict[str, list[Candle]]:
        now = datetime.now(tz=timezone.utc)
        ttl = self.settings.daily_bars_cache_minutes * 60
        if (
            self._daily_bars_cache is not None
            and self._daily_bars_cached_at is not None
            and (now - self._daily_bars_cached_at).total_seconds() < ttl
        ):
            return self._daily_bars_cache
        bars = self.alpaca.get_daily_candles(
            self.settings.symbols + ["SPY"], self.settings.daily_bars_lookback_days
        )
        self._daily_bars_cache = bars
        self._daily_bars_cached_at = now
        return bars

    def _get_fill_performance(self) -> dict[str, dict]:
        now = datetime.now(tz=timezone.utc)
        ttl = self.settings.fills_cache_minutes * 60
        if (
            self._fills_cache is None
            or self._fills_cached_at is None
            or (now - self._fills_cached_at).total_seconds() >= ttl
        ):
            try:
                self._fills_cache = self.alpaca.get_fill_activities()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Fill activity fetch failed: %s", exc)
                self._fills_cache = self._fills_cache or []
            self._fills_cached_at = now
        return build_symbol_performance(self._fills_cache)

    @staticmethod
    def _completed_daily_bars(bars: list[Candle]) -> list[Candle]:
        """Drop today's partial daily bar so live signals use the same
        completed-day data the backtest was validated on."""
        if not bars:
            return bars
        today_market = datetime.now(tz=MARKET_TZ).date()
        if bars[-1].time.astimezone(MARKET_TZ).date() >= today_market:
            return bars[:-1]
        return bars

    def _position_context(
        self,
        symbol: str,
        state: PortfolioState,
        symbol_bars: list[Candle],
        entry_dates: dict[str, datetime],
    ) -> PositionContext | None:
        qty = state.quantity_for(symbol)
        if qty <= 0:
            return None
        entry_price = state.avg_entry_price_for(symbol)
        if entry_price <= 0 and symbol_bars:
            entry_price = symbol_bars[-1].close
        entry_date = entry_dates.get(symbol)
        if entry_date is not None:
            days_held = sum(1 for bar in symbol_bars if bar.time > entry_date)
        else:
            days_held = 0
        return PositionContext(entry_price=entry_price, days_held=days_held)

    @staticmethod
    def _to_trading_decision(strategy_decision, sentiment_tilt: float = 0.0) -> TradingDecision:
        action = strategy_decision.action
        if action == "BUY":
            score = strategy_decision.rank_score + sentiment_tilt
            confidence = 1.0
        elif action == "SELL":
            score = -0.5
            confidence = 1.0
        else:
            score = 0.0
            confidence = 0.0
        rationale = {
            "strategy": "daily_pullback",
            "reason": strategy_decision.reason,
            "stop_price": round(strategy_decision.stop_price, 2) if strategy_decision.stop_price else None,
            "sentiment_tilt": round(sentiment_tilt, 4),
        }
        rationale.update(strategy_decision.metrics)
        return TradingDecision(
            symbol=strategy_decision.symbol,
            action=action,
            confidence=confidence,
            score=round(score, 4),
            rationale=rationale,
        )

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
            reason_counts: Counter[str] = Counter()
            signal_source_counts: Counter[str] = Counter()
            decision_rows: dict[str, dict] = {}
            exit_diagnostics: list[dict] = []
            symbol_errors: dict[str, str] = {}

            portfolio = self.alpaca.get_portfolio_state()
            state = PortfolioState.from_api_payload(portfolio)
            self.runtime.last_open_position_count = len(state.open_symbols)
            self.runtime.last_open_symbols = sorted(state.open_symbols)
            self.runtime.last_pending_buy_symbols = sorted(state.pending_buy_symbols or set())
            self.runtime.last_pending_buy_order_count = len(state.pending_buy_symbols or set())
            self.runtime.last_pending_buy_quantities = state.pending_buy_quantities or {}
            self.runtime.last_equity = state.equity
            self.runtime.last_cash = state.cash
            self.runtime.last_day_pnl_pct = state.day_pnl_pct
            self.runtime.last_unrealized_plpc = {
                symbol: round(plpc, 4) for symbol, plpc in (state.unrealized_plpc or {}).items()
            }
            self.runtime.last_unrealized_pl = {
                symbol: round(pl, 2) for symbol, pl in (state.unrealized_pl or {}).items()
            }

            all_bars = self._get_daily_bars()
            completed = {
                symbol: self._completed_daily_bars(bars) for symbol, bars in all_bars.items()
            }
            spy_bars = completed.get("SPY", [])
            risk_on = self.strategy.market_risk_on(spy_bars)
            self.runtime.market_risk_on = risk_on

            now_utc = datetime.now(tz=timezone.utc)
            bar_ages: list[float] = []
            candle_counts: dict[str, int] = {}
            for symbol in self.settings.symbols:
                bars = completed.get(symbol, [])
                candle_counts[symbol] = len(bars)
                if bars:
                    bar_ages.append((now_utc - bars[-1].time).total_seconds() / 60.0)
            self.runtime.last_candle_counts = candle_counts
            self.runtime.last_candle_fallback_symbols = []
            self.runtime.last_fresh_candle_symbol_count = sum(
                1 for age in bar_ages if age <= DAILY_BAR_FRESH_DAYS * 24 * 60
            )
            self.runtime.last_max_candle_age_minutes = round(max(bar_ages), 1) if bar_ages else None

            # Intel failures degrade to cached/empty signals inside the
            # collector; they must never abort the cycle.
            intel = self.web_intel.collect(self.settings.symbols)
            self.runtime.last_intel_provider_status = dict(self.web_intel.last_provider_status)
            intel_counts = {symbol: len(intel.get(symbol, [])) for symbol in self.settings.symbols}
            for signals in intel.values():
                signal_source_counts.update(signal.source for signal in signals)

            performance = self._get_fill_performance()
            self.runtime.per_symbol_performance = performance
            entry_dates = last_buy_dates(performance)

            # Protective exits run first: a breached disaster-stop is acted on
            # even if the strategy still says hold.
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

            # Pass 1: strategy evaluation for every symbol.
            buy_candidates: list[tuple[float, TradingDecision, float | None, float]] = []
            for symbol in self.settings.symbols:
                try:
                    symbol_bars = completed.get(symbol, [])
                    context = self._position_context(symbol, state, symbol_bars, entry_dates)
                    strategy_decision = self.strategy.evaluate_symbol(symbol, symbol_bars, context, risk_on)
                    sentiment_tilt = 0.0
                    if strategy_decision.action == "BUY":
                        sent = self.sentiment.build(symbol, intel.get(symbol, []))
                        sentiment_tilt = 0.1 * sent.weighted_sentiment
                    decision = self._to_trading_decision(strategy_decision, sentiment_tilt)
                except Exception as exc:  # noqa: BLE001
                    logger.exception("Strategy evaluation failed for %s: %s", symbol, exc)
                    symbol_errors[symbol] = str(exc)
                    risk_block_counts["Symbol evaluation error"] += 1
                    continue
                action_counts[decision.action] += 1
                reason_counts[strategy_decision.reason] += 1
                decisions.append(decision)
                estimated_price = state.current_price_for(symbol)
                if estimated_price <= 0 and symbol_bars:
                    estimated_price = symbol_bars[-1].close
                decision_rows[symbol] = {
                    "symbol": symbol,
                    "action": decision.action,
                    "score": decision.score,
                    "confidence": decision.confidence,
                    "risk_allowed": False,
                    "risk_reason": "No trade action",
                    "reason": strategy_decision.reason,
                    "rsi": strategy_decision.metrics.get("rsi"),
                    "momentum": strategy_decision.metrics.get("momentum"),
                    "intel_count": intel_counts.get(symbol, 0),
                    "pending_buy": state.has_pending_buy(symbol),
                }
                if decision.action == "SELL":
                    if symbol in executed_symbols:
                        risk_block_counts["Already traded this cycle"] += 1
                        decision_rows[symbol]["risk_reason"] = "Already traded this cycle"
                        continue
                    risk_result = self.risk_engine.evaluate(decision, state, market_open=trading_allowed)
                    decision_rows[symbol]["risk_allowed"] = risk_result.allowed
                    decision_rows[symbol]["risk_reason"] = risk_result.reason
                    if risk_result.allowed:
                        order_result = self.trader.execute(decision, state, estimated_price=estimated_price)
                        if order_result.submitted:
                            executed_trades.append((decision, order_result))
                            executed_symbols.add(symbol)
                        else:
                            risk_block_counts[order_result.reason] += 1
                            decision_rows[symbol]["risk_reason"] = order_result.reason
                    else:
                        risk_block_counts[risk_result.reason] += 1
                elif decision.action == "BUY":
                    buy_candidates.append((decision.score, decision, strategy_decision.stop_price, estimated_price))
                else:
                    risk_block_counts["No trade action"] += 1

            # Pass 2: entries, best-ranked first, spending from a cycle cash
            # ledger so multiple buys can never exceed available cash (this is
            # what previously drove the account into unintended margin).
            available_cash = max(0.0, state.cash)
            for _, decision, stop_price, estimated_price in sorted(buy_candidates, key=lambda c: c[0], reverse=True):
                symbol = decision.symbol
                row = decision_rows[symbol]
                if symbol in executed_symbols:
                    risk_block_counts["Already traded this cycle"] += 1
                    row["risk_reason"] = "Already traded this cycle"
                    continue
                open_exposure = len(state.total_buy_exposure_symbols() | executed_symbols)
                if open_exposure >= self.risk_engine.max_open_positions:
                    risk_block_counts["Max open positions reached"] += 1
                    row["risk_reason"] = "Max open positions reached"
                    continue
                risk_result = self.risk_engine.evaluate(decision, state, market_open=trading_allowed)
                row["risk_allowed"] = risk_result.allowed
                row["risk_reason"] = risk_result.reason
                if not risk_result.allowed:
                    risk_block_counts[risk_result.reason] += 1
                    continue
                if estimated_price <= 0:
                    risk_block_counts["Estimated price unavailable"] += 1
                    row["risk_reason"] = "Estimated price unavailable"
                    continue
                qty = self.strategy.size_position(
                    equity=state.equity,
                    available_cash=available_cash,
                    entry_price=estimated_price,
                    stop_price=stop_price,
                    max_position_pct=self.risk_engine.max_position_pct,
                )
                if qty <= 0:
                    risk_block_counts["Insufficient cash for position size"] += 1
                    row["risk_reason"] = "Insufficient cash for position size"
                    continue
                order_result = self.trader.execute(decision, state, estimated_price=estimated_price, qty=qty)
                if order_result.submitted:
                    executed_trades.append((decision, order_result))
                    executed_symbols.add(symbol)
                    available_cash -= qty * estimated_price
                    row["risk_reason"] = "Approved"
                else:
                    risk_block_counts[order_result.reason] += 1
                    row["risk_reason"] = order_result.reason

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
            self.runtime.last_strategy_reason_counts = dict(reason_counts)
            self.runtime.last_intel_counts = intel_counts
            self.runtime.last_signal_source_counts = dict(signal_source_counts)
            self.runtime.last_exit_decisions = exit_diagnostics
            self.runtime.last_symbol_errors = symbol_errors
            action_priority = {"BUY": 2, "SELL": 2, "HOLD": 0}
            self.runtime.last_top_decisions = sorted(
                (
                    {**row, "score": round(float(row["score"]), 4), "confidence": round(float(row["confidence"]), 4)}
                    for row in decision_rows.values()
                ),
                key=lambda item: (action_priority.get(item["action"], 0), abs(float(item["score"]))),
                reverse=True,
            )[:10]
            self.runtime.last_run_at = datetime.now(tz=timezone.utc)
            self.runtime.last_error = None
            self.runtime.consecutive_error_count = 0
            logger.info(
                "Trading cycle complete in %.1fs: market_open=%s risk_on=%s actions=%s blocks=%s reasons=%s",
                time_module.monotonic() - cycle_started,
                market_open,
                risk_on,
                dict(action_counts),
                dict(risk_block_counts),
                dict(reason_counts),
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
