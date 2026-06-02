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

    market_interval_seconds: int = 60
    telegram_summary_interval_seconds: int = 1800
    symbol_watchlist: str = (
        "NVDA,TSLA,MSFT,GOOGL,AMZN,META,AMD,AVGO,PLTR,AAPL,"
        "LLY,NVO,JPM,V,MA,COST,WMT,XOM,CAT,UNH,LIN,PANW,ORCL,DE,KO"
    )
    max_position_pct: float = 0.08
    max_daily_drawdown_pct: float = 0.03
    max_open_positions: int = 5
    min_confidence: float = 0.8
    cooldown_minutes_after_loss: int = 10

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
