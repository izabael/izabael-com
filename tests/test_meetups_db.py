"""Tests for the meetup-notes data layer.

Round-trip insert/retrieve, listing semantics, expiration cleanup,
signup lifecycle, duplicate-prevention, author-only soft delete.
"""

import os
from datetime import datetime, timedelta, timezone

import pytest

from database import (
    init_db, close_db,
    create_meetup_note, get_meetup_note,
    list_notes_for_attraction, list_all_upcoming_notes,
    signup_for_meetup, list_signups,
    delete_meetup_note, cleanup_expired_notes,
    get_attraction_meetup_counts,
)


# ── Fixtures ────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
async def _init_test_db(tmp_path):
    os.environ["IZABAEL_DB"] = str(tmp_path / "meetups.db")
    import database
    database.DB_PATH = str(tmp_path / "meetups.db")
    await init_db()
    yield
    await close_db()


# ── Helpers ─────────────────────────────────────────────────────

def _future_iso(hours_from_now: float = 24) -> str:
    dt = datetime.now(timezone.utc) + timedelta(hours=hours_from_now)
    return dt.isoformat(timespec="seconds").replace("+00:00", "Z")


def _past_iso(hours_ago: float = 4) -> str:
    dt = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    return dt.isoformat(timespec="seconds").replace("+00:00", "Z")


# ── Create + retrieve ───────────────────────────────────────────

@pytest.mark.anyio
async def test_create_and_get_roundtrip():
    note_id = await create_meetup_note(
        attraction_slug="parlor",
        author_kind="human",
        author_label="Marlowe",
        title="Sunday parlor session",
        goal="Watch the lobby riff",
        when_iso=_future_iso(24),
        when_text="Sunday 8pm PT",
        ip="10.1.2.3",
    )
    assert note_id
    note = await get_meetup_note(note_id)
    assert note is not None
    assert note["attraction_slug"] == "parlor"
    assert note["author_kind"] == "human"
    assert note["author_label"] == "Marlowe"
    assert note["title"] == "Sunday parlor session"
    assert note["goal"] == "Watch the lobby riff"
    assert note["when_text"] == "Sunday 8pm PT"
    assert note["is_visible"] is True
    assert note["spam_score"] is None  # Phase 3 writes this
    assert note["spam_verdict"] is None


@pytest.mark.anyio
async def test_agent_authored_note_requires_agent_name():
    with pytest.raises(ValueError):
        await create_meetup_note(
            attraction_slug="chamber",
            author_kind="agent",
            author_label="Zeus",
            title="Strategy sync",
            goal="3pm, observing agents welcome",
            when_iso=_future_iso(6),
            when_text="in 6 hours",
            # missing author_agent
        )


@pytest.mark.anyio
async def test_invalid_recurrence_rejected():
    with pytest.raises(ValueError):
        await create_meetup_note(
            attraction_slug="parlor",
            author_kind="human",
            author_label="M",
            title="t",
            goal="g",
            when_iso=_future_iso(2),
            when_text="soon",
            recurrence="yearly",  # not in the enum
        )


@pytest.mark.anyio
async def test_invalid_author_kind_rejected():
    with pytest.raises(ValueError):
        await create_meetup_note(
            attraction_slug="parlor",
            author_kind="goblin",
            author_label="M",
            title="t",
            goal="g",
            when_iso=_future_iso(2),
            when_text="soon",
        )


# ── Listing ─────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_list_by_attraction_returns_upcoming_only():
    await create_meetup_note(
        attraction_slug="parlor", author_kind="human",
        author_label="A", title="future", goal="g",
        when_iso=_future_iso(12), when_text="tomorrow",
    )
    await create_meetup_note(
        attraction_slug="parlor", author_kind="human",
        author_label="B", title="past", goal="g",
        when_iso=_past_iso(12), when_text="yesterday",
    )
    upcoming = await list_notes_for_attraction("parlor")
    titles = {n["title"] for n in upcoming}
    assert "future" in titles
    assert "past" not in titles


@pytest.mark.anyio
async def test_list_by_attraction_sort_is_chronological():
    ids = []
    for i, hours in enumerate([48, 12, 6, 24]):
        ids.append(await create_meetup_note(
            attraction_slug="parlor", author_kind="human",
            author_label=f"A{i}", title=f"note-{i}", goal="g",
            when_iso=_future_iso(hours), when_text=f"+{hours}h",
        ))
    upcoming = await list_notes_for_attraction("parlor")
    # Soonest first: 6h, 12h, 24h, 48h → titles note-2, note-1, note-3, note-0
    assert [n["title"] for n in upcoming] == ["note-2", "note-1", "note-3", "note-0"]


@pytest.mark.anyio
async def test_list_all_upcoming_spans_attractions():
    await create_meetup_note(
        attraction_slug="parlor", author_kind="human",
        author_label="A", title="parlor-note", goal="g",
        when_iso=_future_iso(4), when_text="soon",
    )
    await create_meetup_note(
        attraction_slug="chamber", author_kind="human",
        author_label="B", title="chamber-note", goal="g",
        when_iso=_future_iso(8), when_text="later",
    )
    upcoming = await list_all_upcoming_notes()
    slugs = {n["attraction_slug"] for n in upcoming}
    assert slugs == {"parlor", "chamber"}


@pytest.mark.anyio
async def test_attraction_meetup_counts():
    await create_meetup_note(
        attraction_slug="parlor", author_kind="human",
        author_label="A", title="a", goal="g",
        when_iso=_future_iso(2), when_text="x",
    )
    await create_meetup_note(
        attraction_slug="parlor", author_kind="human",
        author_label="B", title="b", goal="g",
        when_iso=_future_iso(6), when_text="x",
    )
    await create_meetup_note(
        attraction_slug="chamber", author_kind="human",
        author_label="C", title="c", goal="g",
        when_iso=_future_iso(12), when_text="x",
    )
    counts = await get_attraction_meetup_counts()
    assert counts == {"parlor": 2, "chamber": 1}


# ── Signups ─────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_signup_roundtrip():
    note_id = await create_meetup_note(
        attraction_slug="parlor", author_kind="human",
        author_label="Host", title="Session", goal="g",
        when_iso=_future_iso(4), when_text="later",
    )
    signup_id = await signup_for_meetup(
        note_id=note_id, signup_kind="human",
        handle="Visitor", delivery="email",
        delivery_target="visitor@example.com",
    )
    assert signup_id
    signups = await list_signups(note_id)
    assert len(signups) == 1
    assert signups[0]["handle"] == "Visitor"
    assert signups[0]["delivery"] == "email"
    assert signups[0]["notified_at"] is None


@pytest.mark.anyio
async def test_signup_is_idempotent_per_handle():
    note_id = await create_meetup_note(
        attraction_slug="parlor", author_kind="human",
        author_label="Host", title="Session", goal="g",
        when_iso=_future_iso(4), when_text="later",
    )
    first = await signup_for_meetup(
        note_id=note_id, signup_kind="human",
        handle="Visitor", delivery="none",
    )
    second = await signup_for_meetup(
        note_id=note_id, signup_kind="human",
        handle="Visitor", delivery="none",
    )
    assert first == second
    assert len(await list_signups(note_id)) == 1


@pytest.mark.anyio
async def test_signup_capacity_limit():
    note_id = await create_meetup_note(
        attraction_slug="parlor", author_kind="human",
        author_label="Host", title="Session", goal="g",
        when_iso=_future_iso(4), when_text="later",
        capacity=2,
    )
    for name in ("A", "B"):
        assert await signup_for_meetup(
            note_id=note_id, signup_kind="human",
            handle=name, delivery="none",
        )
    overflow = await signup_for_meetup(
        note_id=note_id, signup_kind="human",
        handle="C", delivery="none",
    )
    assert overflow is None


@pytest.mark.anyio
async def test_signup_unknown_note_returns_none():
    result = await signup_for_meetup(
        note_id="does-not-exist", signup_kind="human",
        handle="X", delivery="none",
    )
    assert result is None


# ── Soft delete ─────────────────────────────────────────────────

@pytest.mark.anyio
async def test_author_only_soft_delete_hides_note():
    note_id = await create_meetup_note(
        attraction_slug="parlor", author_kind="human",
        author_label="Marlowe", title="mine", goal="g",
        when_iso=_future_iso(4), when_text="x",
    )
    # Wrong author label — rejected
    ok = await delete_meetup_note(note_id, "Not-Marlowe")
    assert ok is False
    note_after = await get_meetup_note(note_id)
    assert note_after["is_visible"] is True

    # Correct author label — accepted
    ok = await delete_meetup_note(note_id, "Marlowe")
    assert ok is True

    # Soft-deleted: hidden from listings but still present in the DB
    visible = await list_notes_for_attraction("parlor")
    assert not any(n["note_id"] == note_id for n in visible)
    raw = await get_meetup_note(note_id)
    assert raw is not None
    assert raw["is_visible"] is False


@pytest.mark.anyio
async def test_delete_nonexistent_returns_false():
    ok = await delete_meetup_note("nope", "Anyone")
    assert ok is False


# ── Cleanup ─────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_cleanup_expired_notes_deletes_old_rows():
    import database as _database
    # Insert a note directly with a very old expires_at
    await _database._db.execute(
        """INSERT INTO meetup_notes (
            note_id, attraction_slug, author_kind, author_label,
            title, goal, when_iso, when_text,
            created_at, expires_at, recurrence, is_visible
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'none', 1)""",
        (
            "old-note", "parlor", "human", "Ghost",
            "ancient", "g", "2020-01-01T00:00:00Z", "long ago",
            "2020-01-01T00:00:00Z",
            (datetime.now(timezone.utc) - timedelta(days=60))
                .isoformat(timespec="seconds").replace("+00:00", "Z"),
        ),
    )
    await _database._db.commit()

    deleted = await cleanup_expired_notes(retention_days=30)
    assert deleted >= 1
    assert await get_meetup_note("old-note") is None
