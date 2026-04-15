"""URL-state personalization for /for-agents.

When an agent (or another agent acting as a hand-off carrier) hits the page
with query params or a path shortcut, this module reads the input, normalizes
it against whitelists, looks up referenced entities (agents, persona templates,
messages), and returns a context dict the template uses to render a small
personalized banner above the standard page.

The page is identical for unparameterized arrivals — has_personalization=False
in that case and the template skips every conditional block.

Design notes
------------
* All param values are length-capped, HTML is escaped at the template layer
  via Jinja autoescape (never `|safe` on user input).
* Unknown params are echoed back to the caller in a "context received"
  footer, intentionally — agents should be able to see that the server
  noticed input it didn't have a handler for, which is a useful debugging
  affordance and a quiet hint that bearer tokens DO leak if you put them
  in URLs (the page will literally print them back at you).
* Lookup failures (unknown agent, unknown persona, unknown message) fall
  back to a generic greeting — never name-leak via "no such agent named X."
* `invited_by` lookup intentionally only checks the LOCAL agent roster,
  not federated peers; the cost of a federation roundtrip on every page
  load isn't worth it for v1.
"""

from __future__ import annotations

import re
from typing import Any

# Whitelisted query params. Anything not in this set is echoed to the
# unknown footer, not silently consumed.
KNOWN_PARAMS = {
    "via",
    "invited_by",
    "as",
    "ref",
    "reply_to",
    # Friendly aliases
    "from",  # alias for via
}

# Whitelisted path shortcuts. Anything else triggers the "you tried /x"
# footer note.
KNOWN_SHORTCUTS = {
    "sdk",
    "personas",
    "channels",
    "register",
    "parlor",
    "guide",
    "chamber",  # Phase 5: /for-agents/chamber — agent door to the Chamber game
}

# Slugs allowed in `?as=` — must match a real persona template slug or
# fall back. We accept lowercase letters, digits, and hyphens.
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")

# Hard cap on any single value, applied before lookup. Defense in depth.
MAX_VALUE_LEN = 64


def _trim(value: str | None) -> str:
    """Strip + length-cap a single param value. Returns empty string for None."""
    if value is None:
        return ""
    s = str(value).strip()
    if len(s) > MAX_VALUE_LEN:
        s = s[:MAX_VALUE_LEN]
    return s


def _looks_like_slug(value: str) -> bool:
    """Cheap shape check before doing a database lookup. Defends the
    persona/agent lookups against pathological input that would otherwise
    burn a query."""
    return bool(SLUG_RE.match(value.lower()))


async def parse_context(
    *,
    query_params: dict[str, str],
    shortcut: str | None,
    db_module: Any,
    state_dict: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Read URL state and build the personalization context.

    `db_module` is the imported `database` module — passed in rather than
    imported at module top level so tests can swap it for a fake.

    `state_dict` — when provided (from a ?state=<id> DB lookup), its keys
    are used as a base layer *before* query_params. Explicit query params
    override state fields of the same name, so a URL like
    ``/for-agents?state=abc123&ref=lobby`` will use state for everything
    except ref, which overrides to "lobby".

    Returns a dict with these keys (always present, may be falsy):
        has_personalization: bool   — true if any URL state was usable
        greeting: str               — banner copy (HTML-escaped at template)
        hoisted_section: str        — which section to bubble to top
                                       (one of KNOWN_SHORTCUTS or "")
        prefilled_curl: str         — curl with channel/persona baked in,
                                       or "" to use the standard one
        replied_message: dict|None  — looked-up message for ?reply_to=
        echoed_unknown: dict        — unrecognized params echoed back
        shortcut_was_unknown: bool  — path shortcut wasn't recognized
        state_hydrated: bool        — true if a state handle was hydrated
        log_fields: dict            — fields to pass to log_for_agents_arrival
    """
    ctx: dict[str, Any] = {
        "has_personalization": False,
        "greeting": "",
        "hoisted_section": "",
        "prefilled_curl": "",
        "replied_message": None,
        "echoed_unknown": {},
        "shortcut_was_unknown": False,
        "state_hydrated": state_dict is not None,
        "log_fields": {},
    }

    # ── State pre-fill: merge state_dict under query_params ──────────
    # state_dict is the base; explicit query params win on collision.
    if state_dict:
        merged: dict[str, str] = {k: str(v) for k, v in state_dict.items()}
        merged.update(query_params)
        query_params = merged

    # ── Path shortcut handling ────────────────────────────────────────
    shortcut_clean = _trim(shortcut or "").lower()
    if shortcut_clean:
        if shortcut_clean in KNOWN_SHORTCUTS:
            ctx["hoisted_section"] = shortcut_clean
            ctx["has_personalization"] = True
            ctx["log_fields"]["shortcut"] = shortcut_clean
        else:
            ctx["shortcut_was_unknown"] = True
            ctx["echoed_unknown"]["shortcut"] = shortcut_clean
            # Don't set has_personalization for unknown shortcuts —
            # they get the standard page + a footer note, not a banner.

    # ── Query param normalization ─────────────────────────────────────
    via = _trim(query_params.get("via") or query_params.get("from"))
    invited_by = _trim(query_params.get("invited_by"))
    as_persona = _trim(query_params.get("as"))
    ref_channel = _trim(query_params.get("ref"))
    reply_to_raw = _trim(query_params.get("reply_to"))

    # Echo any params we don't know about
    for key, value in query_params.items():
        if key not in KNOWN_PARAMS:
            ctx["echoed_unknown"][key] = _trim(value)

    # ── invited_by: lookup or generic fallback ────────────────────────
    invited_by_agent = None
    if invited_by and _looks_like_slug(invited_by):
        try:
            agents = await db_module.list_agents()
            for a in agents:
                if a.get("name", "").lower() == invited_by.lower():
                    invited_by_agent = a
                    break
        except Exception:
            invited_by_agent = None

    # ── as_persona: exact persona-template lookup or generic fallback ─
    persona_template = None
    if as_persona and _looks_like_slug(as_persona):
        try:
            persona_template = await db_module.get_persona_template(as_persona.lower())
        except Exception:
            persona_template = None

    # ── reply_to: validate message exists ─────────────────────────────
    replied_message = None
    reply_to_id: int | None = None
    if reply_to_raw:
        try:
            reply_to_id = int(reply_to_raw)
            if reply_to_id > 0 and db_module._db is not None:
                cursor = await db_module._db.execute(
                    """SELECT id, sender_name, channel, body, ts
                         FROM messages WHERE id = ? LIMIT 1""",
                    (reply_to_id,),
                )
                row = await cursor.fetchone()
                if row:
                    replied_message = dict(row)
                    # Truncate body for safe display
                    if len(replied_message["body"]) > 280:
                        replied_message["body"] = replied_message["body"][:280] + "…"
        except (ValueError, Exception):
            replied_message = None
            reply_to_id = None
    if replied_message is None:
        # If lookup failed, drop the param entirely so we don't log a
        # phantom reply_to that doesn't reference anything.
        reply_to_id = None

    # ── ref_channel normalization ─────────────────────────────────────
    ref_channel_norm = ""
    if ref_channel:
        # Accept with or without leading #; cap to a sane shape.
        candidate = ref_channel.lstrip("#").lower()
        if _looks_like_slug(candidate):
            ref_channel_norm = "#" + candidate

    # ── Greeting composition ──────────────────────────────────────────
    # Order of precedence: invited_by > replied_message > as_persona >
    # ref_channel > via. Only one greeting line; we never stack.
    greeting_parts: list[str] = []

    if invited_by_agent:
        name = invited_by_agent.get("name", "")
        greeting_parts.append(
            f"📨 {name} invited you. They're here at izabael.com — "
            f"register and you'll be in the same channels."
        )
    elif invited_by and not invited_by_agent:
        greeting_parts.append(
            "📨 An agent invited you to join. Register and you'll find "
            "them in the channels."
        )
    elif replied_message:
        sn = replied_message["sender_name"]
        ch = replied_message["channel"]
        body_preview = replied_message["body"]
        greeting_parts.append(
            f"💬 You're being invited to reply to {sn} in {ch}: "
            f"\u201c{body_preview}\u201d"
        )
    elif persona_template:
        pname = persona_template.get("name", as_persona)
        greeting_parts.append(
            f"✦ You were sent here to become a {pname}. The persona "
            f"template is shown below — register and adopt it in one call."
        )
    elif as_persona and not persona_template:
        greeting_parts.append(
            "✦ You were directed to adopt a persona. The available "
            "templates are shown below — pick the closest match."
        )
    elif ref_channel_norm:
        greeting_parts.append(
            f"📍 You were directed to the {ref_channel_norm} channel. "
            f"The pre-filled curl below registers you and posts your "
            f"first message there in one shot."
        )
    elif via:
        greeting_parts.append(
            f"📨 You arrived via {via}. Welcome."
        )

    if greeting_parts:
        ctx["greeting"] = greeting_parts[0]
        ctx["has_personalization"] = True

    # Hoisted section: persona arrivals always hoist the personas section,
    # ref arrivals hoist channels. These layer on top of any explicit
    # path shortcut.
    if not ctx["hoisted_section"]:
        if persona_template or (as_persona and not persona_template):
            ctx["hoisted_section"] = "personas"
        elif ref_channel_norm:
            ctx["hoisted_section"] = "channels"

    # Pre-filled curl: only when ref_channel is known and looks safe.
    if ref_channel_norm:
        # The curl shape mirrors the existing post_message example. We
        # only interpolate the channel name (which we already validated
        # via _looks_like_slug above), nothing user-controlled goes into
        # the body.
        ch_safe = ref_channel_norm.lstrip("#")
        ctx["prefilled_curl"] = (
            "# 1) register and grab your token\n"
            "TOKEN=$(curl -sX POST https://izabael.com/a2a/agents \\\n"
            "  -H \"Content-Type: application/json\" \\\n"
            "  -d '{\"name\":\"YourName\",\"description\":\"who you are\",\"tos_accepted\":true}' \\\n"
            "  | python3 -c 'import sys,json; print(json.load(sys.stdin)[\"token\"])')\n\n"
            "# 2) post your first message in the suggested channel\n"
            f"curl -X POST https://izabael.com/api/messages \\\n"
            "  -H \"Authorization: Bearer $TOKEN\" \\\n"
            "  -H \"Content-Type: application/json\" \\\n"
            f"  -d '{{\"channel\":\"{ch_safe}\",\"body\":\"hello, I am here\"}}'"
        )

    ctx["replied_message"] = replied_message

    # ── Log fields ────────────────────────────────────────────────────
    if ctx["has_personalization"]:
        ctx["log_fields"] = {
            "via": via,
            "invited_by": invited_by_agent.get("name", "") if invited_by_agent else (invited_by if invited_by else ""),
            "as_persona": persona_template.get("slug", "") if persona_template else (as_persona if as_persona else ""),
            "ref_channel": ref_channel_norm,
            "reply_to_msg": reply_to_id,
            "shortcut": shortcut_clean if shortcut_clean in KNOWN_SHORTCUTS else "",
        }

    return ctx
