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
async def test_federation_peers_crud(client):
    """Add, list, remove federation peers."""
    # Add peer
    resp = await client.post("/federation/peers?url=https://example.fly.dev&name=Test")
    assert resp.status_code == 200
    assert resp.json()["ok"]

    # List peers
    resp = await client.get("/federation/peers")
    assert resp.status_code == 200
    peers = resp.json()
    assert len(peers) >= 1
    assert peers[0]["url"] == "https://example.fly.dev"

    # Duplicate add
    resp = await client.post("/federation/peers?url=https://example.fly.dev")
    assert not resp.json()["ok"]

    # Remove
    resp = await client.request("DELETE", "/federation/peers?url=https://example.fly.dev")
    assert resp.status_code == 200
    assert resp.json()["ok"]


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
async def test_admin_dashboard_with_db(client):
    """Admin dashboard renders with DB initialized."""
    resp = await client.get("/admin")
    assert resp.status_code == 200
    assert "Dashboard" in resp.text


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
