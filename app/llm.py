"""Thin async clients for the AI engines. Raw HTTP, no SDKs, so a provider change is a one-line fix.

Every call returns (text, error). A provider without a key returns (None, "not configured") and the
audit marks anything that depended on it as "not measured" rather than scoring it zero.
"""
from __future__ import annotations

import json
import re

import httpx

from . import config

TIMEOUT = httpx.Timeout(120.0, connect=15.0)


def _loc(location: dict | None) -> dict:
    location = location or {}
    out = {"type": "approximate", "country": location.get("country") or "US"}
    if location.get("city"):
        out["city"] = location["city"]
    if location.get("region"):
        out["region"] = location["region"]
    return out


# ── Claude ───────────────────────────────────────────────────────────────────

async def _claude(payload: dict) -> tuple[dict | None, str | None]:
    if not config.ANTHROPIC_API_KEY:
        return None, "not configured"
    headers = {"x-api-key": config.ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01",
               "content-type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.post("https://api.anthropic.com/v1/messages", headers=headers, json=payload)
            if r.status_code != 200:
                return None, f"HTTP {r.status_code}: {r.text[:300]}"
            data = r.json()
            # Server tools can pause a long turn; continue once.
            if data.get("stop_reason") == "pause_turn":
                payload = dict(payload)
                payload["messages"] = payload["messages"] + [{"role": "assistant", "content": data["content"]}]
                r = await c.post("https://api.anthropic.com/v1/messages", headers=headers, json=payload)
                if r.status_code == 200:
                    data = r.json()
            return data, None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"[:300]


def _claude_text(data: dict) -> str:
    return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text").strip()


def parse_json(text: str):
    """Pull the first JSON object/array out of a model reply."""
    if not text:
        raise ValueError("empty")
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        m = re.search(r"(\{.*\}|\[.*\])", t, re.S)
        if not m:
            raise
        return json.loads(m.group(1))


async def claude_json(system: str, prompt: str, model: str | None = None, max_tokens: int = 4000):
    data, err = await _claude({
        "model": model or config.CLAUDE_MODEL,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": prompt}],
    })
    if err:
        return None, err
    try:
        return parse_json(_claude_text(data)), None
    except Exception as e:
        return None, f"bad JSON from model: {e}"


async def ask_claude(question: str, location: dict | None) -> tuple[str | None, str | None]:
    data, err = await _claude({
        "model": config.CLAUDE_MODEL,
        "max_tokens": 1500,
        "messages": [{"role": "user", "content": question}],
        "tools": [{"type": config.CLAUDE_SEARCH_TOOL, "name": "web_search", "max_uses": 3,
                   "user_location": _loc(location)}],
    })
    return (_claude_text(data), None) if data else (None, err)


# ── OpenAI (ChatGPT with web search) ─────────────────────────────────────────

async def ask_openai(question: str, location: dict | None) -> tuple[str | None, str | None]:
    if not config.OPENAI_API_KEY:
        return None, "not configured"
    body = {"model": config.OPENAI_MODEL, "input": question,
            "tools": [{"type": config.OPENAI_SEARCH_TOOL, "user_location": _loc(location)}]}
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.post("https://api.openai.com/v1/responses", json=body,
                             headers={"Authorization": f"Bearer {config.OPENAI_API_KEY}"})
            if r.status_code != 200:
                return None, f"HTTP {r.status_code}: {r.text[:300]}"
            data = r.json()
            if data.get("output_text"):
                return data["output_text"].strip(), None
            parts = []
            for item in data.get("output", []):
                if item.get("type") == "message":
                    for c2 in item.get("content", []):
                        if c2.get("type") in ("output_text", "text"):
                            parts.append(c2.get("text", ""))
            return "".join(parts).strip() or None, None if parts else "empty response"
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"[:300]


# ── Gemini (Google Search grounding) ─────────────────────────────────────────

async def ask_gemini(question: str, location: dict | None) -> tuple[str | None, str | None]:
    if not config.GEMINI_API_KEY:
        return None, "not configured"
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/{config.GEMINI_MODEL}:generateContent")
    body = {"contents": [{"role": "user", "parts": [{"text": question}]}], "tools": [{"google_search": {}}]}
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.post(url, json=body, headers={"x-goog-api-key": config.GEMINI_API_KEY})
            if r.status_code != 200:
                return None, f"HTTP {r.status_code}: {r.text[:300]}"
            data = r.json()
            parts = (data.get("candidates") or [{}])[0].get("content", {}).get("parts", [])
            text = "".join(p.get("text", "") for p in parts).strip()
            return (text, None) if text else (None, "empty response")
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"[:300]


# ── Perplexity ───────────────────────────────────────────────────────────────

async def ask_perplexity(question: str, location: dict | None) -> tuple[str | None, str | None]:
    if not config.PERPLEXITY_API_KEY:
        return None, "not configured"
    body = {"model": config.PERPLEXITY_MODEL, "messages": [{"role": "user", "content": question}]}
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.post("https://api.perplexity.ai/chat/completions", json=body,
                             headers={"Authorization": f"Bearer {config.PERPLEXITY_API_KEY}"})
            if r.status_code != 200:
                return None, f"HTTP {r.status_code}: {r.text[:300]}"
            text = r.json()["choices"][0]["message"]["content"].strip()
            return text, None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"[:300]


ENGINES = {
    "ChatGPT": ask_openai,
    "Gemini": ask_gemini,
    "Perplexity": ask_perplexity,
    "Claude": ask_claude,
}


def configured_engines() -> list[str]:
    keys = {"ChatGPT": config.OPENAI_API_KEY, "Gemini": config.GEMINI_API_KEY,
            "Perplexity": config.PERPLEXITY_API_KEY, "Claude": config.ANTHROPIC_API_KEY}
    return [name for name, k in keys.items() if k]
