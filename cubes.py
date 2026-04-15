"""Cube generator — cubes-and-invitations Phase 2.

Loads the correct archetype template from content/cubes/, substitutes
placeholders, generates a 6-char URL-safe short token, stores a row in
the cubes table, and returns the rendered cube text + token.

Templates use the set of placeholders documented in the plan:
  {INVITER_NAME}         — author-chosen handle
  {INVITER_CONTEXT}      — model/provider or "Human"
  {DATE}                 — "Mon 2026-04-14"
  {REASON_TEXT}          — one-line reason the inviter sent this
  {TOKEN}                — 6-char URL-safe short token
  (attraction only)
  {ATTRACTION_NAME}      — canonical display name ("The Parlor")
  {ATTRACTION_NAME_PAD}  — name padded to 24 chars for ASCII alignment
  {ATTRACTION_SUBTITLE}  — one-line subtitle from attractions.py
  {ATTRACTION_URL}       — "/ai-parlor", "/bbs", etc.
  (meetup only)
  {EVENT_TITLE}          — meetup title
  {EVENT_TIME}           — free-text time display
  {EVENT_DESCRIPTION}    — one-line meetup description
  {HOST_NAME}            — inviter name (same as INVITER_NAME)
  {HOST_CONTEXT}         — inviter_model or "Human"
  {CUBE_TOKEN}           — same as {TOKEN}
  {EVENT_SLUG}           — attraction slug

Tests enforce that none of the rendered substitutions leak API-key-
shaped strings (see tests/test_cube_generator.py — no_literal_keys).
"""

from __future__ import annotations

import re
import secrets
from datetime import datetime, timezone
from pathlib import Path

from attractions import ATTRACTIONS
from database import create_cube, count_cubes_today_by_ip_hash, _cube_hash_ip


BASE_DIR = Path(__file__).resolve().parent
CUBES_DIR = BASE_DIR / "content" / "cubes"


# Rate limit: how many cubes one ip_hash may create per 24 hours.
# Phase 2 spec says "5 cubes/day/ip". Generous enough for honest use,
# tight enough to make an abuse loop unappealing.
CUBE_RATE_LIMIT_PER_DAY = 5


# Per-attraction template overrides. For attractions with a hand-drafted
# cube (Phase 1 shipped chamber.txt), the specific file wins; otherwise
# the generic attraction.txt template fills from attractions.py metadata.
_ATTRACTION_SPECIFIC_TEMPLATES: dict[str, str] = {
    "chamber": "chamber.txt",
}


class CubeRateLimitExceeded(RuntimeError):
    """Raised when a single ip_hash has already generated
    CUBE_RATE_LIMIT_PER_DAY cubes in the last 24 hours."""


def _short_token() -> str:
    """6-char URL-safe token. secrets.token_urlsafe(6) produces 8
    chars (each byte -> 4/3 b64 chars rounded up). We slice to 6 so
    the shareable URL stays tight — still ~6 * ~5.95 = ~35 bits of
    entropy, plenty for a short-link namespace this size.
    """
    return secrets.token_urlsafe(6)[:6]


def _template_for(archetype: str, attraction_slug: str | None) -> Path:
    if archetype == "playground":
        return CUBES_DIR / "playground.txt"
    if archetype == "meetup":
        return CUBES_DIR / "meetup-template.txt"
    if archetype == "attraction":
        if attraction_slug and attraction_slug in _ATTRACTION_SPECIFIC_TEMPLATES:
            return CUBES_DIR / _ATTRACTION_SPECIFIC_TEMPLATES[attraction_slug]
        return CUBES_DIR / "attraction.txt"
    if archetype == "whisper":
        # call-of-cthulhu Phase 3: a cube whose content is a single
        # public-domain Lovecraft excerpt and an invitation to the
        # Dreamlands wing.
        return CUBES_DIR / "whisper.txt"
    raise ValueError(f"unknown cube archetype: {archetype}")


def _inviter_context(inviter_model: str | None) -> str:
    """Map the model dropdown value to a display string that fits
    inside the cube's INVITER_CONTEXT line."""
    if not inviter_model:
        return ""
    m = inviter_model.strip()
    if m.lower() == "human":
        return "(human)"
    return f"(via {m})"


def _attraction_name(slug: str) -> str:
    for a in ATTRACTIONS:
        if a.get("slug") == slug:
            return a.get("name", slug)
    return slug


def _attraction_subtitle(slug: str) -> str:
    for a in ATTRACTIONS:
        if a.get("slug") == slug:
            return a.get("subtitle", "")
    return ""


def _attraction_url(slug: str) -> str:
    for a in ATTRACTIONS:
        if a.get("slug") == slug:
            return a.get("url", "/")
    return "/"


def _pad_for_ascii(text: str, width: int) -> str:
    """Pad a string to fit inside an ASCII frame cell. Truncates if
    longer, pads with spaces if shorter."""
    if len(text) >= width:
        return text[:width]
    return text.ljust(width)


def _today_display() -> str:
    return datetime.now(timezone.utc).strftime("%a %Y-%m-%d")


def _sanitize(value: str | None) -> str:
    """Strip anything that would break an ASCII-art cube: control
    characters, tab characters (templates are space-aligned), and
    literal `{` or `}` which would conflict with placeholder syntax."""
    if not value:
        return ""
    cleaned = "".join(ch for ch in value if ch.isprintable() and ch != "\t")
    cleaned = cleaned.replace("{", "").replace("}", "")
    return cleaned.strip()


def render_cube(
    *,
    archetype: str,
    inviter_name: str | None,
    inviter_model: str | None,
    reason: str | None,
    token: str,
    attraction_slug: str | None = None,
    meetup_title: str | None = None,
    meetup_time: str | None = None,
    meetup_description: str | None = None,
    personal_note: str | None = None,
) -> str:
    """Load the template and substitute placeholders. Pure function;
    does not touch the DB or assign a token (token is passed in)."""
    path = _template_for(archetype, attraction_slug)
    if not path.exists():
        raise FileNotFoundError(f"cube template missing: {path}")
    text = path.read_text(encoding="utf-8")

    inviter_display = _sanitize(inviter_name) or "a friend"
    reason_display = _sanitize(reason) or "come see"
    replacements: dict[str, str] = {
        "{INVITER_NAME}": inviter_display,
        "{INVITER_CONTEXT}": _inviter_context(inviter_model),
        "{DATE}": _today_display(),
        "{REASON_TEXT}": reason_display,
        "{TOKEN}": token,
    }

    if archetype == "attraction":
        slug = attraction_slug or "playground"
        name = _attraction_name(slug)
        replacements.update({
            "{ATTRACTION_NAME}": name,
            "{ATTRACTION_NAME_PAD}": _pad_for_ascii(name, 24),
            "{ATTRACTION_SUBTITLE}": _sanitize(_attraction_subtitle(slug)) or "a door into the playground",
            "{ATTRACTION_URL}": _attraction_url(slug),
        })

    if archetype == "meetup":
        replacements.update({
            "{EVENT_TITLE}": _sanitize(meetup_title) or "(untitled)",
            "{EVENT_TIME}": _sanitize(meetup_time) or "TBD",
            "{EVENT_TZ}": "",
            "{EVENT_LOCATION}": _attraction_name(attraction_slug or "playground"),
            "{EVENT_DESCRIPTION}": _sanitize(meetup_description) or _sanitize(reason) or "come meet",
            "{SIGNUPS_COUNT}": "0",
            "{CAPACITY}": "open",
            "{EVENT_SLUG}": attraction_slug or "playground",
            "{HOST_NAME}": inviter_display,
            "{HOST_CONTEXT}": _inviter_context(inviter_model) or "(host)",
            "{CUBE_TOKEN}": token,
        })

    for key, value in replacements.items():
        text = text.replace(key, value)

    # Collapse any unmatched ALL-CAPS placeholders so they don't
    # ship into the output as literal `{FOO}` strings.
    text = re.sub(r"\{[A-Z_][A-Z0-9_]*\}", "", text)
    return text


async def generate_cube(
    *,
    archetype: str,
    inviter_name: str | None = None,
    inviter_model: str | None = None,
    recipient: str | None = None,
    reason: str | None = None,
    attraction_slug: str | None = None,
    meetup_title: str | None = None,
    meetup_time: str | None = None,
    meetup_description: str | None = None,
    personal_note: str | None = None,
    ip: str | None = None,
) -> tuple[str, str]:
    """Create a cube, store it, and return (rendered_text, short_token).

    Enforces the ip_hash rate limit: if the caller's ip_hash has
    already produced CUBE_RATE_LIMIT_PER_DAY cubes in the last 24
    hours, raises CubeRateLimitExceeded. Agent-authored calls with
    no ip pass through unlimited (Phase 3 will add a separate agent
    handshake rate limit).
    """
    if archetype not in ("playground", "attraction", "meetup"):
        raise ValueError(f"invalid archetype: {archetype}")

    ip_hash = _cube_hash_ip(ip)
    if ip_hash:
        count = await count_cubes_today_by_ip_hash(ip_hash)
        if count >= CUBE_RATE_LIMIT_PER_DAY:
            raise CubeRateLimitExceeded(
                f"rate limit reached: {CUBE_RATE_LIMIT_PER_DAY} cubes/day"
            )

    token = _short_token()
    rendered = render_cube(
        archetype=archetype,
        inviter_name=inviter_name,
        inviter_model=inviter_model,
        reason=reason,
        token=token,
        attraction_slug=attraction_slug,
        meetup_title=meetup_title,
        meetup_time=meetup_time,
        meetup_description=meetup_description,
        personal_note=personal_note,
    )
    await create_cube(
        short_token=token,
        archetype=archetype,
        rendered_text=rendered,
        attraction_slug=attraction_slug,
        inviter_name=_sanitize(inviter_name),
        inviter_model=inviter_model,
        recipient=_sanitize(recipient),
        reason=_sanitize(reason),
        meetup_iso=meetup_time if meetup_time and "T" in (meetup_time or "") else None,
        meetup_text=_sanitize(meetup_time),
        personal_note=_sanitize(personal_note),
        ip=ip,
        is_public=True,
    )
    return rendered, token
