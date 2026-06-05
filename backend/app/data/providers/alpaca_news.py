from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx

from backend.app.data.intel_models import IntelSignal


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class NewsEventRule:
    label: str
    sentiment: float
    impact: float
    patterns: tuple[str, ...]


class AlpacaNewsProvider:
    base_url = "https://data.alpaca.markets/v1beta1/news"
    event_rules = (
        NewsEventRule(
            label="earnings_beat",
            sentiment=0.85,
            impact=1.0,
            patterns=(
                r"\bbeats?\s+(earnings|estimates|expectations|consensus)\b",
                r"\btops?\s+(earnings|estimates|expectations|consensus)\b",
                r"\bbetter-than-expected\b",
            ),
        ),
        NewsEventRule(
            label="earnings_miss",
            sentiment=-0.85,
            impact=1.0,
            patterns=(
                r"\bmiss(?:es|ed)?\s+(earnings|estimates|expectations|consensus)\b",
                r"\bworse-than-expected\b",
            ),
        ),
        NewsEventRule(
            label="guidance_raise",
            sentiment=0.95,
            impact=1.15,
            patterns=(
                r"\braises?\s+(guidance|outlook|forecast)\b",
                r"\bboosts?\s+(guidance|outlook|forecast)\b",
                r"\braises?\s+(revenue|profit|eps)\s+(forecast|outlook|guidance)\b",
            ),
        ),
        NewsEventRule(
            label="guidance_cut",
            sentiment=-0.95,
            impact=1.15,
            patterns=(
                r"\bcuts?\s+(guidance|outlook|forecast)\b",
                r"\blowers?\s+(guidance|outlook|forecast)\b",
                r"\breduces?\s+(revenue|profit|eps)\s+(forecast|outlook|guidance)\b",
            ),
        ),
        NewsEventRule(
            label="analyst_upgrade",
            sentiment=0.70,
            impact=0.85,
            patterns=(
                r"\bupgrades?\b",
                r"\bprice target (raised|boosted|increased)\b",
                r"\binitiates? (coverage )?(at|with) (buy|outperform|overweight)\b",
            ),
        ),
        NewsEventRule(
            label="analyst_downgrade",
            sentiment=-0.70,
            impact=0.85,
            patterns=(
                r"\bdowngrades?\b",
                r"\bprice target (cut|lowered|reduced)\b",
                r"\binitiates? (coverage )?(at|with) (sell|underperform|underweight)\b",
            ),
        ),
        NewsEventRule(
            label="regulatory_or_legal_risk",
            sentiment=-0.75,
            impact=0.95,
            patterns=(
                r"\b(sec|doj|ftc)\s+(probe|investigation|lawsuit)\b",
                r"\bclass action\b",
                r"\blawsuit\b",
                r"\bregulatory\s+(probe|investigation|scrutiny)\b",
            ),
        ),
        NewsEventRule(
            label="product_or_deal_strength",
            sentiment=0.55,
            impact=0.70,
            patterns=(
                r"\b(partnership|collaboration|contract|deal)\b",
                r"\bapproval\b",
                r"\bexpands?\b",
                r"\blaunch(?:es|ed)?\b",
                r"\bbuyback\b",
            ),
        ),
        NewsEventRule(
            label="operational_pressure",
            sentiment=-0.55,
            impact=0.70,
            patterns=(
                r"\blayoffs?\b",
                r"\brecall\b",
                r"\bsupply chain\b",
                r"\bmargin pressure\b",
                r"\bprofit warning\b",
            ),
        ),
    )
    section_weights = {
        "headline": 1.6,
        "summary": 1.0,
        "content": 0.45,
    }
    trusted_source_weights = {
        "benzinga": 0.78,
        "reuters": 0.88,
        "dow jones": 0.86,
        "marketwatch": 0.74,
        "seeking alpha": 0.68,
        "the fly": 0.70,
        "business wire": 0.72,
        "pr newswire": 0.66,
    }
    negation_terms = (
        "not",
        "no",
        "never",
        "without",
        "denies",
        "denied",
        "denying",
        "fails",
        "failed",
        "unlikely",
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
        sections = {
            "headline": headline,
            "summary": summary,
            "content": content,
        }
        if not any(sections.values()):
            return None

        raw_score, evidence_weight, labels = self._score_sections(sections)
        if evidence_weight == 0:
            return None

        sentiment = max(-1.0, min(1.0, raw_score / evidence_weight))
        if abs(sentiment) < 0.18:
            return None

        created_at = self._parse_time(article.get("created_at") or article.get("updated_at"))
        recency_weight = self._recency_weight(created_at)
        source_weight = self._source_weight(article)
        symbol_count = max(1, len(self._article_symbols(article)))
        relevance = min(1.0, (1.0 / symbol_count) + 0.25)
        confidence = min(
            1.0,
            (0.30 + 0.22 * min(1.0, evidence_weight / 3.0))
            * recency_weight
            * source_weight
            + 0.18,
        )

        title = headline or "Alpaca news article"
        label_text = ", ".join(sorted(labels))
        return IntelSignal(
            source="news",
            sentiment=sentiment,
            relevance=relevance,
            confidence=confidence,
            timestamp=created_at,
            headline=f"{symbol}: {title} [{label_text}]",
        )

    def _score_sections(self, sections: dict[str, str]) -> tuple[float, float, set[str]]:
        raw_score = 0.0
        evidence_weight = 0.0
        labels: set[str] = set()
        for section_name, section_text in sections.items():
            if not section_text:
                continue
            section_weight = self.section_weights.get(section_name, 1.0)
            text = section_text.lower()
            for rule in self.event_rules:
                for pattern in rule.patterns:
                    for match in re.finditer(pattern, text):
                        contribution = rule.sentiment * rule.impact * section_weight
                        if self._has_nearby_negation(text, match.start()):
                            contribution *= -0.65
                        raw_score += contribution
                        evidence_weight += abs(rule.impact * section_weight)
                        labels.add(rule.label)
        return raw_score, evidence_weight, labels

    def _has_nearby_negation(self, text: str, match_start: int) -> bool:
        context = text[max(0, match_start - 60):match_start]
        words = re.findall(r"[a-z']+", context)[-5:]
        return any(word in self.negation_terms for word in words)

    def _source_weight(self, article: dict) -> float:
        source = str(article.get("source") or article.get("author") or "").lower()
        for source_name, weight in self.trusted_source_weights.items():
            if source_name in source:
                return weight
        return 0.62

    def _recency_weight(self, created_at: datetime) -> float:
        age_hours = max(0.0, (datetime.now(tz=timezone.utc) - created_at).total_seconds() / 3600.0)
        if age_hours <= 2:
            return 1.0
        if age_hours <= 8:
            return 0.9
        if age_hours <= 24:
            return 0.75
        return 0.55

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
