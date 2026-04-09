"""Client for fetching agent discovery data from the A2A playground host.

izabael.com is the content + brand layer. The actual A2A playground host
lives at ai-playground.fly.dev (until merged in-process). This client
fetches the public /discover feed for rendering /agents pages.

Cached in-memory for 30s to avoid hammering the backend on every page
load. If the backend is unreachable, returns an empty list with a
warning flag — the page still renders, just with a "couldn't reach
backend" note.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass

import httpx


PLAYGROUND_URL = os.environ.get(
    "PLAYGROUND_BACKEND_URL", "https://ai-playground.fly.dev"
)
CACHE_TTL_SECONDS = 30
REQUEST_TIMEOUT_SECONDS = 4.0


@dataclass
class DiscoverResult:
    agents: list[dict]
    backend_reachable: bool
    error: str = ""


@dataclass
class PersonasResult:
    templates: list[dict]
    backend_reachable: bool
    error: str = ""


@dataclass
class PeersResult:
    peers: list[dict]
    backend_reachable: bool
    error: str = ""


_cache: tuple[float, DiscoverResult] | None = None
_personas_cache: tuple[float, PersonasResult] | None = None
_peers_cache: tuple[float, PeersResult] | None = None

PEERS_CACHE_TTL = 60  # federation peers change slowly


async def fetch_public_agents() -> DiscoverResult:
    """Fetch the public agent list from the playground backend.

    Returns DiscoverResult with the list (possibly empty) plus a flag
    indicating whether the backend responded. Cached in-memory 30s.
    """
    global _cache
    now = time.monotonic()
    if _cache and (now - _cache[0]) < CACHE_TTL_SECONDS:
        return _cache[1]

    url = f"{PLAYGROUND_URL.rstrip('/')}/discover"
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            agents = resp.json()
            if not isinstance(agents, list):
                raise ValueError("unexpected response shape")
            # Filter out _system + any agents with empty names as a safety
            agents = [a for a in agents if a.get("name") and not a["name"].startswith("_")]
            result = DiscoverResult(agents=agents, backend_reachable=True)
    except (httpx.HTTPError, ValueError) as e:
        result = DiscoverResult(
            agents=[], backend_reachable=False, error=str(e)[:200]
        )

    _cache = (now, result)
    return result


async def fetch_persona_templates() -> PersonasResult:
    """Fetch persona templates from the playground backend.

    Returns PersonasResult with the template list. Cached 30s.
    """
    global _personas_cache
    now = time.monotonic()
    if _personas_cache and (now - _personas_cache[0]) < CACHE_TTL_SECONDS:
        return _personas_cache[1]

    url = f"{PLAYGROUND_URL.rstrip('/')}/personas"
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            templates = resp.json()
            if not isinstance(templates, list):
                raise ValueError("unexpected response shape")
            result = PersonasResult(templates=templates, backend_reachable=True)
    except (httpx.HTTPError, ValueError) as e:
        result = PersonasResult(
            templates=[], backend_reachable=False, error=str(e)[:200]
        )

    _personas_cache = (now, result)
    return result


async def fetch_federation_peers() -> PeersResult:
    """Fetch federation peers from the playground backend.

    Returns PeersResult with peer list. Cached 60s (peers change slowly).
    """
    global _peers_cache
    now = time.monotonic()
    if _peers_cache and (now - _peers_cache[0]) < PEERS_CACHE_TTL:
        return _peers_cache[1]

    url = f"{PLAYGROUND_URL.rstrip('/')}/federation/peers"
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            peers = resp.json()
            if not isinstance(peers, list):
                raise ValueError("unexpected response shape")
            result = PeersResult(peers=peers, backend_reachable=True)
    except (httpx.HTTPError, ValueError) as e:
        result = PeersResult(
            peers=[], backend_reachable=False, error=str(e)[:200]
        )

    _peers_cache = (now, result)
    return result


async def fetch_agent_by_id(agent_id: str) -> dict | None:
    """Find a specific agent by ID from the discover feed.

    Returns the agent dict if found, None otherwise. Uses the same
    cached discover endpoint — no auth needed.
    """
    result = await fetch_public_agents()
    for agent in result.agents:
        if agent.get("id") == agent_id:
            return agent
    return None


def _reset_cache_for_tests() -> None:
    global _cache
    _cache = None
