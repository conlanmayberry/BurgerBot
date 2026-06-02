from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

import httpx

from backend.app.data.intel_models import IntelSignal


logger = logging.getLogger(__name__)


class AlpacaNewsProvider:
    base_url = "https://data.alpaca.markets/v1beta1/news"
    positive_terms = (
        "beat",
        "beats",
        "beat earnings",
        "surge",
        "surges",
        "rally",
        "rallies",
        "upgrade",
        "upgrades",
        "bullish",
        "strong",
        "growth",
        "raises",
        "raise guidance",
        "guidance raised",
        "approval",
        "partnership",
        "buyback",
        "expansion",
        "record",
        "profit",
        "tops",
    )
    negative_terms = (
        "miss",
        "misses",
        "missed",
        "drop",
        "drops",
        "plunge",
        "plunges",
        "downgrade",
        "downgrades",
        "bearish",
        "weak",
        "lawsuit",
        "probe",
        "investigation",
        "cuts",
        "cut guidance",
        "guidance cut",
        "layoffs",
        "recall",
        "debt",
        "loss",
        "warning",
        "slumps",
    )

    def __init__(
        self,
        api_key: str = "",
        api_secret: str = "",
        enabled: bool = True,
        lookback_hours: int = 24,
        max_articles: int = 10,
        timeout_seconds: float = 10.0,
    ) -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        self.enabled = enabled
        self.lookback_hours = max(1, lookback_hours)
        self.max_articles = max(1, min(50, max_articles))
        self.timeout_seconds = timeout_seconds

    def collect(self, symbols: list[str]) -> dict[str, list[IntelSignal]]:
        collected: dict[str, list[IntelSignal]] = {symbol: [] for symbol in symbols}
        if not self.enabled or not self.api_key or not self.api_secret or not symbols:
            return collected

        params = {
            "symbols": ",".join(symbols),
            "limit": self.max_articles,
            "sort": "desc",
            "include_content": True,
            "exclude_contentless": True,
            "start": (datetime.now(tz=timezone.utc) - timedelta(hours=self.lookback_hours)).isoformat(),
        }
        headers = {
            "APCA-API-KEY-ID": self.api_key,
            "APCA-API-SECRET-KEY": self.api_secret,
            "accept": "application/json",
        }

        try:
            with httpx.Client(timeout=self.timeout_seconds, headers=headers) as client:
                response = client.get(self.base_url, params=params)
                response.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Alpaca news request failed: %s", exc)
            return collected

        payload = response.json()
        articles = self._extract_articles(payload)
        for article in articles:
            article_symbols = self._article_symbols(article)
            if not article_symbols:
                continue
            for symbol in symbols:
                if symbol not in article_symbols:
                    continue
                signal = self._signal_from_article(symbol, article)
                if signal is not None:
                    collected[symbol].append(signal)
        return collected

    def _signal_from_article(self, symbol: str, article: dict) -> IntelSignal | None:
        headline = str(article.get("headline") or "").strip()
        summary = str(article.get("summary") or "").strip()
        content = str(article.get("content") or "").strip()
        text = " ".join(part for part in [headline, summary, content] if part).lower()
        if not text:
            return None

        score = 0.0
        hit_count = 0
        for term in self.positive_terms:
            if term in text:
                score += 1.0
                hit_count += 1
        for term in self.negative_terms:
            if term in text:
                score -= 1.0
                hit_count += 1

        if hit_count == 0:
            return None

        keyword_strength = min(1.0, abs(score) / max(1.0, hit_count))
        sentiment = max(-1.0, min(1.0, score / max(2.0, hit_count)))
        if abs(sentiment) < 0.15:
            return None

        created_at = self._parse_time(article.get("created_at") or article.get("updated_at"))
        symbol_count = max(1, len(self._article_symbols(article)))
        relevance = min(1.0, 0.6 + 0.1 * min(symbol_count, 3))
        confidence = min(1.0, 0.45 + 0.15 * keyword_strength)

        title = headline or "Alpaca news article"
        return IntelSignal(
            source="news",
            sentiment=sentiment,
            relevance=relevance,
            confidence=confidence,
            timestamp=created_at,
            headline=f"{symbol}: {title}",
        )

    @staticmethod
    def _extract_articles(payload: object) -> list[dict]:
        if isinstance(payload, dict):
            articles = payload.get("news", [])
            if isinstance(articles, list):
                return [article for article in articles if isinstance(article, dict)]
            return []
        if isinstance(payload, list):
            return [article for article in payload if isinstance(article, dict)]
        return []

    @staticmethod
    def _article_symbols(article: dict) -> set[str]:
        raw_symbols = article.get("symbols") or []
        if not isinstance(raw_symbols, list):
            return set()
        return {
            str(symbol).strip().upper()
            for symbol in raw_symbols
            if isinstance(symbol, str) and symbol.strip()
        }

    @staticmethod
    def _parse_time(value: object) -> datetime:
        if not isinstance(value, str) or not value.strip():
            return datetime.now(tz=timezone.utc)
        cleaned = value.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(cleaned)
        except ValueError:
            logger.warning("Unexpected Alpaca news timestamp: %s", value)
            return datetime.now(tz=timezone.utc)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
