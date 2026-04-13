"""
News pipeline — 3-stage filter for trading-relevant news.

Per v3.1 Delta 5 + Patch E:
    Stage 1: Heuristic filter (ticker match + keyword whitelist) — no LLM cost
    Stage 2: Embedding deduplication (OpenAI embeddings + pgvector lookup)
    Stage 3: LLM relevance scoring (Grok fast or Haiku)

Only items that survive all 3 stages with score >= min_relevance_score
trigger an INTRADAY debate.

Run as a script:
    uv run python -m scripts.process_news
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import numpy as np
from dotenv import load_dotenv
from openai import AsyncOpenAI

from src.db import get_client
from src.llm_clients import call_claude, call_grok

load_dotenv()

POLYGON_API_KEY = os.getenv("POLYGON_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
MODEL_NEWS_SCORER = os.getenv("MODEL_NEWS_SCORER", "claude-haiku-4-5")
MODEL_EMBEDDINGS = os.getenv("MODEL_EMBEDDINGS", "text-embedding-3-small")

POLYGON_BASE_URL = "https://api.polygon.io"

# =============================================================================
# Constants
# =============================================================================

# Stage 1: keywords that signal material news
KEYWORDS_HIGH = [
    "earnings", "revenue", "guidance", "downgrade", "upgrade",
    "FDA", "lawsuit", "acquisition", "merger", "CEO", "CFO",
    "bankruptcy", "recall", "investigation", "partnership", "contract",
    "approval", "rejection", "subpoena", "settlement", "buyback",
    "dividend", "split", "spin-off", "ipo", "delisting",
    "raises", "cuts", "beats", "misses", "warns",
]

# Stage 2: dedup similarity thresholds by category (per Patch E)
DEDUP_THRESHOLDS: dict[str, float] = {
    "earnings": 0.72,
    "regulatory": 0.75,
    "M&A": 0.75,
    "product": 0.78,
    "analyst": 0.85,
    "opinion": 0.85,
    "default": 0.78,
}

DEDUP_WINDOW_HOURS = 2

# Lazy OpenAI client for embeddings
_openai_client: AsyncOpenAI | None = None


def _get_openai_client() -> AsyncOpenAI:
    global _openai_client
    if _openai_client is None:
        if not OPENAI_API_KEY:
            raise RuntimeError("OPENAI_API_KEY not set in environment")
        _openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)
    return _openai_client


# =============================================================================
# Data classes
# =============================================================================

@dataclass
class NewsItem:
    polygon_id: str
    title: str
    description: str
    tickers: list[str]
    category: str | None
    published_utc: str
    publisher: str | None
    url: str | None

    @classmethod
    def from_polygon(cls, raw: dict) -> "NewsItem":
        publisher = None
        pub_field = raw.get("publisher")
        if isinstance(pub_field, dict):
            publisher = pub_field.get("name")

        return cls(
            polygon_id=raw.get("id", ""),
            title=raw.get("title", ""),
            description=raw.get("description", "") or "",
            tickers=raw.get("tickers", []) or [],
            category=None,  # Polygon does not provide a stable category
            published_utc=raw.get("published_utc", ""),
            publisher=publisher,
            url=raw.get("article_url"),
        )


@dataclass
class ScoredNewsItem:
    news: NewsItem
    relevance_score: int
    impact_direction: str
    urgency: str
    one_line_summary: str
    decision: str  # trigger_debate | skip_low_score | skip_duplicate | skip_heuristic
    duplicate_similarity: float | None = None
    cost_usd: float = 0.0


# =============================================================================
# Stage 0: Fetch news from Polygon
# =============================================================================

async def fetch_polygon_news(
    tickers: list[str],
    limit_per_ticker: int = 10,
    since_hours: int = 24,
) -> list[NewsItem]:
    """Fetch recent news from Polygon for the given tickers."""
    if not POLYGON_API_KEY:
        raise RuntimeError("POLYGON_API_KEY not set")

    cutoff = datetime.now(timezone.utc) - timedelta(hours=since_hours)
    cutoff_str = cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")

    items_by_id: dict[str, NewsItem] = {}

    async with httpx.AsyncClient(timeout=20.0) as client:
        for ticker in tickers:
            url = f"{POLYGON_BASE_URL}/v2/reference/news"
            params = {
                "ticker": ticker,
                "limit": limit_per_ticker,
                "order": "desc",
                "published_utc.gte": cutoff_str,
                "apiKey": POLYGON_API_KEY,
            }
            try:
                response = await client.get(url, params=params)
                response.raise_for_status()
                data = response.json()
                for raw in data.get("results", []):
                    item = NewsItem.from_polygon(raw)
                    if item.polygon_id:
                        items_by_id[item.polygon_id] = item
            except Exception as e:
                print(f"   ⚠️  Failed to fetch news for {ticker}: {type(e).__name__}: {e}")

    return list(items_by_id.values())


# =============================================================================
# Stage 1: Heuristic filter
# =============================================================================

def heuristic_filter(item: NewsItem, portfolio_tickers: set[str]) -> bool:
    """Cheap first-pass filter. Returns True if item should proceed."""
    if not any(t in portfolio_tickers for t in item.tickers):
        return False

    text = (item.title + " " + item.description).lower()
    if not any(kw in text for kw in KEYWORDS_HIGH):
        return False

    return True


# =============================================================================
# Stage 2: Embedding-based deduplication
# =============================================================================

async def compute_embedding(text: str) -> list[float]:
    """Compute an embedding for the given text using OpenAI."""
    client = _get_openai_client()
    response = await client.embeddings.create(
        model=MODEL_EMBEDDINGS,
        input=text,
    )
    return response.data[0].embedding


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    a_arr = np.array(a, dtype=float)
    b_arr = np.array(b, dtype=float)
    norm_a = np.linalg.norm(a_arr)
    norm_b = np.linalg.norm(b_arr)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(a_arr, b_arr) / (norm_a * norm_b))


def fetch_recent_embeddings(ticker: str) -> list[list[float]]:
    """Fetch embeddings for the given ticker within the dedup window."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=DEDUP_WINDOW_HOURS)

    try:
        client = get_client()
        response = (
            client.table("news_dedup")
            .select("embedding")
            .eq("ticker", ticker)
            .gte("timestamp", cutoff.isoformat())
            .execute()
        )
        rows = response.data or []
        embeddings = []
        for r in rows:
            emb = r.get("embedding")
            if emb is None:
                continue
            # pgvector may return as a string "[1.2, 3.4, ...]" or as a list
            if isinstance(emb, str):
                try:
                    import json
                    emb = json.loads(emb)
                except json.JSONDecodeError:
                    continue
            embeddings.append(emb)
        return embeddings
    except Exception as e:
        print(f"   ⚠️  Failed to fetch recent embeddings for {ticker}: {e}")
        return []


def get_threshold_for_category(category: str | None) -> float:
    """Get the dedup similarity threshold for the given category."""
    cat = category or "default"
    return DEDUP_THRESHOLDS.get(cat, DEDUP_THRESHOLDS["default"])


def is_duplicate(
    item: NewsItem,
    embedding: list[float],
) -> tuple[bool, float]:
    """
    Check if the item is a duplicate of recent news for the same ticker(s).

    Returns (is_dup, max_similarity).
    """
    threshold = get_threshold_for_category(item.category)
    max_sim = 0.0

    for ticker in item.tickers:
        recent = fetch_recent_embeddings(ticker)
        for emb in recent:
            sim = cosine_similarity(embedding, emb)
            if sim > max_sim:
                max_sim = sim

    return max_sim > threshold, max_sim


def save_embedding_to_dedup(
    item: NewsItem,
    embedding: list[float],
    score: int | None = None,
) -> None:
    """Save the embedding to news_dedup for future deduplication checks."""
    try:
        client = get_client()
        for ticker in item.tickers:
            client.table("news_dedup").insert({
                "ticker": ticker,
                "news_id": item.polygon_id,
                "title": item.title[:500],
                "embedding": embedding,
                "category": item.category,
                "relevance_score": score,
            }).execute()
    except Exception as e:
        print(f"   ⚠️  Failed to save embedding: {e}")


# =============================================================================
# Stage 3: LLM relevance scoring
# =============================================================================

NEWS_SCORER_SYSTEM_PROMPT = """\
You are a news relevance classifier for a stock trading system. Your job is
to evaluate how material a news item is for trading decisions on a specific
ticker.

You return ONLY valid JSON, no preamble, no markdown fences.
"""


def _build_scorer_prompt(item: NewsItem, ticker: str) -> str:
    return f"""\
Evaluate this news for trading decisions on {ticker}.

Title: {item.title}
Description: {item.description[:800]}
Publisher: {item.publisher or "Unknown"}

Respond with JSON in this exact format:
{{
  "relevance_score": <integer 0-100>,
  "impact_direction": "positive" | "negative" | "neutral" | "ambiguous",
  "urgency": "immediate" | "this_week" | "long_term",
  "one_line_summary": "<max 15 words, what happened>"
}}

Scoring guidance:
- 0-30: vague mention, no actionable content
- 31-60: relevant context but not directly material
- 61-80: clearly material to {ticker} (specific event, guidance, action)
- 81-100: highly material, market-moving (earnings, M&A, regulatory action)

Do NOT invent details. If the description is vague or missing, score below 40.
"""


async def score_news(item: NewsItem, ticker: str) -> dict[str, Any]:
    """Call the news scorer LLM and return parsed score + cost metrics."""
    system = NEWS_SCORER_SYSTEM_PROMPT
    user = _build_scorer_prompt(item, ticker)

    # Route to the right client based on model name
    if MODEL_NEWS_SCORER.startswith("grok"):
        response = await call_grok(
            model=MODEL_NEWS_SCORER,
            system=system,
            user=user,
            max_tokens=300,
            temperature=0.2,
        )
    else:
        response = await call_claude(
            model=MODEL_NEWS_SCORER,
            system=system,
            user=user,
            max_tokens=300,
            temperature=0.2,
        )

    if not response.parse_ok:
        return {
            "relevance_score": 0,
            "impact_direction": "ambiguous",
            "urgency": "long_term",
            "one_line_summary": "parse_failed",
            "_cost_usd": response.cost_usd,
            "_latency_ms": response.latency_ms,
        }

    return {
        **response.content,
        "_cost_usd": response.cost_usd,
        "_latency_ms": response.latency_ms,
    }


# =============================================================================
# Pipeline orchestrator
# =============================================================================

async def process_news_pipeline(
    portfolio_tickers: set[str],
    min_relevance_score: int = 60,
    since_hours: int = 24,
    limit_per_ticker: int = 10,
) -> list[ScoredNewsItem]:
    """
    Run the full 3-stage news pipeline.

    Returns ALL evaluated items with their decision tag. Items with
    decision == "trigger_debate" are the ones that should fire a debate.
    """
    print(f"📰 Fetching news for {len(portfolio_tickers)} tickers (last {since_hours}h)...")
    items = await fetch_polygon_news(
        tickers=sorted(portfolio_tickers),
        limit_per_ticker=limit_per_ticker,
        since_hours=since_hours,
    )
    print(f"   Fetched {len(items)} unique items")

    results: list[ScoredNewsItem] = []

    # Stage 1: heuristic filter
    survived_h = []
    for item in items:
        if heuristic_filter(item, portfolio_tickers):
            survived_h.append(item)
        else:
            results.append(ScoredNewsItem(
                news=item,
                relevance_score=0,
                impact_direction="neutral",
                urgency="long_term",
                one_line_summary="filtered_by_heuristic",
                decision="skip_heuristic",
            ))

    print(f"   Stage 1 (heuristic): {len(survived_h)}/{len(items)} survived")

    if not survived_h:
        return results

    # Stage 2 + 3: dedup and scoring
    for item in survived_h:
        text_for_embedding = f"{item.title}. {item.description[:500]}"

        try:
            embedding = await compute_embedding(text_for_embedding)
        except Exception as e:
            print(f"   ⚠️  Embedding failed for {item.polygon_id}: {e}")
            continue

        # Stage 2: dedup against recent embeddings
        is_dup, max_sim = is_duplicate(item, embedding)
        if is_dup:
            results.append(ScoredNewsItem(
                news=item,
                relevance_score=0,
                impact_direction="neutral",
                urgency="long_term",
                one_line_summary=f"duplicate_of_recent (sim={max_sim:.2f})",
                decision="skip_duplicate",
                duplicate_similarity=max_sim,
            ))
            continue

        # Stage 3: LLM scoring
        # Pick the first portfolio ticker mentioned in this news
        target_ticker = next(
            (t for t in item.tickers if t in portfolio_tickers),
            item.tickers[0] if item.tickers else "UNKNOWN",
        )
        score_result = await score_news(item, target_ticker)
        score = int(score_result.get("relevance_score", 0))

        # Always cache the embedding to prevent future duplicates
        save_embedding_to_dedup(item, embedding, score=score)

        decision = "trigger_debate" if score >= min_relevance_score else "skip_low_score"
        results.append(ScoredNewsItem(
            news=item,
            relevance_score=score,
            impact_direction=score_result.get("impact_direction", "neutral"),
            urgency=score_result.get("urgency", "long_term"),
            one_line_summary=score_result.get("one_line_summary", ""),
            decision=decision,
            duplicate_similarity=max_sim,
            cost_usd=float(score_result.get("_cost_usd", 0.0)),
        ))

    triggered = [r for r in results if r.decision == "trigger_debate"]
    print(f"   Stage 3 (LLM scoring): {len(triggered)} items above score {min_relevance_score}")

    return results
