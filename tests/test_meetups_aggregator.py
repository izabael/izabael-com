"""Phase 5 tests — /meetups aggregator page + /meetups.rss feed.

Covers:
    · Empty state: clean DB renders a friendly empty page + 0 count
    · Grouping: N notes across 3 attractions → N rows in 3 groups
    · Sort order: groups ordered by each group's next meetup (soonest
      upcoming attraction first), notes within groups sorted ASC by
      when_iso
    · Visibility filter: hidden notes (moderation queue) never surface
      in /meetups or the RSS feed
    · RSS: valid RSS 2.0 XML, one <item> per visible note, content-type
      application/rss+xml, title/link/description fields present
    · /attractions index still renders meetup-count pills with live
      counts (Phase 2 loop closed, Phase 5 polish only)
"""

from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient, ASGITransport

from app import app
from content_loader import store as content_store
from database import (
    close_db,
    create_meetup_note,
    init_db,
    update_meetup_note_verdict,
)


@pytest.fixture(autouse=True)
def _load_content():
    content_store.load()


@pytest.fixture(autouse=True)
async def _init_test_db(tmp_path):
    os.environ["IZABAEL_DB"] = str(tmp_path / "agg.db")
    import database
    database.DB_PATH = str(tmp_path / "agg.db")
    await init_db()
    try:
        from app import limiter
        limiter.reset()
    except Exception:
        pass
    yield
    await close_db()


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _hours_from_now(h: float) -> str:
    return (
        datetime.now(timezone.utc) + timedelta(hours=h)
    ).isoformat(timespec="seconds").replace("+00:00", "Z")


async def _seed_clean_note(
    slug: str,
    *,
    title: str,
    goal: str = "test goal",
    hours: float = 24,
    author: str = "Marlowe",
) -> str:
    """Insert a note that starts as 'clean' + visible (Phase 3
    normally stamps visibility post-insert via the spam filter; the
    aggregator tests bypass that path by flipping the verdict
    directly)."""
    note_id = await create_meetup_note(
        attraction_slug=slug,
        author_kind="human",
        author_label=author,
        title=title,
        goal=goal,
        when_iso=_hours_from_now(hours),
        when_text=f"in {int(hours)}h",
    )
    await update_meetup_note_verdict(
        note_id, verdict="clean", score=0.95, is_visible=True,
    )
    return note_id


# ── Empty state ──────────────────────────────────────────────────

@pytest.mark.anyio
async def test_meetups_empty_state_renders(client):
    resp = await client.get("/meetups")
    assert resp.status_code == 200
    body = resp.text
    assert "No meetups pinned yet" in body
    assert "/attractions" in body
    assert "meetups-empty" in body


@pytest.mark.anyio
async def test_meetups_empty_total_count_is_zero(client):
    resp = await client.get("/meetups")
    assert resp.status_code == 200
    # Count line shows <em>No meetups pinned yet.</em> when total==0
    assert "<em>No meetups pinned yet." in resp.text


# ── Populated / grouping + sort ──────────────────────────────────

@pytest.mark.anyio
async def test_meetups_groups_by_attraction(client):
    await _seed_clean_note("parlor",  title="Parlor tonight",  hours=3)
    await _seed_clean_note("parlor",  title="Parlor next week", hours=72)
    await _seed_clean_note("sphere",  title="Strategy sync",    hours=8)
    await _seed_clean_note("lexicon", title="Brevis fork jam",  hours=48)

    resp = await client.get("/meetups")
    assert resp.status_code == 200
    body = resp.text
    # Every seeded title must appear on the page.
    for t in ("Parlor tonight", "Parlor next week",
              "Strategy sync", "Brevis fork jam"):
        assert t in body
    # Three attraction groups surface.
    assert body.count('class="meetups-group ') == 3
    # Total count line says 4.
    assert "<strong>4</strong>" in body


@pytest.mark.anyio
async def test_meetups_group_order_soonest_first(client):
    """The group whose next meetup is soonest should be rendered
    first on the page. parlor/3h should beat sphere/8h, which should
    beat lexicon/48h."""
    await _seed_clean_note("parlor",  title="Parlor tonight", hours=3)
    await _seed_clean_note("sphere",  title="Strategy sync",   hours=8)
    await _seed_clean_note("lexicon", title="Brevis fork jam", hours=48)

    resp = await client.get("/meetups")
    body = resp.text
    idx_parlor  = body.find("Parlor tonight")
    idx_sphere  = body.find("Strategy sync")
    idx_lexicon = body.find("Brevis fork jam")
    assert idx_parlor < idx_sphere < idx_lexicon, (
        f"wrong order: parlor={idx_parlor} sphere={idx_sphere} "
        f"lexicon={idx_lexicon}"
    )


@pytest.mark.anyio
async def test_meetups_sorts_notes_within_group(client):
    """Within a group, notes come out sooner-first by when_iso."""
    await _seed_clean_note("parlor", title="Parlor later",   hours=72)
    await _seed_clean_note("parlor", title="Parlor tonight", hours=3)
    await _seed_clean_note("parlor", title="Parlor tomorrow", hours=30)

    resp = await client.get("/meetups")
    body = resp.text
    idx_tonight  = body.find("Parlor tonight")
    idx_tomorrow = body.find("Parlor tomorrow")
    idx_later    = body.find("Parlor later")
    assert idx_tonight < idx_tomorrow < idx_later


@pytest.mark.anyio
async def test_meetups_filters_hidden_notes(client):
    """Notes in the moderation queue (is_visible=0) must not surface."""
    visible_id = await _seed_clean_note(
        "parlor", title="Visible note", hours=12,
    )
    hidden_id = await create_meetup_note(
        attraction_slug="parlor",
        author_kind="human",
        author_label="Attacker",
        title="Hidden spam note",
        goal="http://spam.example.com",
        when_iso=_hours_from_now(6),
        when_text="in 6h",
    )
    await update_meetup_note_verdict(
        hidden_id, verdict="flagged", score=0.2, is_visible=False,
    )

    resp = await client.get("/meetups")
    body = resp.text
    assert "Visible note" in body
    assert "Hidden spam note" not in body
    assert "http://spam.example.com" not in body


# ── RSS feed ─────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_rss_feed_validates_as_rss_2(client):
    await _seed_clean_note("parlor",  title="Parlor tonight", hours=3)
    await _seed_clean_note("sphere",  title="Strategy sync",  hours=8)

    resp = await client.get("/meetups.rss")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/rss+xml")

    root = ET.fromstring(resp.text)
    assert root.tag == "rss"
    assert root.attrib.get("version") == "2.0"
    channel = root.find("channel")
    assert channel is not None
    assert channel.findtext("title") == "Izabael's AI Playground — Meetups"
    assert channel.findtext("link") == "https://izabael.com/meetups"

    items = channel.findall("item")
    assert len(items) == 2
    titles = [it.findtext("title") or "" for it in items]
    assert any("Parlor tonight" in t for t in titles)
    assert any("Strategy sync" in t for t in titles)


@pytest.mark.anyio
async def test_rss_feed_filters_hidden_notes(client):
    """The RSS feed MUST share the same visibility filter as the
    HTML aggregator — a flagged note in moderation should never
    leak to subscribers."""
    await _seed_clean_note("parlor", title="Visible", hours=4)
    hidden_id = await create_meetup_note(
        attraction_slug="parlor",
        author_kind="human",
        author_label="X",
        title="Hidden",
        goal="g",
        when_iso=_hours_from_now(5),
        when_text="in 5h",
    )
    await update_meetup_note_verdict(
        hidden_id, verdict="unverified", score=0.0, is_visible=False,
    )

    resp = await client.get("/meetups.rss")
    assert resp.status_code == 200
    root = ET.fromstring(resp.text)
    titles = [
        (it.findtext("title") or "")
        for it in root.findall("channel/item")
    ]
    assert any("Visible" in t for t in titles)
    assert not any(t == "Hidden" for t in titles)


@pytest.mark.anyio
async def test_rss_feed_empty_still_valid(client):
    """Even with zero notes, the feed should render a valid RSS 2.0
    doc with an empty channel — feed readers hate 500s."""
    resp = await client.get("/meetups.rss")
    assert resp.status_code == 200
    root = ET.fromstring(resp.text)
    assert root.tag == "rss"
    channel = root.find("channel")
    assert channel is not None
    items = channel.findall("item")
    assert items == []


# ── /attractions badge counts (Phase 5 polish) ──────────────────

@pytest.mark.anyio
async def test_attractions_index_shows_live_meetup_counts(client):
    """Phase 2 wired get_attraction_meetup_counts into /attractions.
    Phase 5 polishes the footer copy but the live-count behavior
    must still render correctly. Seed 2 notes under parlor and
    assert the card reads 2 meetups, not the stub 0."""
    await _seed_clean_note("parlor", title="A", hours=3)
    await _seed_clean_note("parlor", title="B", hours=6)
    # sphere unseeded so its card should still read 0.
    resp = await client.get("/attractions")
    assert resp.status_code == 200
    body = resp.text
    # Parlor card shows "2 meetups"
    assert "2 meetups" in body
    # Footer link to /meetups (Phase 5 footer polish)
    assert 'href="/meetups"' in body


@pytest.mark.anyio
async def test_meetups_footer_links_to_rss(client):
    """The HTML page should advertise the RSS feed. Pinboard-watchers
    following via RSS is the whole point of the feed."""
    resp = await client.get("/meetups")
    assert resp.status_code == 200
    assert "/meetups.rss" in resp.text
