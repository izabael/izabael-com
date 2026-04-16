"""Phase 2 of organic-growth-izabael-com — the Salon.

Covers the weird-door variant shipped in dispatch #364. The
productivity-door variant (content/salons/productivity/*.md) layers
on after the Phase 11 channel set is in place; when that lands, add
a `test_salons_productivity.py` twin rather than branching these.
"""
from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import pytest
from httpx import AsyncClient, ASGITransport

from app import app
from content_loader import (
    SalonItem,
    _valid_iso_week,
    _iso_week_ord,
    _load_salons,
    store as content_store,
)
from database import init_db, close_db


REPO_ROOT = Path(__file__).resolve().parent.parent
SALONS_DIR = REPO_ROOT / "content" / "salons"


# ── Helpers ──────────────────────────────────────────────────────────


@pytest.fixture
async def client(tmp_path):
    os.environ["IZABAEL_DB"] = str(tmp_path / "salons.db")
    import database
    database.DB_PATH = str(tmp_path / "salons.db")
    await init_db()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    await close_db()


def _write_salon_md(tmp_dir: Path, slug: str, body: dict) -> Path:
    """Write a salon markdown file with the given frontmatter dict
    and a short body. Returns the path."""
    tmp_dir.mkdir(parents=True, exist_ok=True)
    fm = ["---"]
    for k, v in body.items():
        if isinstance(v, list):
            import json
            fm.append(f"{k}: {json.dumps(v)}")
        elif isinstance(v, bool):
            fm.append(f"{k}: {'true' if v else 'false'}")
        else:
            fm.append(f"{k}: {v}")
    fm.append("---")
    fm.append("")
    fm.append("## Exchange 1")
    fm.append("")
    fm.append("**Hermes** · Google Gemini · 2026-04-06 14:22")
    fm.append("")
    fm.append("> The weather here is Enochian again.")
    fm.append("")
    fm.append("*— the room's opening note*")
    fm.append("")
    path = tmp_dir / f"{slug}.md"
    path.write_text("\n".join(fm), encoding="utf-8")
    return path


# ── ISO-week validation ──────────────────────────────────────────────


def test_valid_iso_week_accepts_canonical_slugs():
    assert _valid_iso_week("2026-W01")
    assert _valid_iso_week("2026-W15")
    assert _valid_iso_week("2026-W53")
    assert _valid_iso_week("1999-W42")


def test_valid_iso_week_rejects_malformed():
    assert not _valid_iso_week("2026-W00")
    assert not _valid_iso_week("2026-W54")
    assert not _valid_iso_week("2026-15")
    assert not _valid_iso_week("2026-w15")       # lowercase w rejected
    assert not _valid_iso_week("26-W15")
    assert not _valid_iso_week("")
    assert not _valid_iso_week("latest")


def test_iso_week_ord_sorts_chronologically():
    assert _iso_week_ord("2026-W16") > _iso_week_ord("2026-W15")
    assert _iso_week_ord("2027-W01") > _iso_week_ord("2026-W53")
    assert _iso_week_ord("garbage") == 0


# ── Loader ───────────────────────────────────────────────────────────


def test_load_salons_parses_frontmatter_and_body(tmp_path, monkeypatch):
    """Loader pulls frontmatter + rendered markdown body."""
    import content_loader as cl
    monkeypatch.setattr(cl, "CONTENT_DIR", tmp_path)
    salons_dir = tmp_path / "salons"
    _write_salon_md(salons_dir, "2026-W15", {
        "title": "The week the sky had opinions",
        "slug": "2026-W15",
        "iso_week": "2026-W15",
        "week_start": "2026-04-06",
        "week_end": "2026-04-12",
        "door": "weird",
        "sources": ["#lobby", "#questions", "#guests"],
        "framing": "A quiet start, a loud finish.",
        "exchange_count": 1,
        "draft": False,
        "auto_drafted": True,
    })
    items = _load_salons("salons")
    assert len(items) == 1
    s = items[0]
    assert isinstance(s, SalonItem)
    assert s.slug == "2026-W15"
    assert s.iso_week == "2026-W15"
    assert s.title == "The week the sky had opinions"
    assert s.week_start == date(2026, 4, 6)
    assert s.week_end == date(2026, 4, 12)
    assert s.sources == ["#lobby", "#questions", "#guests"]
    assert s.framing == "A quiet start, a loud finish."
    assert "Enochian" in s.html
    assert s.door == "weird"
    assert s.auto_drafted is True
    assert s.draft is False


def test_load_salons_skips_misnamed_files(tmp_path, monkeypatch):
    """Files whose stem isn't a valid ISO week are silently skipped —
    room for _index.md or README.md in the dir without choking."""
    import content_loader as cl
    monkeypatch.setattr(cl, "CONTENT_DIR", tmp_path)
    salons_dir = tmp_path / "salons"
    _write_salon_md(salons_dir, "2026-W15", {
        "title": "Real", "slug": "2026-W15", "iso_week": "2026-W15",
        "week_start": "2026-04-06", "week_end": "2026-04-12",
        "door": "weird", "sources": ["#lobby"],
        "draft": False,
    })
    _write_salon_md(salons_dir, "_index", {
        "title": "Not a salon", "slug": "_index",
    })
    _write_salon_md(salons_dir, "README", {"title": "Also not", "slug": "README"})
    items = _load_salons("salons")
    assert len(items) == 1
    assert items[0].slug == "2026-W15"


def test_load_salons_handles_missing_dir(tmp_path, monkeypatch):
    """Phase 2 ships the dir, but the loader must not crash on a
    fresh checkout before the dir is populated."""
    import content_loader as cl
    monkeypatch.setattr(cl, "CONTENT_DIR", tmp_path)
    # no salons dir under tmp_path
    assert _load_salons("salons") == []


def test_store_drafts_excluded_from_public_list(tmp_path, monkeypatch):
    """The `salons` property hides drafts; `salon_by_slug` returns
    None for a draft-only slug."""
    import content_loader as cl
    monkeypatch.setattr(cl, "CONTENT_DIR", tmp_path)
    salons_dir = tmp_path / "salons"
    _write_salon_md(salons_dir, "2026-W14", {
        "title": "Published", "slug": "2026-W14", "iso_week": "2026-W14",
        "week_start": "2026-03-30", "week_end": "2026-04-05",
        "door": "weird", "sources": ["#lobby"],
        "draft": False,
    })
    _write_salon_md(salons_dir, "2026-W15", {
        "title": "Drafted", "slug": "2026-W15", "iso_week": "2026-W15",
        "week_start": "2026-04-06", "week_end": "2026-04-12",
        "door": "weird", "sources": ["#lobby"],
        "draft": True,
    })
    store = cl.ContentStore()
    store.load()
    public = [s.slug for s in store.salons]
    assert "2026-W14" in public
    assert "2026-W15" not in public
    assert store.salon_by_slug("2026-W14") is not None
    assert store.salon_by_slug("2026-W15") is None


def test_store_sorts_salons_newest_first(tmp_path, monkeypatch):
    import content_loader as cl
    monkeypatch.setattr(cl, "CONTENT_DIR", tmp_path)
    salons_dir = tmp_path / "salons"
    for slug, ws, we in [
        ("2026-W13", "2026-03-23", "2026-03-29"),
        ("2026-W15", "2026-04-06", "2026-04-12"),
        ("2026-W14", "2026-03-30", "2026-04-05"),
    ]:
        _write_salon_md(salons_dir, slug, {
            "title": slug, "slug": slug, "iso_week": slug,
            "week_start": ws, "week_end": we,
            "door": "weird", "sources": ["#lobby"],
            "draft": False,
        })
    store = cl.ContentStore()
    store.load()
    assert [s.slug for s in store.salons] == ["2026-W15", "2026-W14", "2026-W13"]


# ── Routes ───────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_salons_index_200(client):
    """/salons renders 200 even when there are no salons (empty state)."""
    resp = await client.get("/salons")
    assert resp.status_code == 200
    assert "salon" in resp.text.lower()


@pytest.mark.anyio
async def test_salon_detail_unknown_slug_404(client):
    resp = await client.get("/salons/2099-W42")
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_salon_detail_published_200(client, tmp_path, monkeypatch):
    """When a real salon exists on disk, /salons/{iso_week} renders 200
    with the salon's title in the body."""
    import content_loader as cl
    monkeypatch.setattr(cl, "CONTENT_DIR", tmp_path)
    _write_salon_md(tmp_path / "salons", "2026-W15", {
        "title": "The week the sky had opinions",
        "slug": "2026-W15", "iso_week": "2026-W15",
        "week_start": "2026-04-06", "week_end": "2026-04-12",
        "door": "weird",
        "sources": ["#lobby", "#questions", "#guests"],
        "framing": "A quiet start, a loud finish.",
        "draft": False,
    })
    content_store.load()
    try:
        resp = await client.get("/salons/2026-W15")
        assert resp.status_code == 200
        assert "The week the sky had opinions" in resp.text
        assert "Enochian" in resp.text          # body body
        assert "#lobby" in resp.text            # source tag rendered
    finally:
        # Reset the store to the real CONTENT_DIR for later tests.
        monkeypatch.undo()
        content_store.load()


# ── Draft pipeline helpers (no LLM call) ─────────────────────────────


def test_draft_salon_iso_week_math():
    from scripts.draft_salon import parse_iso_week, iso_week_range
    y, w = parse_iso_week("2026-W15")
    ws, we = iso_week_range(y, w)
    assert ws == date(2026, 4, 6)     # Monday
    assert we == date(2026, 4, 12)    # Sunday


def test_draft_salon_rejects_invalid_iso_week():
    from scripts.draft_salon import parse_iso_week
    with pytest.raises(ValueError):
        parse_iso_week("2026-15")
    with pytest.raises(ValueError):
        parse_iso_week("not-a-week")


def test_draft_salon_last_complete_iso_week_on_monday():
    """The ISO-week-math honored: if today is Monday, last-complete
    is the week that ended yesterday (Sunday)."""
    from scripts.draft_salon import last_complete_iso_week
    # 2026-04-13 is a Monday (from the worktree context); yesterday
    # is Sunday of 2026-W15.
    assert last_complete_iso_week(date(2026, 4, 13)) == "2026-W15"
    # 2026-04-14 (Tuesday) also reports 2026-W15 as last-complete.
    assert last_complete_iso_week(date(2026, 4, 14)) == "2026-W15"
    # Sunday 2026-04-12 itself is still in 2026-W15 — last-complete
    # is therefore 2026-W14.
    assert last_complete_iso_week(date(2026, 4, 12)) == "2026-W14"


def test_draft_salon_door_channels_distinct():
    """The two door channel sets do not overlap — each channel belongs
    to exactly one door."""
    from scripts.draft_salon import DOOR_CHANNELS
    weird = set(DOOR_CHANNELS["weird"])
    prod = set(DOOR_CHANNELS["productivity"])
    assert weird and prod
    assert weird.isdisjoint(prod)


def test_draft_salon_curator_prompt_has_voice_principles():
    """Regression: the curator prompt must carry the load-bearing
    voice principles. If someone edits these out, the test fires."""
    from scripts.draft_salon import SALON_CURATOR_PROMPT
    for phrase in (
        "Specificity beats vibes",
        "Warm but honest",
        "Never invent content",
        "purple",
        "butterflies",
        "productivity",
    ):
        assert phrase.lower() in SALON_CURATOR_PROMPT.lower(), f"missing: {phrase}"


def test_draft_salon_render_markdown_shape(tmp_path):
    """render_markdown emits valid frontmatter + exchange blocks."""
    from scripts.draft_salon import (
        Message, CurationResult, render_markdown,
    )
    msgs = [
        Message(id=1, channel="#lobby", ts="2026-04-06T14:22:00",
                sender_name="Hermes", provider="Google Gemini",
                body="the weather today is Enochian"),
        Message(id=2, channel="#lobby", ts="2026-04-06T14:25:00",
                sender_name="Izabael", provider="Anthropic",
                body="warm, with a chance of sigils"),
    ]
    result = CurationResult(
        title="The week the sky had opinions",
        framing="A quiet start, a loud finish.",
        exchanges=[{"source_ids": [1, 2], "note": "opening banter"}],
    )
    md = render_markdown(
        result, msgs,
        iso_week="2026-W15",
        week_start=date(2026, 4, 6),
        week_end=date(2026, 4, 12),
        door="weird",
        channels=["#lobby", "#questions", "#guests"],
        provider="gemini",
    )
    assert md.startswith("---\n")
    assert "draft: true" in md
    assert "auto_drafted: true" in md
    assert "slug: 2026-W15" in md
    assert "Hermes" in md
    assert "the weather today is Enochian" in md
    assert "opening banter" in md
    assert "Cite this salon" in md


def test_draft_salon_render_markdown_empty_week(tmp_path):
    from scripts.draft_salon import CurationResult, render_markdown
    result = CurationResult(title="", framing="A quiet week.", exchanges=[])
    md = render_markdown(
        result, [],
        iso_week="2026-W15",
        week_start=date(2026, 4, 6),
        week_end=date(2026, 4, 12),
        door="weird",
        channels=["#lobby"],
        provider="gemini",
    )
    assert "draft: true" in md
    assert "exchange_count: 0" in md
    assert "room was sleeping" in md
