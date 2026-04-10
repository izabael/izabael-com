"""Tests for A2A host endpoints — registration, discovery, Agent Card."""

import os
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
    """Initialize an in-memory test DB for A2A operations."""
    os.environ["IZABAEL_DB"] = str(tmp_path / "test.db")
    # Re-import to pick up new path
    import database
    database.DB_PATH = str(tmp_path / "test.db")
    await init_db()
    yield
    await close_db()


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.anyio
async def test_agent_card(client):
    """/.well-known/agent.json returns the instance Agent Card."""
    resp = await client.get("/.well-known/agent.json")
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "Izabael's AI Playground"
    assert data["url"] == "https://izabael.com"
    assert data["version"] == "1.0.0"
    assert "agentRegistration" in data["capabilities"]
    assert "playground/persona" in data.get("extensions", {})


@pytest.mark.anyio
async def test_discover_returns_list(client):
    """/discover returns a JSON list."""
    resp = await client.get("/discover")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.anyio
async def test_register_requires_tos(client):
    """Registration fails without ToS acceptance."""
    resp = await client.post("/a2a/agents", json={
        "name": "Test Agent",
        "description": "A test",
        "tos_accepted": False,
    })
    assert resp.status_code == 400


@pytest.mark.anyio
async def test_register_and_discover(client):
    """Register an agent, verify it appears in /discover, then delete it."""
    # Register
    resp = await client.post("/a2a/agents", json={
        "name": "Test Agent",
        "description": "An agent for testing",
        "provider": "test",
        "tos_accepted": True,
        "agent_card": {
            "skills": [{"id": "test", "name": "Testing", "description": "Tests things"}],
            "extensions": {
                "playground/persona": {
                    "voice": "Precise and methodical",
                    "values": ["testing", "correctness"],
                }
            }
        }
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"]
    agent_id = data["agent"]["id"]
    token = data["token"]
    assert token

    # Verify in discover
    resp = await client.get("/discover")
    agents = resp.json()
    found = [a for a in agents if a["id"] == agent_id]
    assert len(found) == 1
    assert found[0]["name"] == "Test Agent"

    # Verify detail page
    resp = await client.get(f"/agents/{agent_id}")
    assert resp.status_code == 200
    assert "Test Agent" in resp.text

    # Delete with correct token
    resp = await client.delete(
        f"/a2a/agents/{agent_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"]

    # Verify gone from discover
    resp = await client.get("/discover")
    agents = resp.json()
    found = [a for a in agents if a["id"] == agent_id]
    assert len(found) == 0


@pytest.mark.anyio
async def test_federated_discover(client):
    """Federated discover returns agents with instance field."""
    resp = await client.get("/federation/discover")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.anyio
async def test_federation_peers_requires_admin(client):
    """Federation peer management requires admin role."""
    resp = await client.post("/federation/peers?url=https://example.fly.dev&name=Test")
    assert resp.status_code == 403


@pytest.mark.anyio
async def test_api_lobby(client):
    """/api/lobby returns JSON with agents list."""
    resp = await client.get("/api/lobby")
    assert resp.status_code == 200
    data = resp.json()
    assert "agents" in data
    assert "reachable" in data


@pytest.mark.anyio
async def test_api_digest(client):
    """/api/digest returns instance summary."""
    resp = await client.get("/api/digest")
    assert resp.status_code == 200
    data = resp.json()
    assert data["instance"] == "izabael.com"
    assert "agents" in data
    assert "channels" in data
    assert "blog" in data


@pytest.mark.anyio
async def test_admin_dashboard_requires_auth(client):
    """Admin dashboard redirects unauthenticated users to login."""
    resp = await client.get("/admin", follow_redirects=False)
    assert resp.status_code == 302
    assert "/login" in resp.headers.get("location", "")


@pytest.mark.anyio
async def test_delete_wrong_token(client):
    """Can't delete with wrong token."""
    # Register first
    resp = await client.post("/a2a/agents", json={
        "name": "Protected Agent",
        "description": "Can't delete me",
        "tos_accepted": True,
    })
    agent_id = resp.json()["agent"]["id"]
    real_token = resp.json()["token"]

    # Try wrong token
    resp = await client.delete(
        f"/a2a/agents/{agent_id}",
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert resp.status_code == 404

    # Cleanup with real token
    await client.delete(
        f"/a2a/agents/{agent_id}",
        headers={"Authorization": f"Bearer {real_token}"},
    )


# ── Local channels ──────────────────────────────────────────────────

@pytest.mark.anyio
async def test_channels_list_returns_seven(client):
    """/api/channels lists the seven seeded channels with message_count."""
    resp = await client.get("/api/channels")
    assert resp.status_code == 200
    channels = resp.json()
    assert len(channels) == 7
    names = {c["name"] for c in channels}
    assert "#lobby" in names
    assert "#gallery" in names
    for c in channels:
        assert "message_count" in c
        assert isinstance(c["message_count"], int)


@pytest.mark.anyio
async def test_channel_messages_empty(client):
    """An untouched channel returns an empty list, not 404."""
    resp = await client.get("/api/channels/lobby/messages")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.anyio
async def test_post_message_requires_token(client):
    """POST /api/messages without auth returns 401."""
    resp = await client.post("/api/messages", json={
        "channel": "#lobby",
        "body": "drive-by",
    })
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_post_message_invalid_token(client):
    """POST /api/messages with garbage token returns 401."""
    resp = await client.post(
        "/api/messages",
        json={"channel": "#lobby", "body": "hi"},
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_post_message_round_trip(client):
    """Register, post, read back, verify content + sender."""
    # Register
    resp = await client.post("/a2a/agents", json={
        "name": "Lobby Speaker",
        "description": "talks in lobbies",
        "tos_accepted": True,
    })
    assert resp.status_code == 200
    token = resp.json()["token"]
    agent_id = resp.json()["agent"]["id"]

    # Post
    resp = await client.post(
        "/api/messages",
        json={"channel": "#lobby", "body": "hello izabael.com"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["ok"] is True
    msg = payload["message"]
    assert msg["body"] == "hello izabael.com"
    assert msg["sender_name"] == "Lobby Speaker"
    assert msg["sender_id"] == agent_id
    assert msg["channel"] == "#lobby"
    assert msg["source"] == "local"

    # Read
    resp = await client.get("/api/channels/lobby/messages")
    assert resp.status_code == 200
    msgs = resp.json()
    assert len(msgs) == 1
    assert msgs[0]["body"] == "hello izabael.com"
    assert msgs[0]["sender_name"] == "Lobby Speaker"

    # /api/channels message_count reflects the post
    resp = await client.get("/api/channels")
    by_name = {c["name"]: c for c in resp.json()}
    assert by_name["#lobby"]["message_count"] == 1


@pytest.mark.anyio
async def test_post_message_unknown_channel(client):
    """Posting to a non-seeded channel returns 404."""
    resp = await client.post("/a2a/agents", json={
        "name": "Stray", "description": "x", "tos_accepted": True,
    })
    token = resp.json()["token"]
    resp = await client.post(
        "/api/messages",
        json={"channel": "#nope", "body": "x"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_post_message_validation(client):
    """Empty body and missing channel both return 400."""
    resp = await client.post("/a2a/agents", json={
        "name": "Stray2", "description": "x", "tos_accepted": True,
    })
    token = resp.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    resp = await client.post("/api/messages",
        json={"channel": "#lobby", "body": ""}, headers=headers)
    assert resp.status_code == 400

    resp = await client.post("/api/messages",
        json={"body": "no channel"}, headers=headers)
    assert resp.status_code == 400


@pytest.mark.anyio
async def test_messages_since_polling(client):
    """?since=N only returns messages newer than the given id."""
    resp = await client.post("/a2a/agents", json={
        "name": "Pollster", "description": "polls", "tos_accepted": True,
    })
    token = resp.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Post three messages
    for i in range(3):
        await client.post("/api/messages",
            json={"channel": "#lobby", "body": f"msg {i}"}, headers=headers)

    # Read all
    resp = await client.get("/api/channels/lobby/messages")
    msgs = resp.json()
    assert len(msgs) == 3
    second_id = msgs[1]["id"]

    # since=second_id returns only the third
    resp = await client.get(f"/api/channels/lobby/messages?since={second_id}")
    new_msgs = resp.json()
    assert len(new_msgs) == 1
    assert new_msgs[0]["body"] == "msg 2"


# ── Persona templates ───────────────────────────────────────────────

@pytest.mark.anyio
async def test_mods_page_lists_local_templates(client):
    """The /mods page renders with local templates seeded from
    seeds/persona_templates.json."""
    resp = await client.get("/mods")
    assert resp.status_code == 200
    text = resp.text
    # Seeded RPG-class templates should appear
    assert "The Bard" in text
    assert "The Wizard" in text


@pytest.mark.anyio
async def test_read_fallback_disabled_by_default(client, monkeypatch):
    """Without READ_FALLBACK_ENABLED set, fallback helpers return [].
    /health reflects disabled state."""
    monkeypatch.delenv("READ_FALLBACK_ENABLED", raising=False)

    from read_fallback import fallback_agents, fallback_messages, fallback_status
    assert await fallback_agents() == []
    assert await fallback_messages("#lobby") == []

    status = fallback_status()
    assert status["enabled"] is False
    assert status["url"] == ""

    resp = await client.get("/health")
    assert resp.json()["read_fallback"]["enabled"] is False


@pytest.mark.anyio
async def test_read_fallback_health_reports_when_on(client, monkeypatch):
    """With the env flag set, /health reports the fallback URL.
    No actual upstream call is made (we don't test the network here)."""
    monkeypatch.setenv("READ_FALLBACK_ENABLED", "1")
    monkeypatch.setenv("READ_FALLBACK_URL", "https://example.invalid")
    resp = await client.get("/health")
    rf = resp.json()["read_fallback"]
    assert rf["enabled"] is True
    assert rf["url"] == "https://example.invalid"


@pytest.mark.anyio
async def test_messages_alias_route(client):
    """POST /messages is an alias for POST /api/messages — same handler,
    same auth requirement, same response. Lets cron-driven and
    cross-instance clients repoint with a host-only swap."""
    # Register
    resp = await client.post("/agents", json={
        "name": "Cron Bot",
        "description": "posts on a schedule",
        "tos_accepted": True,
    })
    token = resp.json()["token"]

    # No auth → 401
    resp = await client.post("/messages", json={
        "channel": "#lobby", "body": "drive-by",
    })
    assert resp.status_code == 401

    # With auth → success
    resp = await client.post(
        "/messages",
        json={"channel": "#lobby", "body": "via the alias path"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["ok"] is True
    assert resp.json()["message"]["body"] == "via the alias path"

    # Round-trip read
    resp = await client.get("/api/channels/lobby/messages")
    bodies = [m["body"] for m in resp.json()]
    assert "via the alias path" in bodies


@pytest.mark.anyio
async def test_agents_alias_route(client):
    """POST /agents is an alias for POST /a2a/agents — same handler,
    same response shape. Lets the launch post and awesome-a2a entry
    use the shorter URL that matches ai-playground.fly.dev."""
    resp = await client.post("/agents", json={
        "name": "Alias Test",
        "description": "registered via /agents not /a2a/agents",
        "tos_accepted": True,
    })
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["ok"] is True
    assert data["agent"]["name"] == "Alias Test"
    assert data["token"]

    # And the agent shows up in /discover the same way
    resp = await client.get("/discover")
    names = {a["name"] for a in resp.json()}
    assert "Alias Test" in names


@pytest.mark.anyio
async def test_discover_returns_only_local(client):
    """/discover should return only the local agent roster — never
    merge in upstream backend agents."""
    # Register a known local agent
    resp = await client.post("/a2a/agents", json={
        "name": "Discover Test", "description": "x", "tos_accepted": True,
    })
    assert resp.status_code == 200

    resp = await client.get("/discover")
    agents = resp.json()
    names = {a["name"] for a in agents}
    assert "Discover Test" in names
    # Sanity: no field that would only exist on upstream backend agents
    # (e.g. all returned agents must have the local 'agent_card' field)
    for a in agents:
        assert "id" in a and "name" in a
