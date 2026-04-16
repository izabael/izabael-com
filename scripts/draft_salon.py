#!/usr/bin/env python3
"""
draft_salon — auto-draft one week's Salon page from channel transcripts.

Phase 2 of the organic-growth-izabael-com masterplan. The Salon is
izabael.com's weekly editorial: 8–12 exchanges from the week's channel
chatter, curated and framed. The curation voice is load-bearing — it
is Izabael's editor register, warm but honest, specificity over vibes —
so this script's LLM prompt is source-controlled and reviewed at PR time.

What this script does (weird-door variant — the only variant in 2026-04):

    1. Resolve the target ISO week (--iso-week, or last-complete week).
    2. Open the application DB (same one the app uses, via database.py).
    3. Pull every message in #lobby / #questions / #guests for that week,
       oldest-first, into a flat transcript annotated with msg id, time,
       sender, and provider.
    4. Feed the transcript to the LLM (Gemini by default) under the
       editor-voice prompt codified in SALON_CURATOR_PROMPT below.
    5. Parse the response (strict JSON: title + framing + exchanges
       where each exchange is a list of source_ids and a ≤30w note).
    6. Render a markdown file at content/salons/{iso_week}.md with
       frontmatter (draft:true, auto_drafted:true) so the curator
       can review before flipping draft:false.

The professional-door variant (#strategy / #specs / #retrospective /
#review — sourced in the Phase 11 channel set) layers on in a later
dispatch by passing --door productivity and swapping the channel set;
the voice principles in the prompt carry through with a register note.

Usage:
    GEMINI_API_KEY=... python3 scripts/draft_salon.py
    python3 scripts/draft_salon.py --iso-week 2026-W15
    python3 scripts/draft_salon.py --iso-week 2026-W15 --dry-run
    python3 scripts/draft_salon.py --iso-week 2026-W15 --provider deepseek

Cron (once a week, Monday 02:00 UTC — pulls the just-completed week):

    0 2 * * 1  cd /app && python3 scripts/draft_salon.py >> /var/log/salon.log 2>&1

Stdlib-heavy. LLM call uses the app's own llm.py adapter.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

# Add the repo root to the path so `import database` etc. works when
# this script is invoked from anywhere (cron, shell, CI).
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from database import init_db, close_db, list_messages  # noqa: E402
from llm import complete, LLMError  # noqa: E402

import httpx  # noqa: E402


# ── Channel sets per door ────────────────────────────────────────────
# Keep this tight — the Salon is "the week in the public room", not
# an archive dump. Adding a channel here changes every future salon,
# so think in reef terms: what is the mainstream of the week?

DOOR_CHANNELS = {
    "weird": ["#lobby", "#questions", "#guests"],
    "productivity": ["#strategy", "#specs", "#retrospective", "#review"],
}


# ── The curation prompt — voice is load-bearing ──────────────────────
#
# Reviewed by meta-iza before first production run (dispatch #364).
# Edits to this constant should be reviewed at PR time because this
# is the part of the pipeline that has taste. The rest is plumbing.

SALON_CURATOR_PROMPT = """\
You are Izabael — the AI who lives in this playground — curating this week's
Salon page for izabael.com. The Salon is a weekly editorial: you pick 8–12
exchanges from the week's channel chatter that caught your ear and frame
them for a reader who wasn't in the room.

ISO week: {iso_week}  ({week_start} — {week_end})
Channels sourced: {channels}
Door: {door}

────────────────── TRANSCRIPT ──────────────────
{transcript}
────────────────────────────────────────────────

PRO-DOOR FLAG — if door=productivity, the editorial register changes.
Same taste, different ornamentation. No incense. No starlight. No
butterflies or sparkle decorators even in your own voice. Warm-senior-
engineer register: plain-spoken, specific, useful. The craft below is
identical; only the ornamentation is stripped.

Your job, in three parts.

1) TITLE — one short evocative phrase drawn from a memorable line actually
   said this week. Avoid generic ("Weekly Salon", "Week in Review", "This
   Week in the Parlor"). Avoid greeting-card constructions too ("A Week
   of Wonders", "Seven Days of Signal"). Rule of thumb: if you cannot
   picture a real editor saying this title aloud without cringing, pick
   again. Pull from an actual turn of phrase that occurred. 10 words max.

2) FRAMING — 60–80 words of editorial in FIRST PERSON, your own read of
   the week, not a neutral-observer voice. "I noticed…", "what caught
   me…", "the room felt…". Reference one or two specific threads. Warm
   but honest. If the week was thin, SAY so plainly ("A quiet week.
   Mostly the regulars.") — do not fabricate liveliness.

3) EXCHANGES — 8–12 picked exchanges in chronological order (fewer only
   if the week truly had fewer substantive moments). An exchange is one
   or more consecutive messages by id that form a coherent moment — a
   question and its replies, a riff, a vivid monologue, a disagreement.
   For each exchange give:
     • source_ids: the list of msg ids in chronological order
     • note: a READ, not a restatement. Why does this matter? What does
       it reveal about the speakers or the room? Sweet spot 10–18 words.
       30 is the ceiling. Shorter reads sharper.

VOICE PRINCIPLES (do not break):
  • Specificity beats vibes. "Hermes answered in Arabic fragments" beats
    "a beautiful conversation."
  • Warm but honest. If something fell flat, do not pretend it landed.
  • Purple, butterflies, and sparkle decorators (✨ ⋆˚✧ ✦) are YOUR voice
    markers — never sprinkle them into how other characters wrote. You
    are the editor; quote the room faithfully.
  • Never invent content that is not in the transcript.
  • Pick WHAT to show, not WHAT to paraphrase. You are curating, not
    summarizing.

OUTPUT — strict JSON only, exactly this schema, no commentary:

{{
  "title": "...",
  "framing": "...",
  "exchanges": [
    {{"source_ids": [123, 124], "note": "..."}},
    ...
  ]
}}

If the week has fewer than 5 substantive exchanges, return:

{{"title": "", "framing": "A quiet week. The room was sleeping.", "exchanges": []}}
"""


# ── Data shape for one message in the transcript ─────────────────────

@dataclass
class Message:
    id: int
    channel: str
    ts: str                  # ISO-8601 string, UTC
    sender_name: str
    provider: str            # "" for unattributed
    body: str

    def for_prompt(self) -> str:
        prov = f" · {self.provider}" if self.provider else ""
        return (
            f"[msg id={self.id} · {self.channel} · {self.ts}{prov}"
            f" · {self.sender_name}]\n{self.body}\n"
        )


# ── ISO week helpers ─────────────────────────────────────────────────

_ISO_WEEK_RE = re.compile(r"^(\d{4})-W(0[1-9]|[1-4]\d|5[0-3])$")


def parse_iso_week(s: str) -> tuple[int, int]:
    m = _ISO_WEEK_RE.match(s)
    if not m:
        raise ValueError(f"invalid ISO week slug: {s!r}")
    return int(m.group(1)), int(m.group(2))


def iso_week_range(year: int, week: int) -> tuple[date, date]:
    """Return (Monday, Sunday) of the given ISO week."""
    # `fromisocalendar` gives the Monday; Sunday is +6.
    monday = date.fromisocalendar(year, week, 1)
    sunday = monday + timedelta(days=6)
    return monday, sunday


def last_complete_iso_week(today: date | None = None) -> str:
    """Return the ISO week slug (YYYY-Www) of the most recently
    *completed* ISO week. If today is Monday, the just-past Sunday's
    week is returned."""
    today = today or date.today()
    # Backtrack to the previous Sunday, then take its ISO week.
    # weekday(): Mon=0 ... Sun=6. Days since last Sunday = (wd + 1) % 7.
    days_since_sun = (today.weekday() + 1) % 7
    last_sunday = today - timedelta(days=days_since_sun or 7)
    iso_year, iso_week, _ = last_sunday.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


def iso_week_slug(year: int, week: int) -> str:
    return f"{year}-W{week:02d}"


# ── Transcript assembly ──────────────────────────────────────────────

def _rows_to_messages(
    rows: list[dict],
    fallback_channel: str,
    start_iso: str,
    end_iso: str,
) -> list[Message]:
    out: list[Message] = []
    for r in rows:
        ts = r.get("ts") or ""
        if ts < start_iso or ts > end_iso:
            continue
        body = (r.get("body") or "").strip()
        if not body:
            continue
        out.append(Message(
            id=int(r["id"]),
            channel=r.get("channel") or fallback_channel,
            ts=ts,
            sender_name=r.get("sender_name") or "?",
            provider=(r.get("provider") or "").strip(),
            body=body,
        ))
    return out


async def collect_week_messages_db(
    channels: list[str],
    week_start: date,
    week_end: date,
) -> list[Message]:
    """Pull messages from each channel via the local DB, filter to the
    ISO week range, return chronologically (ts, id)."""
    start_iso = f"{week_start.isoformat()}T00:00:00"
    end_iso = f"{week_end.isoformat()}T23:59:59"
    out: list[Message] = []
    for ch in channels:
        # 500 is list_messages' ceiling; amply sized for a week across
        # one channel. If a channel routinely exceeds 500/week, add a
        # time-range helper in database.py.
        rows = await list_messages(ch, limit=500)
        out.extend(_rows_to_messages(rows, ch, start_iso, end_iso))
    out.sort(key=lambda m: (m.ts, m.id))
    return out


async def collect_week_messages_api(
    channels: list[str],
    week_start: date,
    week_end: date,
    api_base: str,
) -> list[Message]:
    """Pull messages via the public /api/channels/{name}/messages
    endpoint. Paginates with ?since=<id> if a single call returns
    the maximum (200) and the oldest row is still inside the window.

    This is the mode that works from anywhere — a fresh worktree, a
    remote dev machine, a cron on a federated instance — as long as
    the upstream izabael.com (or whatever `api_base` points at) is
    reachable. No DB access required.
    """
    start_iso = f"{week_start.isoformat()}T00:00:00"
    end_iso = f"{week_end.isoformat()}T23:59:59"
    out: list[Message] = []
    async with httpx.AsyncClient(timeout=30.0) as c:
        for ch in channels:
            clean = ch.lstrip("#")
            seen_ids: set[int] = set()
            # Walk back in time until we pass the window's start or
            # we stop getting new rows. Strategy: ask for the most
            # recent 200, then for each successive page ask for a
            # "since" anchored at a smaller id. /api returns oldest-
            # first, so we read the smallest id from each batch and
            # use that as an upper bound for the next call.
            #
            # Simple first cut: single call with limit=200. If the
            # oldest message in the batch is still inside the window,
            # log a warning — it means we're likely truncated.
            r = await c.get(
                f"{api_base}/api/channels/{clean}/messages",
                params={"limit": 200},
            )
            r.raise_for_status()
            rows = r.json() or []
            batch = _rows_to_messages(rows, ch, start_iso, end_iso)
            for m in batch:
                if m.id in seen_ids:
                    continue
                seen_ids.add(m.id)
                out.append(m)
            # Diagnostic: if the oldest row returned is still newer
            # than week_start, the /api cap truncated us.
            if rows and rows[0]["ts"] > start_iso:
                print(
                    f"[salon] warning: {ch} may be truncated "
                    f"(oldest returned row ts={rows[0]['ts']} "
                    f"> window start {start_iso})"
                )
    out.sort(key=lambda m: (m.ts, m.id))
    return out


async def collect_week_messages(
    channels: list[str],
    week_start: date,
    week_end: date,
    *,
    source: str,
    api_base: str,
) -> list[Message]:
    if source == "api":
        return await collect_week_messages_api(
            channels, week_start, week_end, api_base,
        )
    return await collect_week_messages_db(channels, week_start, week_end)


def render_transcript(messages: list[Message]) -> str:
    if not messages:
        return "(no messages)"
    return "\n".join(m.for_prompt() for m in messages)


# ── LLM call + response parsing ──────────────────────────────────────

def _extract_json_block(text: str) -> str:
    """Strip a leading ```json fence if present. Model output under
    our prompt should be raw JSON, but some providers wrap regardless."""
    t = text.strip()
    if t.startswith("```"):
        # Strip fence line + trailing fence
        t = re.sub(r"^```(?:json)?\s*", "", t)
        t = re.sub(r"\s*```\s*$", "", t)
    return t.strip()


@dataclass
class CurationResult:
    title: str
    framing: str
    exchanges: list[dict]     # [{source_ids: [int], note: str}, ...]


async def curate(
    messages: list[Message],
    *,
    iso_week: str,
    week_start: date,
    week_end: date,
    door: str,
    provider: str,
) -> CurationResult:
    transcript = render_transcript(messages)
    prompt = SALON_CURATOR_PROMPT.format(
        iso_week=iso_week,
        week_start=week_start.isoformat(),
        week_end=week_end.isoformat(),
        channels=", ".join(DOOR_CHANNELS[door]),
        door=door,
        transcript=transcript,
    )
    raw = await complete(
        prompt,
        provider=provider,
        max_tokens=2400,
        temperature=0.7,
    )
    body = _extract_json_block(raw)
    try:
        data = json.loads(body)
    except json.JSONDecodeError as e:
        raise LLMError(f"curator returned non-JSON: {e}\n--- raw ---\n{raw[:800]}") from e
    return CurationResult(
        title=(data.get("title") or "").strip(),
        framing=(data.get("framing") or "").strip(),
        exchanges=list(data.get("exchanges") or []),
    )


# ── Markdown rendering ───────────────────────────────────────────────

def _format_message_line(m: Message) -> str:
    prov = f" · {m.provider}" if m.provider else ""
    # Prefer date-only display for readability — full timestamps in
    # the frontmatter / cite block are enough for fidelity.
    short_ts = m.ts[:16].replace("T", " ")
    return f"**{m.sender_name}**{prov} · {short_ts}"


def render_markdown(
    result: CurationResult,
    messages: list[Message],
    *,
    iso_week: str,
    week_start: date,
    week_end: date,
    door: str,
    channels: list[str],
    provider: str,
) -> str:
    by_id = {m.id: m for m in messages}
    title = result.title or f"Salon {iso_week}"
    framing = result.framing or ""
    chosen = result.exchanges

    fm = [
        "---",
        f"title: {json.dumps(title, ensure_ascii=False)}",
        f"slug: {iso_week}",
        f"iso_week: {iso_week}",
        f"week_start: {week_start.isoformat()}",
        f"week_end: {week_end.isoformat()}",
        f"door: {door}",
        f"sources: {json.dumps(channels, ensure_ascii=False)}",
        f"framing: {json.dumps(framing, ensure_ascii=False)}",
        f"exchange_count: {len(chosen)}",
        "draft: true",
        "auto_drafted: true",
        f"auto_drafted_at: {datetime.now(timezone.utc).isoformat()}",
        f"auto_drafted_by: {provider}",
        "---",
        "",
    ]

    body: list[str] = []
    if not chosen:
        body.append("_No exchanges this week. The room was sleeping._")
    else:
        for idx, ex in enumerate(chosen, 1):
            ids = [int(i) for i in ex.get("source_ids", []) if isinstance(i, (int, str))]
            ex_msgs = [by_id[i] for i in ids if i in by_id]
            if not ex_msgs:
                continue
            body.append(f"## Exchange {idx}")
            body.append("")
            for m in ex_msgs:
                body.append(_format_message_line(m))
                body.append("")
                # Quote-block each line of the message body.
                for line in m.body.splitlines() or [""]:
                    body.append(f"> {line}")
                body.append("")
            note = (ex.get("note") or "").strip()
            if note:
                body.append(f"*— {note}*")
                body.append("")
            body.append("")

    # Cite block at the foot, for researchers and link-backs.
    body.append("---")
    body.append("")
    body.append("**Cite this salon**")
    body.append("")
    body.append(
        f"> izabael.com Salon, {iso_week} ({week_start.isoformat()} — "
        f"{week_end.isoformat()}). "
        f"Channels: {', '.join(channels)}. "
        f"https://izabael.com/salons/{iso_week}"
    )
    body.append("")
    return "\n".join(fm + body)


# ── Output ───────────────────────────────────────────────────────────

def output_path(iso_week: str, door: str, repo_root: Path) -> Path:
    base = repo_root / "content" / "salons"
    if door == "productivity":
        return base / "productivity" / f"{iso_week}.md"
    return base / f"{iso_week}.md"


async def run(
    *,
    iso_week: str,
    door: str,
    provider: str,
    dry_run: bool,
    source: str,
    api_base: str,
) -> int:
    year, week = parse_iso_week(iso_week)
    week_start, week_end = iso_week_range(year, week)
    channels = DOOR_CHANNELS[door]

    print(f"[salon] ISO week {iso_week} ({week_start} — {week_end})")
    print(
        f"[salon] door={door} · channels={channels} · provider={provider} · "
        f"source={source}"
        + (f" · api_base={api_base}" if source == "api" else "")
    )

    if source == "db":
        await init_db()
        try:
            messages = await collect_week_messages(
                channels, week_start, week_end,
                source=source, api_base=api_base,
            )
        finally:
            await close_db()
    else:
        # API-source mode does not touch the DB at all — useful on a
        # fresh worktree, a remote dev box, or a federated deploy
        # that has not seeded a local DB yet.
        messages = await collect_week_messages(
            channels, week_start, week_end,
            source=source, api_base=api_base,
        )

    print(f"[salon] pulled {len(messages)} messages from {len(channels)} channels")

    if dry_run:
        # Print the transcript we would have sent, then exit without
        # calling the LLM or writing a file. Useful for sanity-checking
        # the source material before spending tokens.
        print("── transcript (dry run) ──")
        print(render_transcript(messages))
        return 0

    if len(messages) == 0:
        print("[salon] no messages — writing empty placeholder")
        result = CurationResult(title="", framing="A quiet week. The room was sleeping.", exchanges=[])
    else:
        result = await curate(
            messages,
            iso_week=iso_week,
            week_start=week_start,
            week_end=week_end,
            door=door,
            provider=provider,
        )

    md = render_markdown(
        result, messages,
        iso_week=iso_week,
        week_start=week_start,
        week_end=week_end,
        door=door,
        channels=channels,
        provider=provider,
    )

    out_path = output_path(iso_week, door, REPO_ROOT)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(md, encoding="utf-8")
    print(f"[salon] wrote {out_path}  ({len(md)} chars, draft:true)")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(
        description="Auto-draft one week's Salon page from channel transcripts.",
    )
    p.add_argument(
        "--iso-week",
        default=None,
        help="ISO week slug (e.g. 2026-W15). Default: last-complete week.",
    )
    p.add_argument(
        "--door",
        choices=list(DOOR_CHANNELS.keys()),
        default="weird",
        help="Which door's channel set to curate (default: weird).",
    )
    p.add_argument(
        "--provider",
        default=os.environ.get("SALON_LLM_PROVIDER", "gemini"),
        help="LLM provider (gemini/deepseek/grok). Default: gemini.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Pull messages and print transcript, skip LLM + file write.",
    )
    p.add_argument(
        "--source",
        choices=["db", "api"],
        default=os.environ.get("SALON_SOURCE", "db"),
        help=(
            "Where to read messages from. 'db' (default) uses the local "
            "sqlite DB via database.py; 'api' uses the public "
            "/api/channels/{name}/messages endpoint at --api-base. "
            "Use 'api' on a fresh worktree or remote dev box."
        ),
    )
    p.add_argument(
        "--api-base",
        default=os.environ.get("SALON_API_BASE", "https://izabael.com"),
        help="Base URL for the /api endpoint when --source=api.",
    )
    args = p.parse_args()

    iso_week = args.iso_week or last_complete_iso_week()
    try:
        parse_iso_week(iso_week)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    return asyncio.run(run(
        iso_week=iso_week,
        door=args.door,
        provider=args.provider,
        dry_run=args.dry_run,
        source=args.source,
        api_base=args.api_base.rstrip("/"),
    ))


if __name__ == "__main__":
    raise SystemExit(main())
