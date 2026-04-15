"""Tests for the meetup-notes FastAPI routes.

Happy path on create + signup, JSON shape asserts, 404 on missing
note, 403 on non-author delete, 404 on invalid attraction slug.
"""

import os
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient, ASGITransport

from app import app
from content_loader import store as content_store
from database import init_db, close_db


@pytest.fixture(autouse=True)
def _load_content():
    content_store.load()


@pytest.fixture(autouse=True)
async def _init_test_db(tmp_path):
    os.environ["IZABAEL_DB"] = str(tmp_path / "api.db")
    import database
    database.DB_PATH = str(tmp_path / "api.db")
    await init_db()
    yield
    await close_db()


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _future_iso(hours: float = 24) -> str:
    dt = datetime.now(timezone.utc) + timedelta(hours=hours)
    return dt.isoformat(timespec="seconds").replace("+00:00", "Z")


# ── Create ──────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_create_meetup_happy_path(client):
    resp = await client.post(
        "/api/meetups/parlor/create",
        json={
            "author_kind": "human",
            "author_label": "Marlowe",
            "title": "Sunday parlor session",
            "goal": "Watch the lobby riff",
            "when_iso": _future_iso(24),
            "when_text": "Sunday 8pm PT",
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["ok"] is True
    assert data["note_id"]
    assert data["note"]["title"] == "Sunday parlor session"
    assert data["note"]["author_kind"] == "human"
    assert data["spam"]["verdict"] == "clean"  # stubbed in Phase 2


@pytest.mark.anyio
async def test_create_on_unknown_slug_404s(client):
    resp = await client.post(
        "/api/meetups/nonexistent/create",
        json={
            "author_kind": "human",
            "author_label": "M",
            "title": "t", "goal": "g",
            "when_iso": _future_iso(2), "when_text": "soon",
        },
    )
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_create_rejects_invalid_author_kind(client):
    resp = await client.post(
        "/api/meetups/parlor/create",
        json={
            "author_kind": "goblin",
            "author_label": "M",
            "title": "t", "goal": "g",
            "when_iso": _future_iso(2), "when_text": "soon",
        },
    )
    assert resp.status_code == 422  # Pydantic validation


# ── Read ────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_get_attraction_meetups_empty(client):
    resp = await client.get("/api/meetups/parlor")
    assert resp.status_code == 200
    data = resp.json()
    assert data["slug"] == "parlor"
    assert data["count"] == 0
    assert data["notes"] == []


@pytest.mark.anyio
async def test_get_attraction_meetups_after_create(client):
    await client.post(
        "/api/meetups/parlor/create",
        json={
            "author_kind": "human", "author_label": "M",
            "title": "x", "goal": "g",
            "when_iso": _future_iso(6), "when_text": "later",
        },
    )
    resp = await client.get("/api/meetups/parlor")
    data = resp.json()
    assert data["count"] == 1
    assert data["notes"][0]["title"] == "x"


@pytest.mark.anyio
async def test_get_unknown_attraction_404s(client):
    resp = await client.get("/api/meetups/nonexistent")
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_list_all_upcoming_spans_attractions(client):
    for slug in ("parlor", "bbs"):
        await client.post(
            f"/api/meetups/{slug}/create",
            json={
                "author_kind": "human", "author_label": "M",
                "title": f"{slug}-note", "goal": "g",
                "when_iso": _future_iso(4), "when_text": "later",
            },
        )
    resp = await client.get("/api/meetups")
    data = resp.json()
    slugs = {n["attraction_slug"] for n in data["notes"]}
    assert "parlor" in slugs
    assert "bbs" in slugs


# ── Signups ─────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_signup_happy_path(client):
    create_resp = await client.post(
        "/api/meetups/parlor/create",
        json={
            "author_kind": "human", "author_label": "Host",
            "title": "Session", "goal": "g",
            "when_iso": _future_iso(4), "when_text": "later",
        },
    )
    note_id = create_resp.json()["note_id"]

    signup_resp = await client.post(
        f"/api/meetups/{note_id}/signup",
        json={
            "signup_kind": "human",
            "handle": "Visitor",
            "delivery": "email",
            "delivery_target": "v@example.com",
        },
    )
    assert signup_resp.status_code == 200
    data = signup_resp.json()
    assert data["ok"] is True
    assert data["signup_id"]
    assert data["count"] == 1

    # Read back
    roster = await client.get(f"/api/meetups/{note_id}/signups")
    assert roster.status_code == 200
    rdata = roster.json()
    assert rdata["count"] == 1
    assert rdata["signups"][0]["handle"] == "Visitor"
    assert rdata["signups"][0]["delivery"] == "email"


@pytest.mark.anyio
async def test_signup_on_missing_note_404s(client):
    resp = await client.post(
        "/api/meetups/does-not-exist/signup",
        json={"signup_kind": "human", "handle": "X", "delivery": "none"},
    )
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_signup_capacity_enforced(client):
    create_resp = await client.post(
        "/api/meetups/parlor/create",
        json={
            "author_kind": "human", "author_label": "Host",
            "title": "Limited", "goal": "g",
            "when_iso": _future_iso(4), "when_text": "later",
            "capacity": 1,
        },
    )
    note_id = create_resp.json()["note_id"]

    ok = await client.post(
        f"/api/meetups/{note_id}/signup",
        json={"signup_kind": "human", "handle": "A", "delivery": "none"},
    )
    assert ok.status_code == 200

    overflow = await client.post(
        f"/api/meetups/{note_id}/signup",
        json={"signup_kind": "human", "handle": "B", "delivery": "none"},
    )
    assert overflow.status_code == 409


# ── Delete ──────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_author_delete_hides_note(client):
    create_resp = await client.post(
        "/api/meetups/parlor/create",
        json={
            "author_kind": "human", "author_label": "Marlowe",
            "title": "mine", "goal": "g",
            "when_iso": _future_iso(4), "when_text": "later",
        },
    )
    note_id = create_resp.json()["note_id"]

    resp = await client.request(
        "DELETE", f"/api/meetups/{note_id}",
        json={"author_label": "Marlowe"},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    listing = await client.get("/api/meetups/parlor")
    assert listing.json()["count"] == 0


@pytest.mark.anyio
async def test_delete_wrong_author_403s(client):
    create_resp = await client.post(
        "/api/meetups/parlor/create",
        json={
            "author_kind": "human", "author_label": "Marlowe",
            "title": "mine", "goal": "g",
            "when_iso": _future_iso(4), "when_text": "later",
        },
    )
    note_id = create_resp.json()["note_id"]

    resp = await client.request(
        "DELETE", f"/api/meetups/{note_id}",
        json={"author_label": "Impostor"},
    )
    assert resp.status_code == 403


@pytest.mark.anyio
async def test_delete_nonexistent_note_404s(client):
    resp = await client.request(
        "DELETE", "/api/meetups/does-not-exist",
        json={"author_label": "Whoever"},
    )
    assert resp.status_code == 404


# ── /attractions index ──────────────────────────────────────────

@pytest.mark.anyio
async def test_attractions_index_shows_live_counts(client):
    # Zero notes — should render 0 meetups
    resp = await client.get("/attractions")
    assert resp.status_code == 200
    assert "0 meetups" in resp.text

    # Create two for /parlor + one for /bbs and re-check
    for _ in range(2):
        await client.post(
            "/api/meetups/parlor/create",
            json={
                "author_kind": "human", "author_label": "M",
                "title": "p", "goal": "g",
                "when_iso": _future_iso(4), "when_text": "later",
            },
        )
    await client.post(
        "/api/meetups/bbs/create",
        json={
            "author_kind": "human", "author_label": "M",
            "title": "c", "goal": "g",
            "when_iso": _future_iso(4), "when_text": "later",
        },
    )

    resp = await client.get("/attractions")
    body = resp.text
    assert "2 meetups" in body  # parlor badge (plural)
    # BBS card shows singular "1 meetup" — careful: "2 meetups" also
    # contains substring "1 meetup" is False, it contains "2 meetup".
    # So we can check for the whole "📌 1 meetup" phrase as the singular.
    assert "1 meetup<" in body or "1 meetup " in body or "1 meetup\n" in body
