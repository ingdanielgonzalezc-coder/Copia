"""
LLM clients — thin wrappers for Claude (Anthropic) and Grok (xAI).

Both clients return a uniform LLMResponse with parsed JSON, token counts,
cost in USD, and latency. JSON parsing is robust to markdown fences and
common LLM output quirks.

Pricing reflects approximate April 2026 published rates and is used for
cost tracking only — not billing.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any

import httpx
from anthropic import AsyncAnthropic
from dotenv import load_dotenv
from tenacity import retry, stop_after_attempt, wait_exponential

load_dotenv()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
XAI_API_KEY = os.getenv("XAI_API_KEY", "")

# Approximate pricing per million tokens (USD), April 2026
PRICING_USD_PER_MTOK = {
    # Claude family
    "claude-sonnet-4-6":   {"input": 3.0,  "output": 15.0},
    "claude-opus-4-6":     {"input": 5.0, "output": 25.0},
    "claude-haiku-4-5":    {"input": 1.0,  "output": 5.0},
    # Grok family
    "grok-4-latest":               {"input": 2.0,  "output": 6.0},
    "grok-4.20-multi-agent-0309":  {"input": 2.0,  "output": 6.0},
    "grok-4.20-0309-reasoning":    {"input": 2.0,  "output": 6.0},
    "grok-4-1-fast-reasoning":     {"input": 0.2,  "output": 0.5},
}


@dataclass
class LLMResponse:
    """Uniform response object across all LLM providers."""

    content: dict[str, Any]    # parsed JSON
    raw_text: str              # original text returned by the model
    model: str
    tokens_in: int
    tokens_out: int
    cost_usd: float
    latency_ms: int
    parse_ok: bool             # False if JSON parsing failed (raw_text is best-effort)


# =============================================================================
# JSON parsing helpers
# =============================================================================

def _strip_markdown_fences(text: str) -> str:
    """Strip ```json and ``` fences that LLMs sometimes wrap output in."""
    text = text.strip()
    # Match ```json ... ``` or ``` ... ```
    pattern = r"^```(?:json)?\s*\n?(.*?)\n?```$"
    m = re.match(pattern, text, re.DOTALL)
    if m:
        return m.group(1).strip()
    return text


def parse_json_response(text: str) -> tuple[dict[str, Any], bool]:
    """
    Parse a JSON response from an LLM.

    Returns (parsed_dict, parse_ok). On failure, parsed_dict contains a
    minimal error structure and parse_ok is False.
    """
    cleaned = _strip_markdown_fences(text)
    try:
        return json.loads(cleaned), True
    except json.JSONDecodeError:
        # Try to find JSON object inside the text (greedy match for first { ... })
        m = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0)), True
            except json.JSONDecodeError:
                pass

    return (
        {
            "error": "json_parse_failed",
            "raw_text_preview": text[:500],
        },
        False,
    )


def _compute_cost(model: str, tokens_in: int, tokens_out: int) -> float:
    """Compute USD cost for a call."""
    pricing = PRICING_USD_PER_MTOK.get(model)
    if not pricing:
        return 0.0
    return (
        (tokens_in / 1_000_000) * pricing["input"]
        + (tokens_out / 1_000_000) * pricing["output"]
    )


# =============================================================================
# Claude client
# =============================================================================

_claude_client: AsyncAnthropic | None = None


def _get_claude_client() -> AsyncAnthropic:
    global _claude_client
    if _claude_client is None:
        if not ANTHROPIC_API_KEY:
            raise RuntimeError("ANTHROPIC_API_KEY not set")
        _claude_client = AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
    return _claude_client


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
async def call_claude(
    model: str,
    system: str,
    user: str,
    max_tokens: int = 1500,
    temperature: float = 0.3,
    extended_thinking: bool = False,
    tools: list[dict[str, Any]] | None = None,
) -> LLMResponse:
    """
    Call a Claude model and return a parsed LLMResponse.

    For Claude 4.6 models (Opus / Sonnet), set extended_thinking=True to
    enable adaptive thinking. The model decides dynamically how much to
    reason. max_tokens must be generous since it includes thinking tokens.

    Args:
        model: Model identifier (e.g. "claude-opus-4-6").
        system: System prompt.
        user: User message.
        max_tokens: Maximum output tokens.
        temperature: Sampling temperature.
        extended_thinking: Enable adaptive thinking for 4.6 models.
        tools: Optional list of tool definitions. For web search:
            [{"type": "web_search_20250305", "name": "web_search", "max_uses": 3}]
    """
    client = _get_claude_client()
    start = time.perf_counter()

    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }

    if extended_thinking:
        # Adaptive thinking: model decides how much reasoning to do
        kwargs["thinking"] = {"type": "adaptive"}
        # Thinking requires temperature=1.0
        kwargs["temperature"] = 1.0

    if tools:
        kwargs["tools"] = tools

    response = await client.messages.create(**kwargs)
    latency_ms = int((time.perf_counter() - start) * 1000)

    # Extract text content (skip thinking blocks, tool_use blocks, etc.)
    text_parts = [
        block.text for block in response.content if hasattr(block, "text")
    ]
    raw_text = "\n".join(text_parts)

    parsed, parse_ok = parse_json_response(raw_text)

    tokens_in = response.usage.input_tokens
    tokens_out = response.usage.output_tokens
    cost = _compute_cost(model, tokens_in, tokens_out)

    return LLMResponse(
        content=parsed,
        raw_text=raw_text,
        model=model,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_usd=cost,
        latency_ms=latency_ms,
        parse_ok=parse_ok,
    )


# =============================================================================
# Grok (xAI) client — OpenAI-compatible API
# =============================================================================

XAI_BASE_URL = "https://api.x.ai/v1"


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
async def call_grok(
    model: str,
    system: str,
    user: str,
    max_tokens: int = 1500,
    temperature: float = 0.7,
    reasoning_effort: str | None = None,
) -> LLMResponse:
    """
    Call a Grok model via the xAI OpenAI-compatible endpoint.

    For multi-agent variants like grok-4.20-multi-agent-0309, set
    reasoning_effort="medium" to activate exactly 4 agents (per xAI docs).
    For non-reasoning models like grok-4-1-fast-reasoning, leave it as None.
    """
    if not XAI_API_KEY:
        raise RuntimeError("XAI_API_KEY not set")

    headers = {
        "Authorization": f"Bearer {XAI_API_KEY}",
        "Content-Type": "application/json",
    }
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }

    if reasoning_effort is not None:
        payload["reasoning"] = {"effort": reasoning_effort}

    start = time.perf_counter()
    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(
            f"{XAI_BASE_URL}/chat/completions",
            headers=headers,
            json=payload,
        )
        if response.status_code >= 400:
            error_body = response.text
            print(f"\n❌ xAI API error {response.status_code}")
            print(f"   Request payload: {json.dumps(payload, indent=2)[:800]}")
            print(f"   Response body: {error_body[:1000]}")
            response.raise_for_status()
        data = response.json()
    latency_ms = int((time.perf_counter() - start) * 1000)

    raw_text = data["choices"][0]["message"]["content"]
    parsed, parse_ok = parse_json_response(raw_text)

    usage = data.get("usage", {})
    tokens_in = usage.get("prompt_tokens", 0)
    tokens_out = usage.get("completion_tokens", 0)
    cost = _compute_cost(model, tokens_in, tokens_out)

    return LLMResponse(
        content=parsed,
        raw_text=raw_text,
        model=model,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_usd=cost,
        latency_ms=latency_ms,
        parse_ok=parse_ok,
    )
