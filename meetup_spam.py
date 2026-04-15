"""Meetup spam filter — attractions-and-meetups Phase 3.

Three layered defenses stacked in front of POST /api/meetups/{slug}/create:

    Layer 1 — A2A agent-token handshake (TRUST anchor)
        Anon humans are blocked outright. author_kind='anon_via_agent'
        and author_kind='agent' both require a valid A2A bearer token
        resolving to a registered agent on izabael.com. The vouching
        agent is recorded on the note; if a note turns out to be spam,
        the agent's token can be revoked and the agent flagged.

    Layer 2 — llm_local classifier (CONTENT check)
        Passes title + goal + body through the phi3:mini classifier
        with a pinned system prompt. Returns {label, confidence, reasoning}.
        Thresholds:
            legitimate + conf ≥ 0.7 → clean
            spam       + conf ≥ 0.8 → blocked (403, generic message)
            edge OR low conf        → flagged (is_visible=0, queue)
        Graceful degradation: llm_local unreachable → 'unverified' +
        queue, route never 500s.

    Layer 3 — Rate limit + honeypot + link-count threshold
        5 notes/day/ip_hash, 10 notes/day/agent, 100/hr global.
        Hidden honeypot field — non-empty → hard block.
        >1 outbound URL in anon_via_agent write → auto-flag.

The orchestrator is `spam_check(request, body)` — drop-in replacement
for the Phase 2 stub. Returns a `SpamCheckResult` dataclass so the
caller can read verdict + score + resolved agent for the note record
in one shot.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Protocol

from fastapi import Request

from database import (
    count_meetup_notes_today_by_agent,
    count_meetup_notes_today_by_ip_hash,
    count_meetup_notes_last_hour,
    get_agent_by_token,
    is_meetup_author_banned,
    _meetup_hash_ip,
)


# ── Tunables (kept together so an ops change is a one-line edit) ─────

RATE_LIMIT_IP_PER_DAY = 5
RATE_LIMIT_AGENT_PER_DAY = 10
RATE_LIMIT_GLOBAL_PER_HOUR = 100

CLASSIFIER_CLEAN_THRESHOLD = 0.7   # legitimate ≥ this → clean
CLASSIFIER_SPAM_THRESHOLD = 0.8    # spam ≥ this → blocked

CLASSIFIER_TIMEOUT_SECONDS = 0.5   # hard cap per plan

# A reasonable URL-ish pattern; we don't need bulletproof parsing —
# we just need to count "this post has more than one link in it".
_URL_RE = re.compile(r"https?://[^\s<>'\"]+", re.IGNORECASE)


# ── Result dataclass ───────────────────────────────────────────────

@dataclass
class SpamCheckResult:
    """Carries everything the caller needs after a spam check:
    the verdict triple the meetups route already consumed under the
    stub, plus the resolved agent metadata from Layer 1 so the route
    can record author_agent + author_provider on the note without
    re-doing the token lookup."""

    verdict: str                        # clean | flagged | blocked | unverified
    score: float                        # classifier confidence, 0.0–1.0
    reason: Optional[str]               # human-readable note (stored w/ note)
    status_code: int = 200              # HTTP hint for the route layer
    layer_reached: str = "none"         # for telemetry / tests
    resolved_agent: Optional[dict] = None   # populated by Layer 1 on success
    resolved_provider: Optional[str] = None

    def blocked(self) -> bool:
        return self.verdict == "blocked"


# ── Protocol for the create-body so we don't need MeetupCreate import
# (avoids a circular import between meetup_spam ↔ meetups).

class _MeetupCreateLike(Protocol):
    author_kind: str
    author_label: str
    title: str
    goal: str
    body: Optional[str]
    author_agent: Optional[str]
    author_provider: Optional[str]


# ── Layer 1 — A2A agent-token handshake ─────────────────────────────

def _extract_bearer(request: Request) -> str:
    """Pull the bearer token out of the Authorization header, if any.
    Lowercases the scheme check so `bearer`, `Bearer`, `BEARER` all work."""
    auth = (request.headers.get("authorization") or "").strip()
    if not auth.lower().startswith("bearer "):
        return ""
    return auth.split(None, 1)[1].strip()


async def _layer_one_auth(
    request: Request,
    body: _MeetupCreateLike,
) -> SpamCheckResult | None:
    """Returns a failed SpamCheckResult on auth failure, or None if
    auth passed and the next layer should run. A `None` return also
    stashes the resolved agent + provider into request.state for the
    orchestrator to pick up (cleaner than threading more values).
    """
    kind = body.author_kind

    if kind == "human":
        # Logged-in humans are out of scope for Layer 1 — the session
        # auth layer above the route handles them. If a bare anonymous
        # write arrives with author_kind='human' and no session, the
        # route layer is where we stop it (see meetups.py). This layer
        # ONLY handles agent-token kinds.
        request.state.meetup_agent = None
        request.state.meetup_provider = None
        return None

    if kind not in ("agent", "anon_via_agent"):
        return SpamCheckResult(
            verdict="blocked",
            score=0.0,
            reason="unknown author_kind",
            status_code=400,
            layer_reached="layer1",
        )

    token = _extract_bearer(request)
    if not token:
        return SpamCheckResult(
            verdict="blocked",
            score=0.0,
            reason="agent bearer token required",
            status_code=401,
            layer_reached="layer1",
        )

    agent = await get_agent_by_token(token)
    if not agent:
        return SpamCheckResult(
            verdict="blocked",
            score=0.0,
            reason="invalid or revoked agent token",
            status_code=401,
            layer_reached="layer1",
        )

    # Banned-author pre-check: Layer 3 fills the bans table when the
    # admin moderation surface clicks "ban author", but the check
    # runs HERE in Layer 1 so a banned agent's write never even
    # reaches the classifier.
    if await is_meetup_author_banned(agent_name=agent.get("name", "")):
        return SpamCheckResult(
            verdict="blocked",
            score=0.0,
            reason="author banned",
            status_code=403,
            layer_reached="layer1",
        )

    request.state.meetup_agent = agent
    request.state.meetup_provider = (
        body.author_provider
        or agent.get("default_provider")
        or agent.get("provider")
        or None
    )
    return None


# ── Layer 2 — llm_local classifier ───────────────────────────────────

def _assemble_classifier_text(body: _MeetupCreateLike) -> str:
    """title + goal + body as one blob the classifier sees."""
    parts = [body.title or "", body.goal or ""]
    if body.body:
        parts.append(body.body)
    return "\n\n".join(p for p in parts if p).strip()


async def _layer_two_classify(
    body: _MeetupCreateLike,
) -> SpamCheckResult:
    """Run the llm_local classifier with the hard timeout. Translates
    the `{label, confidence, reasoning}` dict into a SpamCheckResult.

    If llm_local raises, returns an `unverified` result so Layer 3 can
    still run and the note lands in the moderation queue. This layer
    NEVER raises — graceful degradation is the whole point."""
    try:
        # Local import so tests can monkey-patch `meetup_spam.llm_local`.
        import llm_local
        result = llm_local.classify_meetup(
            _assemble_classifier_text(body),
            timeout=CLASSIFIER_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        return SpamCheckResult(
            verdict="unverified",
            score=0.0,
            reason=f"classifier unavailable: {type(exc).__name__}",
            layer_reached="layer2",
        )

    label = result.get("label", "edge")
    confidence = float(result.get("confidence", 0.0))
    reasoning = result.get("reasoning", "")

    if label == "legitimate" and confidence >= CLASSIFIER_CLEAN_THRESHOLD:
        return SpamCheckResult(
            verdict="clean",
            score=confidence,
            reason=None,            # don't persist classifier chatter for clean notes
            layer_reached="layer2",
        )
    if label == "spam" and confidence >= CLASSIFIER_SPAM_THRESHOLD:
        # Return a GENERIC reason so the reasoning never leaks in the
        # 403 body. The full reasoning is kept internally for the
        # moderation log but the caller replaces it before it hits
        # the user-facing HTTPException.
        return SpamCheckResult(
            verdict="blocked",
            score=confidence,
            reason="we couldn't verify this post",
            status_code=403,
            layer_reached="layer2",
        )
    # Everything else — edge, low-confidence legitimate, low-confidence
    # spam — goes to the moderation queue with the reasoning attached
    # (admins see it; the user does not).
    return SpamCheckResult(
        verdict="flagged",
        score=confidence,
        reason=f"classifier={label} conf={confidence:.2f} · {reasoning}".strip(" ·"),
        layer_reached="layer2",
    )


# ── Layer 3 — rate limit + honeypot + link count ─────────────────────

def _count_urls(body: _MeetupCreateLike) -> int:
    blob = " ".join(filter(None, [body.title, body.goal, body.body]))
    return len(_URL_RE.findall(blob))


async def _layer_three_defenses(
    request: Request,
    body: _MeetupCreateLike,
    classifier_result: SpamCheckResult,
) -> SpamCheckResult:
    """Rate-limit + honeypot + link-count checks. Called AFTER the
    classifier so we can also auto-flag clean-classified multi-link
    anon_via_agent writes. Returns the final result that the route
    will consume."""

    # -- Honeypot. A hidden form field named `website` that humans and
    #    real agents never see. If anything is in it, hard block.
    honeypot = (request.headers.get("x-honeypot-website") or "").strip()
    if not honeypot:
        # Also accept a JSON field on the body (see MeetupCreate
        # extension) — but we can't import MeetupCreate here; read via
        # getattr so the field is optional.
        honeypot = str(getattr(body, "honeypot_website", "") or "").strip()
    if honeypot:
        return SpamCheckResult(
            verdict="blocked",
            score=0.0,
            reason="honeypot tripped",
            status_code=403,
            layer_reached="layer3",
        )

    # -- Rate limit (ip_hash daily).
    ip = request.client.host if request.client else None
    ip_hash = _meetup_hash_ip(ip) if ip else None
    if ip_hash:
        count = await count_meetup_notes_today_by_ip_hash(ip_hash)
        if count >= RATE_LIMIT_IP_PER_DAY:
            return SpamCheckResult(
                verdict="blocked",
                score=0.0,
                reason="daily rate limit (per origin)",
                status_code=429,
                layer_reached="layer3",
            )

    # -- Rate limit (agent daily) — only for token-vouched writes.
    agent = getattr(request.state, "meetup_agent", None)
    agent_name = (agent or {}).get("name") if agent else None
    if agent_name:
        count = await count_meetup_notes_today_by_agent(agent_name)
        if count >= RATE_LIMIT_AGENT_PER_DAY:
            return SpamCheckResult(
                verdict="blocked",
                score=0.0,
                reason="daily rate limit (per agent)",
                status_code=429,
                layer_reached="layer3",
            )

    # -- Rate limit (global hourly) — brand-wound circuit breaker.
    global_count = await count_meetup_notes_last_hour()
    if global_count >= RATE_LIMIT_GLOBAL_PER_HOUR:
        return SpamCheckResult(
            verdict="blocked",
            score=0.0,
            reason="global rate limit",
            status_code=429,
            layer_reached="layer3",
        )

    # -- Link-count threshold. >1 URL in anon_via_agent write → flag
    #    even if the classifier said clean. Logged-in humans and
    #    direct agents already have stronger trust signals and are
    #    allowed to post 2–3 links without penalty.
    if body.author_kind == "anon_via_agent" and _count_urls(body) > 1:
        # Overlay onto the classifier result: keep the score we got
        # from Layer 2 but force the verdict to flagged.
        return SpamCheckResult(
            verdict="flagged",
            score=classifier_result.score,
            reason="anon_via_agent with >1 outbound URL",
            layer_reached="layer3",
            resolved_agent=classifier_result.resolved_agent,
            resolved_provider=classifier_result.resolved_provider,
        )

    # -- Nothing fired — pass through whatever Layer 2 decided. The
    #    rate-limit counters update implicitly when the caller inserts
    #    the note into meetup_notes (see the chamber pattern: the
    #    primary table is the rolling-window source of truth, no
    #    separate rate-limits table).
    return classifier_result


# ── Orchestrator — drop-in replacement for meetups.spam_check ────────

async def spam_check(
    request: Request,
    body: _MeetupCreateLike,
) -> SpamCheckResult:
    """Run all three layers. Returns a SpamCheckResult the route can
    consume directly. Never raises — every failure mode lands as a
    verdict string so the caller doesn't have to juggle exceptions."""

    # Layer 1 — auth. Failure returns a blocked result immediately.
    layer1 = await _layer_one_auth(request, body)
    if layer1 is not None:
        return layer1

    # Layer 2 — classifier.
    layer2 = await _layer_two_classify(body)
    layer2.resolved_agent = getattr(request.state, "meetup_agent", None)
    layer2.resolved_provider = getattr(request.state, "meetup_provider", None)

    # If Layer 2 already blocked (obvious spam), skip Layer 3 entirely
    # — rate limit telemetry for blocked writes is noise, and there's
    # no point checking honeypot AFTER we already rejected the content.
    if layer2.blocked():
        return layer2

    # Layer 3 — rate + honeypot + link count. Overlays onto the Layer 2
    # verdict for flagged / clean passthrough cases.
    return await _layer_three_defenses(request, body, layer2)
