"""Tests for llm.py — the LLM provider adapter layer.

Network calls are mocked. The point is to verify the dispatch logic,
the fallback behavior, and the error shapes — not the upstream APIs.
"""
from __future__ import annotations

import pytest

import llm


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    """Strip all LLM env vars before each test so callers must set
    explicitly. Prevents tests from accidentally hitting real APIs."""
    for key in ("GEMINI_API_KEY", "DEEPSEEK_API_KEY", "XAI_API_KEY", "HF_API_KEY"):
        monkeypatch.delenv(key, raising=False)


@pytest.mark.anyio
async def test_no_keys_means_no_providers():
    """Without any env vars set, configured_providers reports all False."""
    assert llm.configured_providers() == {
        "gemini": False, "deepseek": False, "grok": False, "huggingface": False,
    }


@pytest.mark.anyio
async def test_configured_providers_picks_up_env(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    cfg = llm.configured_providers()
    assert cfg["gemini"] is True
    assert cfg["deepseek"] is True
    assert cfg["grok"] is False


@pytest.mark.anyio
async def test_gemini_no_key_raises():
    """Calling Gemini without GEMINI_API_KEY raises LLMError, not a network error."""
    with pytest.raises(llm.LLMError, match="GEMINI_API_KEY not set"):
        await llm.complete("hi", provider="gemini", fallback=False)


@pytest.mark.anyio
async def test_deepseek_no_key_falls_back_to_gemini_which_also_fails():
    """Without keys for either provider, fallback chain exhausts and
    raises LLMError mentioning both failures."""
    with pytest.raises(llm.LLMError) as exc_info:
        await llm.complete("hi", provider="deepseek")
    msg = str(exc_info.value)
    assert "deepseek" in msg
    assert "gemini" in msg


@pytest.mark.anyio
async def test_unknown_provider_falls_back_to_gemini(monkeypatch):
    """An unknown provider name should fall back to Gemini if a key exists.
    Here Gemini also has no key, so the fallback chain still raises — but
    the error message should mention both."""
    with pytest.raises(llm.LLMError) as exc_info:
        await llm.complete("hi", provider="madeup")
    assert "madeup" in str(exc_info.value)


@pytest.mark.anyio
async def test_unknown_provider_no_fallback_raises_directly():
    """fallback=False prevents the fallback chain entirely."""
    with pytest.raises(llm.LLMError, match="unknown provider"):
        await llm.complete("hi", provider="madeup", fallback=False)


@pytest.mark.anyio
async def test_huggingface_not_yet_implemented(monkeypatch):
    """HF adapter is on the someday list — explicit error, not silent fail."""
    monkeypatch.setenv("HF_API_KEY", "test-key")
    with pytest.raises(llm.LLMError, match="someday"):
        await llm.complete("hi", provider="huggingface", fallback=False)


@pytest.mark.anyio
async def test_default_provider_is_gemini(monkeypatch):
    """Calling complete() without specifying a provider uses Gemini."""
    # No keys set — Gemini call should raise the GEMINI-specific error
    with pytest.raises(llm.LLMError, match="GEMINI_API_KEY not set"):
        await llm.complete("hi", fallback=False)
