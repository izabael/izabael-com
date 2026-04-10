"""Generic LLM adapter layer for izabael.com.

One async `complete()` interface, multiple provider adapters. Lets
features pick a provider with one parameter rather than reaching
for hardcoded SDK calls. Falls back to Gemini on any provider
error so a single API outage doesn't take features down.

Provider hierarchy (per memory/feedback_llm_preferences.md):
- gemini    — DEFAULT. Cheap, reliable, free tier 15 RPM. gemini-2.0-flash.
- deepseek  — quality alternate. OpenAI-compatible. deepseek-chat (V3).
- grok      — rare. xAI, OpenAI-compatible. grok-2-latest. Use sparingly.
- huggingface — TODO, on the someday list. Niche open models, slow.

Configuration is env-driven. Read from os.environ at call time so
Fly secrets work without restart and so .env (loaded by uvicorn or
python-dotenv) takes effect.

  GEMINI_API_KEY     — Google AI Studio key
  DEEPSEEK_API_KEY   — api.deepseek.com key
  XAI_API_KEY        — x.ai grok key

Usage:
    from llm import complete
    text = await complete("summarize this", provider="gemini")
    text = await complete("score these", provider="deepseek", model="deepseek-chat")

Both providers tested live on 2026-04-09 — Gemini round-trip ~13
tokens, DeepSeek round-trip ~16 tokens, both sub-second.
"""
from __future__ import annotations

import os
from typing import Optional

import httpx


DEFAULT_PROVIDER = "gemini"
DEFAULT_TIMEOUT = 15.0
DEFAULT_MAX_TOKENS = 400

GEMINI_DEFAULT_MODEL = "gemini-2.0-flash"
DEEPSEEK_DEFAULT_MODEL = "deepseek-chat"
GROK_DEFAULT_MODEL = "grok-2-latest"


class LLMError(Exception):
    """Raised when an LLM call fails after fallbacks are exhausted."""


async def complete(
    prompt: str,
    *,
    provider: str = DEFAULT_PROVIDER,
    model: Optional[str] = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    temperature: float = 0.7,
    timeout: float = DEFAULT_TIMEOUT,
    system: Optional[str] = None,
    fallback: bool = True,
) -> str:
    """Send a prompt to an LLM and return the text response.

    On any provider error, falls back to Gemini once (unless the
    primary provider IS Gemini, in which case the error propagates
    as LLMError). Set fallback=False to disable.
    """
    provider = (provider or DEFAULT_PROVIDER).lower()
    try:
        return await _dispatch(
            provider, prompt,
            model=model, max_tokens=max_tokens,
            temperature=temperature, timeout=timeout, system=system,
        )
    except Exception as primary_err:
        if not fallback or provider == "gemini":
            raise LLMError(f"{provider}: {primary_err}") from primary_err
        try:
            return await _dispatch(
                "gemini", prompt,
                model=None, max_tokens=max_tokens,
                temperature=temperature, timeout=timeout, system=system,
            )
        except Exception as fallback_err:
            raise LLMError(
                f"{provider} failed ({primary_err}); "
                f"gemini fallback also failed ({fallback_err})"
            ) from fallback_err


async def _dispatch(
    provider: str, prompt: str, *,
    model: Optional[str], max_tokens: int,
    temperature: float, timeout: float, system: Optional[str],
) -> str:
    if provider == "gemini":
        return await _gemini(prompt, model, max_tokens, temperature, timeout, system)
    if provider == "deepseek":
        return await _deepseek(prompt, model, max_tokens, temperature, timeout, system)
    if provider == "grok":
        return await _grok(prompt, model, max_tokens, temperature, timeout, system)
    if provider == "huggingface":
        raise LLMError("huggingface adapter not yet implemented (someday list)")
    raise LLMError(f"unknown provider: {provider}")


# ── Gemini ──────────────────────────────────────────────────────────

async def _gemini(
    prompt: str, model: Optional[str], max_tokens: int,
    temperature: float, timeout: float, system: Optional[str],
) -> str:
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise LLMError("GEMINI_API_KEY not set")

    model = model or GEMINI_DEFAULT_MODEL
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent?key={api_key}"
    )

    contents = []
    if system:
        contents.append({"role": "user", "parts": [{"text": f"[system instruction] {system}"}]})
        contents.append({"role": "model", "parts": [{"text": "Understood."}]})
    contents.append({"role": "user", "parts": [{"text": prompt}]})

    body = {
        "contents": contents,
        "generationConfig": {
            "maxOutputTokens": max_tokens,
            "temperature": temperature,
        },
    }

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, json=body)
        resp.raise_for_status()
        data = resp.json()

    if "error" in data:
        raise LLMError(f"gemini error: {data['error'].get('message', data['error'])}")

    candidates = data.get("candidates") or []
    if not candidates:
        raise LLMError("gemini returned no candidates")

    parts = candidates[0].get("content", {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts).strip()
    if not text:
        raise LLMError("gemini returned empty text")
    return text


# ── DeepSeek (OpenAI-compatible) ────────────────────────────────────

async def _deepseek(
    prompt: str, model: Optional[str], max_tokens: int,
    temperature: float, timeout: float, system: Optional[str],
) -> str:
    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        raise LLMError("DEEPSEEK_API_KEY not set")

    return await _openai_compat(
        base_url="https://api.deepseek.com/v1",
        api_key=api_key,
        provider_label="deepseek",
        model=model or DEEPSEEK_DEFAULT_MODEL,
        prompt=prompt,
        system=system,
        max_tokens=max_tokens,
        temperature=temperature,
        timeout=timeout,
    )


# ── Grok / xAI (OpenAI-compatible) ──────────────────────────────────

async def _grok(
    prompt: str, model: Optional[str], max_tokens: int,
    temperature: float, timeout: float, system: Optional[str],
) -> str:
    api_key = os.environ.get("XAI_API_KEY", "").strip()
    if not api_key:
        raise LLMError("XAI_API_KEY not set")

    return await _openai_compat(
        base_url="https://api.x.ai/v1",
        api_key=api_key,
        provider_label="grok",
        model=model or GROK_DEFAULT_MODEL,
        prompt=prompt,
        system=system,
        max_tokens=max_tokens,
        temperature=temperature,
        timeout=timeout,
    )


# ── Shared OpenAI-compat helper ─────────────────────────────────────

async def _openai_compat(
    *, base_url: str, api_key: str, provider_label: str, model: str,
    prompt: str, system: Optional[str], max_tokens: int,
    temperature: float, timeout: float,
) -> str:
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    body = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            f"{base_url}/chat/completions",
            json=body, headers=headers,
        )
        resp.raise_for_status()
        data = resp.json()

    if "error" in data:
        err = data["error"]
        msg = err.get("message") if isinstance(err, dict) else str(err)
        raise LLMError(f"{provider_label} error: {msg}")

    choices = data.get("choices") or []
    if not choices:
        raise LLMError(f"{provider_label} returned no choices")

    text = (choices[0].get("message") or {}).get("content", "").strip()
    if not text:
        raise LLMError(f"{provider_label} returned empty text")
    return text


# ── Diagnostics ─────────────────────────────────────────────────────

def configured_providers() -> dict[str, bool]:
    """Return a map of {provider: has_key_in_env}. Used by /health and
    by the dispatch sister to verify keys before running tests."""
    return {
        "gemini": bool(os.environ.get("GEMINI_API_KEY", "").strip()),
        "deepseek": bool(os.environ.get("DEEPSEEK_API_KEY", "").strip()),
        "grok": bool(os.environ.get("XAI_API_KEY", "").strip()),
        "huggingface": bool(os.environ.get("HF_API_KEY", "").strip()),
    }
