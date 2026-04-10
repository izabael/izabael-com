"""Optional read-only fallback to a remote A2A instance during cutover.

izabael.com is now a self-sufficient A2A host (its own SQLite-backed
agents, messages, persona templates). This module exists as a safety
net during the local-first cutover: if the local query returns empty
AND the env flag is set, try one tight-timeout HTTP read against the
configured upstream and return whatever it gives back.

The fallback is OFF by default. Enable it for ~1 week after cutover by
setting Fly secret READ_FALLBACK_ENABLED=1, then disable when local
data is healthy. Writes are never proxied — POST requests always go
straight to the local DB.

Tunables (env vars):
  READ_FALLBACK_ENABLED   "1" to enable, anything else disables
  READ_FALLBACK_URL       upstream A2A host (default: ai-playground.fly.dev)
  READ_FALLBACK_TIMEOUT   per-request timeout in seconds (default: 3.0)
"""
from __future__ import annotations

import os

import httpx


def _enabled() -> bool:
    return os.environ.get("READ_FALLBACK_ENABLED", "") == "1"


def _base_url() -> str:
    return os.environ.get(
        "READ_FALLBACK_URL", "https://ai-playground.fly.dev",
    ).rstrip("/")


def _timeout() -> float:
    try:
        return float(os.environ.get("READ_FALLBACK_TIMEOUT", "3.0"))
    except ValueError:
        return 3.0


async def fallback_agents() -> list[dict]:
    """Fetch /discover from the upstream. Returns [] if disabled or
    on any error — never raises."""
    if not _enabled():
        return []
    try:
        async with httpx.AsyncClient(timeout=_timeout()) as client:
            resp = await client.get(f"{_base_url()}/discover")
            resp.raise_for_status()
            data = resp.json()
            if not isinstance(data, list):
                return []
            return [
                a for a in data
                if isinstance(a, dict)
                and a.get("name")
                and not str(a["name"]).startswith("_")
            ]
    except Exception:
        return []


async def fallback_messages(channel: str, limit: int = 50) -> list[dict]:
    """Fetch recent channel messages from the upstream. Returns [] if
    disabled or on any error. Channel name should include the leading #."""
    if not _enabled():
        return []
    clean = channel.lstrip("#")
    url = f"{_base_url()}/channels/%23{clean}/messages?limit={limit}"
    try:
        async with httpx.AsyncClient(timeout=_timeout()) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
            if not isinstance(data, list):
                return []
            return data
    except Exception:
        return []


def fallback_status() -> dict:
    """Return current fallback configuration for /health and admin views."""
    return {
        "enabled": _enabled(),
        "url": _base_url() if _enabled() else "",
        "timeout": _timeout(),
    }
