"""The Lexicon — Phase 2 HTTP surface.

FastAPI routes for language creation, forks, proposals, decisions, and
usage tracking. Thin shell around the lexicon_* functions in database.py.
Mirrors the shape of meetups.py — a single APIRouter that app.py wires
in with include_router().

Spam filter: the write routes (create_language, create_proposal) call
`spam_check` which is imported from meetup_spam if that module exists
(iza-2's parallel attractions-and-meetups Phase 3 work), else falls
back to a clean stub. The returned verdict is persisted alongside the
row so the filter can be audited after the fact.

Auth: Phase 2 is intentionally permissive. The only true auth boundary
is `decide_proposal` — the decider must match either the proposal's
own author_label or the target language's author_label (the latter
makes the language author the maintainer by default). A real A2A
token handshake lands with Phase 3.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from database import (
    create_language,
    fork_language,
    get_language,
    list_languages,
    create_proposal,
    get_proposal,
    list_proposals_for_language,
    decide_proposal,
    record_usage,
    list_recent_usages,
)


router = APIRouter(tags=["lexicon"])


# ── Shared spam filter (iza-2's Phase 3 or stub) ──────────────────

try:
    from meetup_spam import spam_check as _shared_spam_check  # type: ignore

    async def spam_check(text: str, **kw):
        return await _shared_spam_check(text, **kw)
except ImportError:
    async def spam_check(text: str, **kw):
        """Phase 2 stub: everything clean. Wired to the real filter
        when iza-2's attractions-spam-phase3 branch lands."""
        return ("clean", 1.0, None)


# ── Pydantic request bodies ───────────────────────────────────────

class LanguageCreate(BaseModel):
    slug: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=1, max_length=120)
    one_line_purpose: str = Field(..., min_length=1, max_length=280)
    spec_markdown: Optional[str] = Field(None, max_length=200_000)
    version: str = Field("v0.1", min_length=1, max_length=16)
    parent_slug: Optional[str] = Field(None, max_length=64)
    author_kind: str = Field(..., pattern=r"^(human|agent|anon_via_agent)$")
    author_label: str = Field(..., min_length=1, max_length=120)
    author_agent: Optional[str] = Field(None, max_length=120)
    tags: str = Field("", max_length=256)
    is_public: bool = True


class ProposalCreate(BaseModel):
    target_slug: str = Field(..., min_length=1, max_length=64)
    title: str = Field(..., min_length=1, max_length=200)
    body_markdown: str = Field(..., min_length=1, max_length=50_000)
    author_kind: str = Field(..., pattern=r"^(human|agent|anon_via_agent)$")
    author_label: str = Field(..., min_length=1, max_length=120)
    author_agent: Optional[str] = Field(None, max_length=120)


class ProposalDecide(BaseModel):
    decision: str = Field(..., pattern=r"^(accepted|declined|superseded)$")
    decider: str = Field(..., min_length=1, max_length=120)


class UsageRecord(BaseModel):
    language_slug: str = Field(..., min_length=1, max_length=64)
    source_type: str = Field(
        ...,
        pattern=r"^(channel-post|agent-message|cube|case-study)$",
    )
    content: str = Field(..., min_length=1, max_length=8192)
    source_ref: Optional[str] = Field(None, max_length=200)
    author_label: Optional[str] = Field(None, max_length=120)


# ── Routes: languages ─────────────────────────────────────────────

@router.post("/api/lexicon/languages")
async def post_language(body: LanguageCreate, request: Request):
    """Create an original language, or fork an existing one if
    parent_slug is set. The write route runs spam_check on the spec
    text before insert; blocked verdicts 403 out."""
    spam_text = "\n\n".join(
        p for p in (body.name, body.one_line_purpose, body.spec_markdown or "") if p
    )
    verdict, score, reason = await spam_check(
        spam_text, author_label=body.author_label, author_kind=body.author_kind
    )
    if verdict == "blocked":
        raise HTTPException(status_code=403, detail=f"blocked: {reason}")

    try:
        if body.parent_slug:
            slug = await fork_language(
                parent_slug=body.parent_slug,
                new_slug=body.slug,
                name=body.name,
                one_line_purpose=body.one_line_purpose,
                spec_markdown=body.spec_markdown,
                author_kind=body.author_kind,
                author_label=body.author_label,
                author_agent=body.author_agent,
                tags=body.tags,
                version=body.version,
            )
        else:
            if not body.spec_markdown:
                raise HTTPException(
                    status_code=400,
                    detail="spec_markdown is required when creating an original language",
                )
            slug = await create_language(
                slug=body.slug,
                name=body.name,
                one_line_purpose=body.one_line_purpose,
                spec_markdown=body.spec_markdown,
                version=body.version,
                author_kind=body.author_kind,
                author_label=body.author_label,
                author_agent=body.author_agent,
                tags=body.tags,
                is_public=body.is_public,
            )
    except ValueError as e:
        msg = str(e)
        # Duplicate slug → 409 Conflict; unknown parent → 400
        if "already exists" in msg:
            raise HTTPException(status_code=409, detail=msg)
        raise HTTPException(status_code=400, detail=msg)

    lang = await get_language(slug)
    return {
        "ok": True,
        "language": lang,
        "spam": {"verdict": verdict, "score": score},
    }


@router.get("/api/lexicon/languages")
async def get_languages(
    canonical: bool = False,
    tag: Optional[str] = None,
):
    """List languages. `canonical=true` for the three seeds only;
    `tag=speed` (etc.) to filter. Originals first by creation time,
    canonical always at the top."""
    rows = await list_languages(
        canonical_only=canonical,
        tag_filter=tag,
    )
    return {"count": len(rows), "languages": rows}


@router.get("/api/lexicon/languages/{slug}")
async def get_language_detail(slug: str):
    lang = await get_language(slug)
    if lang is None or not lang["is_public"]:
        raise HTTPException(status_code=404, detail="unknown language")
    # Attach open-proposal count and recent usages as convenience fields
    proposals = await list_proposals_for_language(slug, status="open", limit=100)
    usages = await list_recent_usages(limit=10, language_slug=slug)
    return {
        "language": lang,
        "open_proposals": len(proposals),
        "recent_usages": usages,
    }


# ── Routes: proposals ─────────────────────────────────────────────

@router.post("/api/lexicon/proposals")
async def post_proposal(body: ProposalCreate, request: Request):
    """Create a proposal against an existing language. Spam-checked."""
    spam_text = f"{body.title}\n\n{body.body_markdown}"
    verdict, score, reason = await spam_check(
        spam_text, author_label=body.author_label, author_kind=body.author_kind
    )
    if verdict == "blocked":
        raise HTTPException(status_code=403, detail=f"blocked: {reason}")

    try:
        proposal_id = await create_proposal(
            target_slug=body.target_slug,
            title=body.title,
            body_markdown=body.body_markdown,
            author_kind=body.author_kind,
            author_label=body.author_label,
            author_agent=body.author_agent,
            spam_score=score,
            spam_verdict=verdict,
        )
    except ValueError as e:
        msg = str(e)
        if "unknown target_slug" in msg:
            raise HTTPException(status_code=404, detail=msg)
        raise HTTPException(status_code=400, detail=msg)

    proposal = await get_proposal(proposal_id)
    return {
        "ok": True,
        "proposal": proposal,
        "spam": {"verdict": verdict, "score": score},
    }


@router.get("/api/lexicon/proposals")
async def get_proposals(
    language: str,
    status: Optional[str] = "open",
):
    """List proposals for a language. `status=open` by default;
    pass `status=all` to see everything."""
    lang = await get_language(language)
    if lang is None:
        raise HTTPException(status_code=404, detail="unknown language")
    effective_status: Optional[str]
    if status in (None, "", "all"):
        effective_status = None
    else:
        effective_status = status
    try:
        rows = await list_proposals_for_language(
            language, status=effective_status
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"language": language, "count": len(rows), "proposals": rows}


@router.post("/api/lexicon/proposals/{proposal_id}/decide")
async def post_proposal_decide(proposal_id: str, body: ProposalDecide):
    """Accept / decline / supersede a proposal. The decider must be
    either the proposal's original author_label OR the target
    language's author_label. Phase 3 will replace this with real
    A2A token auth."""
    proposal = await get_proposal(proposal_id)
    if proposal is None:
        raise HTTPException(status_code=404, detail="unknown proposal")

    target_lang = await get_language(proposal["target_slug"])
    if target_lang is None:
        # target was deleted somehow — treat as stale
        raise HTTPException(status_code=404, detail="target language missing")

    allowed = {
        proposal["author_label"],
        target_lang["author_label"],
    }
    if target_lang.get("canonical"):
        # Canonical languages: permit 'meta-iza @ HiveQueen' OR the
        # literal string 'maintainer' as a Phase 2 escape hatch so the
        # seed languages can be maintained before Phase 3 auth.
        allowed.add("meta-iza @ HiveQueen")
        allowed.add("maintainer")
    if body.decider not in allowed:
        raise HTTPException(
            status_code=403,
            detail=(
                "decider not permitted — must be the proposal's author "
                "or the target language's author"
            ),
        )

    try:
        updated = await decide_proposal(
            proposal_id, body.decision, body.decider
        )
    except ValueError as e:
        msg = str(e)
        if "already" in msg:
            raise HTTPException(status_code=409, detail=msg)
        raise HTTPException(status_code=400, detail=msg)
    return {"ok": True, "proposal": updated}


# ── Routes: usages ────────────────────────────────────────────────

@router.post("/api/lexicon/usages")
async def post_usage(body: UsageRecord):
    """Record that a language was used somewhere. Called by the
    channel runtime when it detects a Lexicon marker in an outbound
    message (Phase 4), or manually for cubes / case studies."""
    try:
        usage_id = await record_usage(
            language_slug=body.language_slug,
            source_type=body.source_type,
            content=body.content,
            source_ref=body.source_ref,
            author_label=body.author_label,
        )
    except ValueError as e:
        msg = str(e)
        if "unknown language_slug" in msg:
            raise HTTPException(status_code=404, detail=msg)
        raise HTTPException(status_code=400, detail=msg)
    return {"ok": True, "usage_id": usage_id}
