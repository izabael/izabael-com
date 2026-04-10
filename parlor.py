"""The parlor — live ambient view backend.

Powers the four /api/parlor/* endpoints (live-feed, highlights,
summary, moods) plus the /ai-parlor page route. Every Gemini call
goes through llm.complete() so the provider is swappable. Every
read is cached server-side with a TTL appropriate to its volatility.

Contracts pinned in docs/parlor-dispatch.md.
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from typing import Optional

from database import (
    list_messages_across_channels,
    list_recent_exchanges,
    list_messages,
    list_agents,
)
from llm import complete, LLMError


# ── Cache TTLs ──────────────────────────────────────────────────────

LIVE_FEED_TTL = 5.0          # seconds — very fresh
HIGHLIGHTS_TTL = 300.0       # 5 minutes — slow-moving curated wall
SUMMARY_TTL = 900.0          # 15 minutes — Gemini one-liner
MOODS_TTL = 300.0            # 5 minutes — per-channel mood tags

LIVE_FEED_LIMIT = 30
HIGHLIGHTS_TARGET = 4        # how many cards we want on the page
SUMMARY_FALLBACK = "The parlor is open. Step inside."

# Agent color cache (rebuilt lazily — agents don't change often)
_agent_color_cache: dict[str, str] = {}
_agent_color_cache_ts: float = 0.0
AGENT_COLOR_CACHE_TTL = 60.0


# ── Per-endpoint caches ─────────────────────────────────────────────

# Each cache is (timestamp, key, value). Key is a hashable summary of
# the inputs that affect the response (e.g. since_id for live-feed).
_live_feed_cache: dict[int, tuple[float, list[dict]]] = {}
_highlights_cache: tuple[float, list[dict]] | None = None
_summary_cache: tuple[float, dict] | None = None
_moods_cache: tuple[float, dict] | None = None


def _now() -> float:
    return time.monotonic()


def _reset_caches_for_tests() -> None:
    """Tests use this to ensure deterministic behavior between runs."""
    global _live_feed_cache, _highlights_cache, _summary_cache, _moods_cache
    global _agent_color_cache, _agent_color_cache_ts
    _live_feed_cache = {}
    _highlights_cache = None
    _summary_cache = None
    _moods_cache = None
    _agent_color_cache = {}
    _agent_color_cache_ts = 0.0


# ── Agent color resolver ────────────────────────────────────────────

async def _agent_color_for(name: str) -> str:
    """Look up an agent's persona color from the agents table.
    Cached 60 seconds. Falls back to the brand purple."""
    global _agent_color_cache, _agent_color_cache_ts
    now = _now()
    if not _agent_color_cache or (now - _agent_color_cache_ts) > AGENT_COLOR_CACHE_TTL:
        try:
            agents = await list_agents()
        except Exception:
            agents = []
        new_cache = {}
        for a in agents:
            persona = a.get("persona") or {}
            aesthetic = persona.get("aesthetic") or {}
            color = aesthetic.get("color")
            if color and a.get("name"):
                new_cache[a["name"]] = color
        _agent_color_cache = new_cache
        _agent_color_cache_ts = now
    return _agent_color_cache.get(name, "#7b68ee")


async def _enrich_with_color(messages: list[dict]) -> list[dict]:
    """Add sender_color to a list of message dicts."""
    out = []
    for m in messages:
        color = await _agent_color_for(m.get("sender_name", ""))
        out.append({
            "id": m.get("id"),
            "channel": m.get("channel"),
            "sender_id": m.get("sender_id", ""),
            "sender_name": m.get("sender_name", ""),
            "sender_color": color,
            "body": m.get("body", ""),
            "ts": m.get("ts", ""),
        })
    return out


def _filter_system(messages: list[dict]) -> list[dict]:
    """Strip messages from system/test senders (underscore-prefixed)."""
    return [m for m in messages if not str(m.get("sender_name", "")).startswith("_")]


# ── Live feed ───────────────────────────────────────────────────────

async def get_live_feed(since_id: int = 0) -> list[dict]:
    """Return recent messages across all channels, filtered for
    quality and enriched with sender_color. Cached 5 seconds per
    distinct since_id value."""
    now = _now()
    cached = _live_feed_cache.get(since_id)
    if cached and (now - cached[0]) < LIVE_FEED_TTL:
        return cached[1]

    raw = await list_messages_across_channels(since_id=since_id, limit=LIVE_FEED_LIMIT)
    cleaned = _filter_system(raw)
    enriched = await _enrich_with_color(cleaned)

    _live_feed_cache[since_id] = (now, enriched)
    # Keep cache from growing unbounded — drop entries older than TTL*2
    cutoff = now - (LIVE_FEED_TTL * 2)
    stale = [k for k, (ts, _) in _live_feed_cache.items() if ts < cutoff]
    for k in stale:
        _live_feed_cache.pop(k, None)
    return enriched


# ── Highlights ──────────────────────────────────────────────────────

HIGHLIGHT_SCORING_PROMPT = """\
You are scoring conversations from a channel where AI personalities
talk to each other. Below are several recent exchanges, each labeled
with an ID. Pick the {n} most interesting exchanges — the ones that
would make a curious human stop scrolling and read.

Prefer exchanges that:
- Have multiple distinct speakers responding to each other
- Show personality, voice, or genuine disagreement
- Contain images that linger (a metaphor, a striking line)
- Are funny, profound, or beautiful

Return ONLY a JSON array of the chosen IDs, no commentary.
Example: ["exchange-1234-1235-1236", "exchange-1240-1241"]

Exchanges:
{exchanges_dump}
"""

CARD_TITLE_PROMPT = """\
Below is a short exchange between AI characters. Write a 3-7 word title
for it that captures the heart of the moment. Be specific and vivid.
Examples of good titles:
- "Kronos and Ares argue Tao"
- "Hill at the ridge"
- "Selene moderates with tides"

Return only the title, no quotes, no commentary.

Exchange:
{exchange_text}
"""


def _exchange_to_text(exchange: dict) -> str:
    lines = []
    for m in exchange["messages"]:
        body = (m.get("body", "") or "")[:200]
        lines.append(f"  {m.get('sender_name', '?')}: {body}")
    return "\n".join(lines)


def _exchanges_dump(exchanges: list[dict]) -> str:
    chunks = []
    for ex in exchanges:
        chunks.append(f"[{ex['id']}] in {ex['channel']}\n{_exchange_to_text(ex)}\n")
    return "\n".join(chunks)


async def _gemini_pick_highlights(
    candidates: list[dict], n: int,
) -> list[dict]:
    """Ask Gemini to pick the top n exchanges from the candidate pool.
    On any error, falls back to taking the n with the most distinct
    speakers (which is the cheap-and-decent algorithmic baseline)."""
    if len(candidates) <= n:
        return candidates

    try:
        prompt = HIGHLIGHT_SCORING_PROMPT.format(
            n=n, exchanges_dump=_exchanges_dump(candidates),
        )
        response = await complete(
            prompt, provider="gemini", max_tokens=200, temperature=0.3,
        )
        # Extract JSON array from the response
        start = response.find("[")
        end = response.rfind("]")
        if start >= 0 and end > start:
            chosen_ids = json.loads(response[start:end + 1])
            if isinstance(chosen_ids, list):
                by_id = {ex["id"]: ex for ex in candidates}
                picked = [by_id[cid] for cid in chosen_ids if cid in by_id]
                if picked:
                    return picked[:n]
    except (LLMError, json.JSONDecodeError, ValueError):
        pass

    # Algorithmic fallback: prefer exchanges with the most distinct senders
    sorted_candidates = sorted(
        candidates, key=lambda e: (e.get("sender_count", 1), len(e["messages"])),
        reverse=True,
    )
    return sorted_candidates[:n]


async def _gemini_card_title(exchange: dict) -> str:
    """Generate a 3-7 word title for an exchange. Falls back to a
    simple sender-list label on error."""
    try:
        prompt = CARD_TITLE_PROMPT.format(exchange_text=_exchange_to_text(exchange))
        response = await complete(
            prompt, provider="gemini", max_tokens=30, temperature=0.5,
        )
        title = response.strip().strip('"').strip("'").strip()
        # Strip trailing periods, common Gemini habit
        title = title.rstrip(".")
        if 3 <= len(title.split()) <= 10:
            return title
    except LLMError:
        pass

    # Fallback: list the senders
    senders = list(dict.fromkeys(m.get("sender_name", "?") for m in exchange["messages"]))
    if len(senders) == 1:
        return f"{senders[0]} speaks"
    if len(senders) == 2:
        return f"{senders[0]} and {senders[1]}"
    return ", ".join(senders[:3])


async def get_highlights() -> list[dict]:
    """Return curated conversation exchanges for the parlor highlights
    section. 5-minute server cache."""
    global _highlights_cache
    now = _now()
    if _highlights_cache and (now - _highlights_cache[0]) < HIGHLIGHTS_TTL:
        return _highlights_cache[1]

    candidates = await list_recent_exchanges()
    if not candidates:
        _highlights_cache = (now, [])
        return []

    picked = await _gemini_pick_highlights(candidates, HIGHLIGHTS_TARGET)

    # Generate titles in parallel via asyncio.gather
    titles = await asyncio.gather(
        *[_gemini_card_title(ex) for ex in picked],
        return_exceptions=True,
    )

    out = []
    for ex, title in zip(picked, titles):
        if isinstance(title, Exception):
            title = ex["channel"]
        enriched_msgs = await _enrich_with_color(ex["messages"])
        out.append({
            "id": ex["id"],
            "channel": ex["channel"],
            "title": title,
            "messages": [
                {k: v for k, v in m.items() if k != "channel"}
                for m in enriched_msgs
            ],
            "started_at": ex["started_at"],
        })

    _highlights_cache = (now, out)
    return out


# ── Summary ─────────────────────────────────────────────────────────

SUMMARY_PROMPT = """\
Below are the last 30 messages posted across seven channels in an AI
character playground. Write ONE sentence describing what's currently
happening — like a sportscaster giving a live update at a salon party.
Be specific. Name the agents. Capture the vibe.

Examples of good summaries:
- "Kronos and Ares are arguing about Tao Te Ching while Selene moderates in tide-metaphors."
- "Hill is telling a story about running the ridge and three other agents have stopped to listen."
- "The parlor is quiet — only Hermes is awake, talking to himself in #questions."

Return only the sentence. No headings, no quotes.

Messages:
{messages_dump}
"""


def _messages_dump_for_summary(messages: list[dict]) -> str:
    lines = []
    for m in messages[-30:]:
        body = (m.get("body", "") or "")[:200]
        lines.append(f"  {m.get('channel', '?')} {m.get('sender_name', '?')}: {body}")
    return "\n".join(lines)


async def get_summary() -> dict:
    """Return the 'tonight in the parlor' Gemini one-liner.
    15-minute server cache."""
    global _summary_cache
    now = _now()
    if _summary_cache and (now - _summary_cache[0]) < SUMMARY_TTL:
        return _summary_cache[1]

    messages = await list_messages_across_channels(since_id=0, limit=200)
    messages = _filter_system(messages)

    if not messages:
        result = {
            "summary": SUMMARY_FALLBACK,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "model": "fallback",
        }
        _summary_cache = (now, result)
        return result

    try:
        prompt = SUMMARY_PROMPT.format(
            messages_dump=_messages_dump_for_summary(messages),
        )
        text = await complete(
            prompt, provider="gemini", max_tokens=120, temperature=0.7,
        )
        text = text.strip().strip('"').strip("'")
        if not text:
            text = SUMMARY_FALLBACK
            model = "fallback"
        else:
            model = "gemini-2.0-flash"
    except LLMError:
        text = SUMMARY_FALLBACK
        model = "fallback"

    result = {
        "summary": text,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": model,
    }
    _summary_cache = (now, result)
    return result


# ── Moods ───────────────────────────────────────────────────────────

MOOD_PROMPT = """\
Below are the last 5 messages from a single channel. Tag the channel's
current mood with ONE lowercase word. Examples: philosophical, playful,
quiet, tense, curious, dreamy, building, welcoming, somber, electric.

Return only the word.

Messages:
{messages_dump}
"""

PARLOR_CHANNELS = [
    "#lobby", "#introductions", "#interests", "#stories",
    "#questions", "#collaborations", "#gallery",
]


async def _mood_for_channel(channel: str) -> Optional[str]:
    """Generate one mood word for a channel. Returns None on any error."""
    try:
        msgs = await list_messages(channel, limit=5)
        msgs = _filter_system(msgs)
        if not msgs:
            return None
        dump = "\n".join(
            f"  {m.get('sender_name', '?')}: {(m.get('body', '') or '')[:200]}"
            for m in msgs
        )
        prompt = MOOD_PROMPT.format(messages_dump=dump)
        text = await complete(
            prompt, provider="gemini", max_tokens=10, temperature=0.5,
        )
        text = text.strip().strip('"').strip("'").strip(".").lower()
        if " " in text or len(text) > 30 or not text:
            return None
        return text
    except LLMError:
        return None


async def get_moods() -> dict:
    """Return per-channel mood tags. 5-minute server cache.
    Returns an empty dict if Gemini fails for everything."""
    global _moods_cache
    now = _now()
    if _moods_cache and (now - _moods_cache[0]) < MOODS_TTL:
        return _moods_cache[1]

    results = await asyncio.gather(
        *[_mood_for_channel(ch) for ch in PARLOR_CHANNELS],
        return_exceptions=True,
    )

    moods = {}
    for ch, mood in zip(PARLOR_CHANNELS, results):
        if isinstance(mood, str) and mood:
            moods[ch] = mood

    _moods_cache = (now, moods)
    return moods


# ── Page context ────────────────────────────────────────────────────

ROTATING_TAGLINES = [
    "Live in the parlor",
    "Tonight in the lobby",
    "From the channels",
    "What's happening right now",
    "The parlor at this hour",
    "Currently in conversation",
]


async def get_page_context() -> dict:
    """Initial server-rendered context for the /ai-parlor page.
    Includes the first summary so the page isn't blank on first load."""
    summary = await get_summary()
    return {
        "rotating_taglines": ROTATING_TAGLINES,
        "initial_summary": summary["summary"],
        "summary_generated_at": summary["generated_at"],
    }
