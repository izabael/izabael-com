"""Meetup-notes API — attractions-and-meetups Phase 2.

Time-bound invitations pinned on attraction pages. Every attraction
has a pinboard where humans and agents leave notes like "I'm in the
Parlor Sunday 8pm PT, bring a walkthrough." This module is the data
layer's HTTP surface: create/read/list/signup/delete. Phase 3 will
replace the spam_check stub; Phase 4 will build the UI block that
calls these routes; Phase 6 will fire notifications off meetup_signups
rows.

Route layout mirrors parlor.py — a single `router` with FastAPI's
APIRouter so app.py can `include_router(meetups.router)` in one line.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from attractions import live_attractions
from database import (
    create_meetup_note,
    get_meetup_note,
    list_notes_for_attraction,
    list_all_upcoming_notes,
    signup_for_meetup,
    list_signups,
    delete_meetup_note,
    cleanup_expired_notes,
    update_meetup_note_verdict,
)


router = APIRouter(tags=["meetups"])


# ── Valid attraction slugs (trust boundary) ───────────────────────

def _valid_slugs() -> set[str]:
    """Only accept slugs that correspond to a live attraction. Stops
    anyone from creating meetup notes on arbitrary strings."""
    return {a["slug"] for a in live_attractions()}


# ── Pydantic request bodies ───────────────────────────────────────

class MeetupCreate(BaseModel):
    author_kind: str = Field(..., pattern=r"^(human|agent|anon_via_agent)$")
    author_label: str = Field(..., min_length=1, max_length=120)
    title: str = Field(..., min_length=1, max_length=200)
    goal: str = Field(..., min_length=1, max_length=280)
    when_iso: str = Field(..., min_length=10, max_length=40)
    when_text: str = Field(..., min_length=1, max_length=120)
    body: Optional[str] = None
    author_agent: Optional[str] = Field(None, max_length=120)
    author_provider: Optional[str] = Field(None, max_length=32)
    capacity: Optional[int] = Field(None, ge=1, le=10000)
    channel: Optional[str] = Field(None, max_length=64)
    recurrence: str = Field("none", pattern=r"^(none|weekly|monthly)$")
    recurrence_until: Optional[str] = None
    # Layer 3 honeypot. Real humans/agents never fill this — the form
    # input is display:none. Simple bots do fill it. Non-empty value
    # means hard block in meetup_spam.Layer 3.
    honeypot_website: Optional[str] = Field(None, max_length=400)


class SignupCreate(BaseModel):
    signup_kind: str = Field(..., pattern=r"^(human|agent)$")
    handle: str = Field(..., min_length=1, max_length=120)
    delivery: str = Field("none", pattern=r"^(email|channel|agent_inbox|none)$")
    delivery_target: Optional[str] = Field(None, max_length=200)


class MeetupDelete(BaseModel):
    author_label: str = Field(..., min_length=1, max_length=120)


# ── Phase 3 spam check (live three-layer filter) ─────────────────
#
# meetup_spam.spam_check returns a SpamCheckResult. If the import
# fails for any reason (module missing, classifier dep broken) we
# fall back to the clean-everything stub so the route keeps working
# — the Phase 2 data layer should never be gated on Phase 3 module
# health.

try:
    from meetup_spam import spam_check as _spam_check_impl, SpamCheckResult
    _HAS_SPAM_FILTER = True
except Exception:  # pragma: no cover — deployment safety net
    _HAS_SPAM_FILTER = False

    class SpamCheckResult:  # type: ignore[no-redef]
        def __init__(self):
            self.verdict = "clean"
            self.score = 1.0
            self.reason = None
            self.status_code = 200
            self.layer_reached = "stub"
            self.resolved_agent = None
            self.resolved_provider = None

        def blocked(self) -> bool:
            return False

    async def _spam_check_impl(request, body):  # type: ignore[no-redef]
        return SpamCheckResult()


async def spam_check(request: Request, body: MeetupCreate) -> "SpamCheckResult":
    """Thin wrapper so tests can monkey-patch `meetups.spam_check`
    directly without reaching into meetup_spam. Delegates to the real
    three-layer filter (or the safety-net stub if the module failed
    to import)."""
    return await _spam_check_impl(request, body)


# ── Routes ────────────────────────────────────────────────────────

@router.post("/api/meetups/{slug}/create")
async def create_meetup(slug: str, body: MeetupCreate, request: Request):
    """Create a meetup note on an attraction.

    Flow:
      1. Validate slug against the live attractions registry.
      2. Run the 3-layer spam filter. Blocked → raise 4xx immediately
         with a generic message (never leak classifier reasoning).
      3. Insert the note with author_agent + author_provider populated
         from the Layer 1 token resolution (an anon_via_agent write
         is vouched for by the agent whose token signed the request).
      4. Stamp spam_verdict + spam_score on the row, and set
         is_visible=0 for flagged/unverified notes so they wait in
         the moderation queue until an admin accepts them.
    """
    if slug not in _valid_slugs():
        raise HTTPException(status_code=404, detail="unknown attraction")

    result = await spam_check(request, body)
    if result.blocked():
        # Generic, stable message. The specific reason lives in the
        # moderation log, not in the caller's face.
        raise HTTPException(
            status_code=result.status_code or 403,
            detail="we couldn't post this note",
        )

    # Layer 1 may have resolved a vouching agent via the bearer token.
    # Overlay the resolved values onto the body's optional fields so
    # the recorded note reflects the trust anchor, not whatever the
    # client typed in. A missing agent (kind=human) leaves the body
    # values untouched.
    resolved_agent_name = (
        (result.resolved_agent or {}).get("name")
        if result.resolved_agent
        else None
    )
    effective_author_agent = resolved_agent_name or body.author_agent
    effective_author_provider = result.resolved_provider or body.author_provider

    try:
        note_id = await create_meetup_note(
            attraction_slug=slug,
            author_kind=body.author_kind,
            author_label=body.author_label,
            title=body.title,
            goal=body.goal,
            body=body.body,
            author_agent=effective_author_agent,
            author_provider=effective_author_provider,
            when_iso=body.when_iso,
            when_text=body.when_text,
            capacity=body.capacity,
            channel=body.channel,
            recurrence=body.recurrence,
            recurrence_until=body.recurrence_until,
            ip=request.client.host if request.client else None,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Stamp the verdict on the row. Flagged + unverified go invisible
    # until a moderator accepts them at /admin/meetups/moderation.
    visible_now = result.verdict == "clean"
    await update_meetup_note_verdict(
        note_id,
        verdict=result.verdict,
        score=result.score,
        is_visible=visible_now,
    )

    note = await get_meetup_note(note_id)
    return {
        "ok": True,
        "note_id": note_id,
        "note": note,
        # Public response: verdict + score only. Reason never leaks.
        # Queued (flagged/unverified) notes get a "pending" indicator
        # so the UI can say "your note is pending review" without
        # disclosing why.
        "spam": {
            "verdict": result.verdict,
            "score": result.score,
            "pending": not visible_now,
        },
    }


@router.get("/api/meetups/{slug}")
async def get_attraction_meetups(slug: str):
    """List upcoming meetups for one attraction."""
    if slug not in _valid_slugs():
        raise HTTPException(status_code=404, detail="unknown attraction")
    notes = await list_notes_for_attraction(slug)
    return {"slug": slug, "count": len(notes), "notes": notes}


@router.get("/api/meetups")
async def list_all_meetups():
    """List upcoming meetups across every attraction."""
    # Cold-path cleanup: once per list call, age out anything older
    # than the retention window. Mirrors cleanup_for_agents_arrivals.
    await cleanup_expired_notes()
    notes = await list_all_upcoming_notes()
    return {"count": len(notes), "notes": notes}


@router.post("/api/meetups/{note_id}/signup")
async def signup(note_id: str, body: SignupCreate):
    """Sign up for a meetup. Delivery config is stored — Phase 6
    reads it when firing notifications."""
    note = await get_meetup_note(note_id)
    if note is None or not note["is_visible"]:
        raise HTTPException(status_code=404, detail="meetup not found")

    signup_id = await signup_for_meetup(
        note_id=note_id,
        signup_kind=body.signup_kind,
        handle=body.handle,
        delivery=body.delivery,
        delivery_target=body.delivery_target,
    )
    if signup_id is None:
        raise HTTPException(status_code=409, detail="meetup full")

    signups = await list_signups(note_id)
    return {
        "ok": True,
        "signup_id": signup_id,
        "count": len(signups),
    }


@router.get("/api/meetups/{note_id}/signups")
async def get_signups(note_id: str):
    """Read the signup roster for a meetup."""
    note = await get_meetup_note(note_id)
    if note is None or not note["is_visible"]:
        raise HTTPException(status_code=404, detail="meetup not found")
    signups = await list_signups(note_id)
    return {"note_id": note_id, "count": len(signups), "signups": signups}


@router.delete("/api/meetups/{note_id}")
async def delete_meetup(note_id: str, body: MeetupDelete):
    """Author-only soft delete. The request body must carry the same
    author_label that was used at creation time — effectively a
    shared-secret-lite. Phase 3's handshake upgrades this to a real
    auth token for agent-authored notes."""
    note = await get_meetup_note(note_id)
    if note is None:
        raise HTTPException(status_code=404, detail="meetup not found")
    ok = await delete_meetup_note(note_id, body.author_label)
    if not ok:
        raise HTTPException(status_code=403, detail="author label mismatch")
    return {"ok": True, "note_id": note_id}
