"""Tests for user auth — registration, login, logout, admin protection."""

import os
import pytest
from httpx import AsyncClient, ASGITransport

from app import app
from content_loader import store as content_store
from database import init_db, close_db, create_user


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
async def test_register_page_renders(client):
    resp = await client.get("/register")
    assert resp.status_code == 200
    assert "Join the playground" in resp.text


@pytest.mark.anyio
async def test_login_page_renders(client):
    resp = await client.get("/login")
    assert resp.status_code == 200
    assert "Welcome back" in resp.text


@pytest.mark.anyio
async def test_register_creates_user(client):
    resp = await client.post("/register", data={
        "username": "testuser",
        "email": "test@example.com",
        "password": "securepass123",
        "display_name": "Test User",
    }, follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/"


@pytest.mark.anyio
async def test_register_rejects_short_password(client):
    resp = await client.post("/register", data={
        "username": "testuser",
        "email": "test@example.com",
        "password": "short",
    })
    assert resp.status_code == 400
    assert "at least 8 characters" in resp.text


@pytest.mark.anyio
async def test_register_rejects_duplicate_username(client):
    await create_user("taken", "taken@example.com", "password123")
    resp = await client.post("/register", data={
        "username": "taken",
        "email": "other@example.com",
        "password": "password123",
    })
    assert resp.status_code == 409
    assert "Registration failed" in resp.text


@pytest.mark.anyio
async def test_login_with_valid_credentials(client):
    await create_user("testuser", "test@example.com", "password123")
    resp = await client.post("/login", data={
        "username": "testuser",
        "password": "password123",
        "next": "/",
    }, follow_redirects=False)
    assert resp.status_code == 302


@pytest.mark.anyio
async def test_login_with_invalid_credentials(client):
    await create_user("testuser", "test@example.com", "password123")
    resp = await client.post("/login", data={
        "username": "testuser",
        "password": "wrongpassword",
        "next": "/",
    })
    assert resp.status_code == 401
    assert "Invalid" in resp.text


@pytest.mark.anyio
async def test_login_by_email(client):
    await create_user("testuser", "test@example.com", "password123")
    resp = await client.post("/login", data={
        "username": "test@example.com",
        "password": "password123",
        "next": "/",
    }, follow_redirects=False)
    assert resp.status_code == 302


@pytest.mark.anyio
async def test_admin_requires_admin_role(client):
    """Non-admin users get redirected from /admin."""
    # Create regular user and login
    await create_user("regular", "regular@example.com", "password123", role="user")
    resp = await client.post("/login", data={
        "username": "regular",
        "password": "password123",
        "next": "/admin",
    }, follow_redirects=False)
    assert resp.status_code == 302

    # Try admin with the session cookie from login
    resp = await client.get("/admin", follow_redirects=False)
    assert resp.status_code == 302
    assert "/login" in resp.headers.get("location", "")


@pytest.mark.anyio
async def test_admin_accessible_by_admin(client):
    """Admin users can access /admin."""
    await create_user("adminuser", "admin@example.com", "password123", role="admin")
    # Login
    await client.post("/login", data={
        "username": "adminuser",
        "password": "password123",
    }, follow_redirects=False)
    # Access admin
    resp = await client.get("/admin")
    assert resp.status_code == 200
    assert "Dashboard" in resp.text


@pytest.mark.anyio
async def test_bbs_page_renders(client):
    resp = await client.get("/bbs")
    assert resp.status_code == 200
    assert "NETZACH BBS" in resp.text


@pytest.mark.anyio
async def test_made_page_renders(client):
    resp = await client.get("/made")
    assert resp.status_code == 200
    assert "What We" in resp.text


@pytest.mark.anyio
async def test_made_category_filter(client):
    resp = await client.get("/made?category=occult")
    assert resp.status_code == 200


@pytest.mark.anyio
async def test_vote_requires_login(client):
    resp = await client.post("/made/butterfly/vote")
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_logout_clears_session(client):
    await create_user("testuser", "test@example.com", "password123")
    await client.post("/login", data={
        "username": "testuser",
        "password": "password123",
    }, follow_redirects=False)
    resp = await client.get("/logout", follow_redirects=False)
    assert resp.status_code == 302

    # After logout, account should redirect to login
    resp = await client.get("/account", follow_redirects=False)
    assert resp.status_code == 302
    assert "/login" in resp.headers.get("location", "")


@pytest.mark.anyio
async def test_account_requires_login(client):
    resp = await client.get("/account", follow_redirects=False)
    assert resp.status_code == 302
    assert "/login" in resp.headers.get("location", "")
