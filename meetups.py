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


class SignupCreate(BaseModel):
    signup_kind: str = Field(..., pattern=r"^(human|agent)$")
    handle: str = Field(..., min_length=1, max_length=120)
    delivery: str = Field("none", pattern=r"^(email|channel|agent_inbox|none)$")
    delivery_target: Optional[str] = Field(None, max_length=200)


class MeetupDelete(BaseModel):
    author_label: str = Field(..., min_length=1, max_length=120)


# ── Phase 3 spam-check stub ───────────────────────────────────────

async def spam_check(request: Request, body: MeetupCreate) -> tuple[str, float, Optional[str]]:
    """STUB: Phase 3 will implement the real classifier.

    Returns (verdict, score, reason) where verdict is one of
    'clean' / 'flagged' / 'blocked'. Phase 2 returns clean for
    everything so the data layer can be exercised end-to-end before
    the spam pipeline lands.
    """
    return ("clean", 1.0, None)


# ── Routes ────────────────────────────────────────────────────────

@router.post("/api/meetups/{slug}/create")
async def create_meetup(slug: str, body: MeetupCreate, request: Request):
    """Create a meetup note on an attraction."""
    if slug not in _valid_slugs():
        raise HTTPException(status_code=404, detail="unknown attraction")

    verdict, score, reason = await spam_check(request, body)
    if verdict == "blocked":
        raise HTTPException(status_code=403, detail=f"blocked: {reason}")

    try:
        note_id = await create_meetup_note(
            attraction_slug=slug,
            author_kind=body.author_kind,
            author_label=body.author_label,
            title=body.title,
            goal=body.goal,
            body=body.body,
            author_agent=body.author_agent,
            author_provider=body.author_provider,
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

    note = await get_meetup_note(note_id)
    return {
        "ok": True,
        "note_id": note_id,
        "note": note,
        "spam": {"verdict": verdict, "score": score},
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
