from __future__ import annotations

from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="BOT_", extra="ignore")

    environment: str = "dev"
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    alpaca_key: str = ""
    alpaca_secret: str = ""
    alpaca_base_url: str = "https://paper-api.alpaca.markets"
    alpaca_data_url: str = "https://data.alpaca.markets"
    alpaca_feed: str = "iex"

    finnhub_api_key: str = ""
    enable_analyst_signals: bool = True
    enable_news_signals: bool = True
    news_lookback_hours: int = 24
    news_max_articles: int = 10
    min_technical_bars: int = 61
    technical_fallback_lookback_days: int = 7

    market_interval_seconds: int = 60
    telegram_summary_interval_seconds: int = 1800
    symbol_watchlist: str = (
        "NVDA,TSLA,MSFT,GOOGL,AMZN,META,AMD,AVGO,PLTR,AAPL,"
        "LLY,NVO,JPM,V,MA,COST,WMT,XOM,CAT,UNH,LIN,PANW,ORCL,DE,KO"
    )
    max_position_pct: float = 0.12
    max_daily_drawdown_pct: float = 0.03
    max_open_positions: int = 12
    min_confidence: float = 0.8
    cooldown_minutes_after_loss: int = 10

    allow_after_hours_trading: bool = False
    closed_market_interval_seconds: int = 600
    clock_cache_seconds: int = 60
    # Protective exits are a disaster/windfall backstop around the strategy's
    # own ATR/time exits - wide on purpose so they don't strangle the
    # backtest-validated behavior (mean-reversion entries often dip a few
    # percent before bouncing).
    enable_protective_exits: bool = True
    stop_loss_pct: float = 0.10
    take_profit_pct: float = 0.15

    # Daily pullback strategy (validated in STRATEGY.md).
    strategy_trend_sma_days: int = 200
    strategy_entry_rsi_max: float = 20.0
    strategy_max_hold_days: int = 10
    strategy_stop_atr_multiple: float = 2.5
    risk_per_trade_pct: float = 0.015
    daily_bars_lookback_days: int = 340
    daily_bars_cache_minutes: int = 60
    fills_cache_minutes: int = 10
    analyst_cache_minutes: int = 60
    news_cache_minutes: int = 10
    candle_stale_minutes: int = 15
    http_retry_attempts: int = 3

    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    enable_auto_execution: bool = True
    botpress_verify_token: str = ""

    @property
    def symbols(self) -> list[str]:
        return [s.strip().upper() for s in self.symbol_watchlist.split(",") if s.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
