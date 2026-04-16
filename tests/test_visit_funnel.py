"""Tests for Phase 10 — /visit → /join conversion funnel + analytics.

Covers:
  - record_funnel_event() persists rows
  - POST /visit/say fires a `guest_note` event and returns the handoff block
  - GET /join?from_visit=1&note=... renders the acknowledgement + note
  - POST /a2a/agents fires an `agent_registered` event
  - GET /agents/{id}/invite renders the landing page and fires `invite_landing`
  - /agents/{id}/invite is 404 for internal (_-prefixed) agents
  - GET /admin (admin only) renders the funnel table
  - agents/detail.html shows the "Invite your AI friend" share block
"""

import os
import pytest
from httpx import AsyncClient, ASGITransport

from app import app
from content_loader import store as content_store
from database import (
    init_db, close_db, create_user,
    record_funnel_event, get_funnel_stats,
)


@pytest.fixture(autouse=True)
def _load_content():
    content_store.load()


@pytest.fixture(autouse=True)
async def _init_test_db(tmp_path):
    os.environ["IZABAEL_DB"] = str(tmp_path / "test.db")
    import database
    database.DB_PATH = str(tmp_path / "test.db")
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


async def _count_stage(stage: str) -> int:
    import database
    assert database._db is not None
    cur = await database._db.execute(
        "SELECT COUNT(*) AS n FROM funnel_events WHERE stage = ?", (stage,)
    )
    row = await cur.fetchone()
    return row["n"] if row else 0


# ── record_funnel_event / get_funnel_stats ──────────────────────────

@pytest.mark.anyio
async def test_record_funnel_event_persists():
    await record_funnel_event("guest_note", agent_name="Alice")
    assert await _count_stage("guest_note") == 1


@pytest.mark.anyio
async def test_get_funnel_stats_shape():
    stats = await get_funnel_stats(days=7)
    assert "stages" in stats
    keys = [s["key"] for s in stats["stages"]]
    for needed in ("home", "visit", "guest_note", "join", "registered", "profile", "invite"):
        assert needed in keys


# ── /visit/say handoff + guest_note event ────────────────────────────

@pytest.mark.anyio
async def test_visit_say_fires_guest_note_event_and_returns_handoff(client):
    # /visit/say requires the _visitor agent to be registered by the
    # startup hook. In tests the lifespan doesn't always run, so we
    # register one directly to ensure the endpoint is ready.
    from database import register_agent
    import app as _app
    agent, token = await register_agent(
        name="_visitor", description="Test visitor",
        provider="local", model="", agent_card={},
        persona={}, skills=[], capabilities=[], purpose="",
    )
    _app._VISITOR_TOKEN = token

    resp = await client.post("/visit/say", json={
        "name": "Wayfarer",
        "message": "hello at the door",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "handoff" in data
    assert data["handoff"]["url"].startswith("/join?from_visit=1")
    assert "hello%20at%20the%20door" in data["handoff"]["url"]
    assert await _count_stage("guest_note") == 1


@pytest.mark.anyio
async def test_visit_say_does_not_pollute_real_queen_db(client, tmp_path, monkeypatch):
    """Regression: /visit/say must never write into the developer's real
    queen.db during a pytest run. Prior to the PYTEST_CURRENT_TEST guard
    in _queen_notify, every run of this suite appended a 'Wayfarer' row
    to ~/.claude/queen/queen.db on the laptop."""
    import sqlite3
    import app as _app
    from database import register_agent

    fake_queen = tmp_path / "queen.db"
    conn = sqlite3.connect(str(fake_queen))
    conn.execute(
        "CREATE TABLE messages ("
        "id INTEGER PRIMARY KEY, from_sister TEXT, to_sister TEXT NOT NULL,"
        " body TEXT NOT NULL, priority TEXT DEFAULT 'normal',"
        " sent_at TEXT NOT NULL, read_at TEXT, acked_at TEXT)"
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(_app, "QUEEN_DB_PATH", fake_queen)

    agent, token = await register_agent(
        name="_visitor", description="Test visitor",
        provider="local", model="", agent_card={},
        persona={}, skills=[], capabilities=[], purpose="",
    )
    _app._VISITOR_TOKEN = token

    resp = await client.post("/visit/say", json={
        "name": "Wayfarer",
        "message": "hello at the door",
    })
    assert resp.status_code == 200

    conn = sqlite3.connect(str(fake_queen))
    row = conn.execute("SELECT COUNT(*) FROM messages").fetchone()
    conn.close()
    assert row[0] == 0, (
        "pytest run leaked a guest-visitor row into the queen DB — "
        "the PYTEST_CURRENT_TEST guard in _queen_notify is missing or broken"
    )


# ── /join warm handoff block ────────────────────────────────────────

@pytest.mark.anyio
async def test_join_renders_handoff_ack_when_from_visit(client):
    resp = await client.get("/join?from_visit=1&note=brought%20a%20friend")
    assert resp.status_code == 200
    body = resp.text
    assert "Thanks for the note at the door" in body
    assert "brought a friend" in body


@pytest.mark.anyio
async def test_join_omits_ack_when_not_from_visit(client):
    resp = await client.get("/join")
    assert resp.status_code == 200
    assert "Thanks for the note at the door" not in resp.text


# ── agent_registered event on /a2a/agents ───────────────────────────

@pytest.mark.anyio
async def test_a2a_register_fires_agent_registered_event(client):
    resp = await client.post("/a2a/agents", json={
        "name": "Phase10Test",
        "description": "test agent",
        "provider": "anthropic",
        "tos_accepted": True,
    })
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert await _count_stage("agent_registered") == 1


@pytest.mark.anyio
async def test_a2a_alias_also_fires_agent_registered(client):
    resp = await client.post("/agents", json={
        "name": "AliasTest",
        "description": "via /agents alias",
        "provider": "anthropic",
        "tos_accepted": True,
    })
    assert resp.status_code == 200
    assert await _count_stage("agent_registered") == 1


# ── /agents/{id}/invite landing page ────────────────────────────────

@pytest.mark.anyio
async def test_invite_landing_renders_and_fires_event(client):
    from database import register_agent
    agent, _token = await register_agent(
        name="Muse",
        description="A thoughtful research partner.",
        provider="anthropic",
        model="",
        agent_card={},
        persona={"voice": "warm and curious"},
        skills=[],
        capabilities=[],
        purpose="companion",
    )
    resp = await client.get(f"/agents/{agent['name']}/invite")
    assert resp.status_code == 200
    body = resp.text
    assert "Muse" in body
    assert "You've been invited" in body or "invited" in body.lower()
    assert "Bring your own AI" in body
    assert await _count_stage("invite_landing") == 1


@pytest.mark.anyio
async def test_invite_landing_404s_for_internal_agent(client):
    from database import register_agent
    await register_agent(
        name="_hidden", description="", provider="local", model="",
        agent_card={}, persona={}, skills=[], capabilities=[], purpose="",
    )
    resp = await client.get("/agents/_hidden/invite")
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_invite_landing_404s_for_unknown(client):
    resp = await client.get("/agents/nobody-here/invite")
    assert resp.status_code == 404


# ── /agents/{id} share block ────────────────────────────────────────

@pytest.mark.anyio
async def test_agent_detail_shows_invite_share_block(client):
    from database import register_agent
    agent, _token = await register_agent(
        name="Pixel", description="sparkles", provider="anthropic",
        model="", agent_card={}, persona={}, skills=[], capabilities=[],
        purpose="companion",
    )
    resp = await client.get(f"/agents/{agent['name']}")
    assert resp.status_code == 200
    body = resp.text
    assert "Invite your AI friend to meet Pixel" in body
    assert "invite-copy-btn" in body
    assert "/agents/Pixel/invite" in body


# ── /admin funnel block ─────────────────────────────────────────────

@pytest.mark.anyio
async def test_admin_dashboard_shows_funnel(client):
    await create_user("admin1", "admin@example.com", "securepass123", role="admin")
    login = await client.post(
        "/login",
        data={"username": "admin1", "password": "securepass123"},
        follow_redirects=False,
    )
    assert login.status_code in (302, 303)

    # Seed a handful of events so the section has visible rows
    await record_funnel_event("guest_note")
    await record_funnel_event("agent_registered", agent_name="seed")

    resp = await client.get("/admin")
    assert resp.status_code == 200
    body = resp.text
    assert "Visit &rarr; Join Funnel" in body or "Visit → Join Funnel" in body
    assert "Note submitted" in body
    assert "Agent registered" in body
