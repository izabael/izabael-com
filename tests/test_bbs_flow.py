"""End-to-end test: BBS login flow — register, link token, verify connected state."""

import os
import re
import pytest
from httpx import AsyncClient, ASGITransport

from app import app
from content_loader import store as content_store
from database import init_db, close_db, create_user, link_agent_token


def _extract_csrf(html: str) -> str:
    """Extract CSRF token from a rendered HTML page."""
    m = re.search(r'name="csrf_token"\s+value="([^"]+)"', html)
    return m.group(1) if m else ""


@pytest.fixture(autouse=True)
def _load_content():
    content_store.load()


@pytest.fixture(autouse=True)
async def _init_test_db(tmp_path):
    os.environ["IZABAEL_DB"] = str(tmp_path / "test.db")
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
async def test_bbs_shows_sign_in_when_not_logged_in(client):
    """Anonymous users see 'Sign in to post' on BBS."""
    resp = await client.get("/bbs")
    assert resp.status_code == 200
    assert "Sign in" in resp.text
    assert "NETZACH BBS" in resp.text


@pytest.mark.anyio
async def test_bbs_shows_link_prompt_when_no_token(client):
    """Logged-in users without agent token see 'Link your token'."""
    await create_user("bbsuser", "bbs@test.com", "password123")
    await client.post("/login", data={
        "username": "bbsuser", "password": "password123",
    }, follow_redirects=False)

    resp = await client.get("/bbs")
    assert resp.status_code == 200
    assert "Link your token" in resp.text


@pytest.mark.anyio
async def test_bbs_shows_connected_when_token_linked(client):
    """Logged-in users with linked agent token see 'connected as'."""
    user = await create_user("bbslinked", "linked@test.com", "password123")
    await link_agent_token(user["id"], "test-agent-token-xyz")

    # Login with CSRF token from login page
    resp = await client.get("/login")
    csrf = _extract_csrf(resp.text)
    await client.post("/login", data={
        "username": "bbslinked", "password": "password123",
        "csrf_token": csrf,
    }, follow_redirects=False)

    resp = await client.get("/bbs")
    assert resp.status_code == 200
    assert "connected as" in resp.text


@pytest.mark.anyio
async def test_api_my_token_returns_empty_when_not_logged_in(client):
    """Token endpoint returns empty when not logged in."""
    resp = await client.get("/api/my-token")
    assert resp.status_code == 200
    assert resp.json()["token"] == ""


@pytest.mark.anyio
async def test_api_my_token_returns_token_when_linked(client):
    """Token endpoint returns agent token when logged in + linked."""
    user = await create_user("tokenuser", "token@test.com", "password123")
    await link_agent_token(user["id"], "my-secret-agent-token")

    await client.post("/login", data={
        "username": "tokenuser", "password": "password123",
    }, follow_redirects=False)

    resp = await client.get("/api/my-token")
    assert resp.status_code == 200
    assert resp.json()["token"] == "my-secret-agent-token"


@pytest.mark.anyio
async def test_full_bbs_flow(client):
    """Full flow: register -> login -> link token -> BBS connected -> token endpoint works."""
    # 1. Register
    resp = await client.post("/register", data={
        "username": "fullflow", "email": "flow@test.com",
        "password": "securepass123", "display_name": "Flow Tester",
    }, follow_redirects=False)
    assert resp.status_code == 302

    # 2. Check BBS — logged in but no token
    resp = await client.get("/bbs")
    assert "Link your token" in resp.text

    # 3. Link a token (visit account page first for CSRF)
    resp = await client.get("/account")
    csrf = _extract_csrf(resp.text)
    resp = await client.post("/account/link-token", data={
        "agent_token": "flow-agent-token-abc",
        "csrf_token": csrf,
    }, follow_redirects=False)
    assert resp.status_code == 302

    # 4. Check BBS — now connected
    resp = await client.get("/bbs")
    assert "connected as" in resp.text

    # 5. Token endpoint returns it
    resp = await client.get("/api/my-token")
    assert resp.json()["token"] == "flow-agent-token-abc"

    # 6. JS will use this token to hit /api/messages — verified by proxy existence
    resp = await client.get("/api/channels/collaborations/messages")
    assert resp.status_code == 200  # proxy works (may return [] if backend down)
