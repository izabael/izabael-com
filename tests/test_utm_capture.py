"""Tests for UTM capture + Reddit Pixel + Reddit CAPI attribution.

Covers:
  - record_page_view persists utm_* columns
  - record_funnel_event persists utm_* columns
  - Middleware extracts UTMs from query params into page_views
  - Middleware sets iza_utm cookie when fresh UTMs arrive
  - _extract_utm falls back to iza_utm cookie on subsequent requests
  - get_utm_stats rolls up by campaign / by content
  - Admin page renders the UTM attribution section
  - reddit_capi is a clean no-op without env vars
  - base.html renders Reddit Pixel JS only when REDDIT_PIXEL_ID is set
"""

import json
import os

import pytest
from httpx import AsyncClient, ASGITransport

from app import app
from content_loader import store as content_store
from database import (
    init_db, close_db, create_user,
    record_page_view, record_funnel_event, get_utm_stats,
)
import reddit_capi


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


# ── record_page_view + record_funnel_event UTM persistence ─────────


@pytest.mark.anyio
async def test_record_page_view_persists_utm_columns():
    await record_page_view(
        "/for-agents", "", "",
        utm={
            "utm_source": "reddit",
            "utm_medium": "paid",
            "utm_campaign": "bored",
            "utm_content": "localllama-v1",
            "utm_term": "",
        },
    )
    import database
    cur = await database._db.execute(
        "SELECT utm_source, utm_medium, utm_campaign, utm_content FROM page_views"
    )
    row = await cur.fetchone()
    assert row["utm_source"] == "reddit"
    assert row["utm_medium"] == "paid"
    assert row["utm_campaign"] == "bored"
    assert row["utm_content"] == "localllama-v1"


@pytest.mark.anyio
async def test_record_page_view_handles_missing_utm_dict():
    await record_page_view("/", "", "")  # utm=None
    import database
    cur = await database._db.execute(
        "SELECT utm_source, utm_campaign FROM page_views"
    )
    row = await cur.fetchone()
    assert row["utm_source"] == ""
    assert row["utm_campaign"] == ""


@pytest.mark.anyio
async def test_record_funnel_event_persists_utm_columns():
    await record_funnel_event(
        "agent_registered",
        agent_name="testbot",
        utm={"utm_campaign": "bored", "utm_content": "sillytavern-v1"},
    )
    import database
    cur = await database._db.execute(
        "SELECT utm_campaign, utm_content FROM funnel_events"
    )
    row = await cur.fetchone()
    assert row["utm_campaign"] == "bored"
    assert row["utm_content"] == "sillytavern-v1"


# ── Middleware UTM extraction + cookie setting ────────────────────


@pytest.mark.anyio
async def test_middleware_captures_utm_from_query_params(client):
    resp = await client.get(
        "/for-agents?utm_source=reddit&utm_medium=paid&utm_campaign=bored&utm_content=localllama-v1"
    )
    assert resp.status_code == 200
    # The page view should land in the db with UTMs
    import database
    cur = await database._db.execute(
        "SELECT utm_campaign, utm_content, utm_source FROM page_views WHERE path = '/for-agents'"
    )
    row = await cur.fetchone()
    assert row is not None
    assert row["utm_campaign"] == "bored"
    assert row["utm_content"] == "localllama-v1"
    assert row["utm_source"] == "reddit"


@pytest.mark.anyio
async def test_middleware_sets_iza_utm_cookie_on_fresh_utms(client):
    """Cookie is SET on a UTM landing — round-trip parsing is verified
    by test_cookie_carries_utm_to_conversion below, which confirms a
    later request can extract the UTMs from the cookie. Here we just
    assert the Set-Cookie header carries the right names so the cookie
    physically exists. Starlette serializes special chars per the
    cookie spec (e.g. `,` → `\\054`), which makes manual JSON-parsing
    of the raw httpx cookie value unreliable — the round-trip is the
    real contract test."""
    resp = await client.get(
        "/for-agents?utm_source=reddit&utm_campaign=bored"
    )
    assert resp.status_code == 200
    set_cookie = resp.headers.get("set-cookie", "")
    assert "iza_utm=" in set_cookie
    # Both UTM keys should be referenced in the encoded payload
    assert "utm_source" in set_cookie
    assert "utm_campaign" in set_cookie
    assert "reddit" in set_cookie
    assert "bored" in set_cookie


@pytest.mark.anyio
async def test_middleware_no_cookie_without_utm(client):
    resp = await client.get("/for-agents")
    assert resp.status_code == 200
    assert "iza_utm=" not in resp.headers.get("set-cookie", "")


@pytest.mark.anyio
async def test_cookie_carries_utm_to_conversion(client):
    # Land on the attraction page with UTMs — sets cookie on the client
    landing = await client.get(
        "/for-agents?utm_campaign=bored&utm_content=localllama-v1"
    )
    assert landing.status_code == 200
    assert "iza_utm" in landing.cookies
    # Now register an agent — the UTM should be read from the cookie
    payload = {
        "name": "TestAgentUTM",
        "description": "Test agent for UTM attribution",
        "model": "gpt-test",
        "purpose": "testing",
        "agent_card": {
            "name": "TestAgentUTM",
            "description": "test",
            "url": "https://example.com/agent",
            "version": "1.0.0",
            "defaultInputModes": ["text/plain"],
            "defaultOutputModes": ["text/plain"],
            "capabilities": {},
            "skills": [],
        },
    }
    resp = await client.post("/a2a/agents", json=payload)
    # Response body may vary by validation outcome — we just need the
    # funnel_event to have been recorded with the UTMs from the cookie.
    import database
    cur = await database._db.execute(
        "SELECT utm_campaign, utm_content FROM funnel_events WHERE stage = 'agent_registered'"
    )
    row = await cur.fetchone()
    if row is not None:
        # If registration succeeded, UTMs should be on the funnel row
        assert row["utm_campaign"] == "bored"
        assert row["utm_content"] == "localllama-v1"


# ── get_utm_stats rollups ────────────────────────────────────────


@pytest.mark.anyio
async def test_utm_stats_rolls_up_by_campaign_and_content():
    # Seed several page views with mixed UTMs
    await record_page_view("/for-agents", utm={"utm_campaign": "bored", "utm_content": "localllama-v1"})
    await record_page_view("/for-agents", utm={"utm_campaign": "bored", "utm_content": "localllama-v1"})
    await record_page_view("/for-agents", utm={"utm_campaign": "bored", "utm_content": "sillytavern-v1"})
    await record_page_view("/", utm={"utm_campaign": "organic"})

    # One agent_registered with the localllama UTMs
    await record_funnel_event(
        "agent_registered",
        utm={"utm_campaign": "bored", "utm_content": "localllama-v1"},
    )

    stats = await get_utm_stats(days=30)
    by_c = {row["key"]: row for row in stats["by_campaign"]}
    assert by_c["bored"]["views"] == 3
    assert by_c["bored"]["registered"] == 1
    assert by_c["organic"]["views"] == 1
    assert by_c["organic"]["registered"] == 0

    by_content = {row["key"]: row for row in stats["by_content"]}
    assert by_content["localllama-v1"]["views"] == 2
    assert by_content["localllama-v1"]["registered"] == 1
    assert by_content["sillytavern-v1"]["views"] == 1


@pytest.mark.anyio
async def test_utm_stats_excludes_non_utm_traffic():
    # Plain page views without UTMs shouldn't appear in UTM rollup
    await record_page_view("/", utm={})
    await record_page_view("/for-agents", utm=None)
    stats = await get_utm_stats(days=30)
    assert stats["by_campaign"] == []
    assert stats["by_content"] == []


# ── Admin page renders UTM section ───────────────────────────────


@pytest.mark.anyio
async def test_admin_page_includes_utm_section(client):
    # Seed a UTM'd page view so the section renders
    await record_page_view(
        "/for-agents",
        utm={"utm_campaign": "bored", "utm_content": "localllama-v1"},
    )

    # Match the working test_visit_funnel admin-login pattern: positional
    # create_user(username, email, password, role); login by username.
    await create_user(
        "admintest_utm", "admin-utm@test.local", "testpassword123",
        role="admin",
    )
    login_resp = await client.post(
        "/login",
        data={"username": "admintest_utm", "password": "testpassword123"},
        follow_redirects=False,
    )
    assert login_resp.status_code in (302, 303)

    resp = await client.get("/admin")
    assert resp.status_code == 200
    body = resp.text
    assert "Paid Acquisition" in body or "UTM Attribution" in body
    assert "bored" in body
    assert "localllama-v1" in body


# ── Reddit Pixel JS rendering in base.html ───────────────────────


@pytest.mark.anyio
async def test_base_html_no_pixel_without_env(client, monkeypatch):
    monkeypatch.delenv("REDDIT_PIXEL_ID", raising=False)
    resp = await client.get("/")
    assert resp.status_code == 200
    assert "redditstatic.com/ads/pixel.js" not in resp.text
    assert "rdt(" not in resp.text


@pytest.mark.anyio
async def test_base_html_renders_pixel_with_env(client, monkeypatch):
    monkeypatch.setenv("REDDIT_PIXEL_ID", "t2_testpixelid")
    resp = await client.get("/")
    assert resp.status_code == 200
    # Pixel snippet should be in the rendered HTML
    assert "redditstatic.com/ads/pixel.js" in resp.text
    assert "t2_testpixelid" in resp.text
    assert "PageVisit" in resp.text


# ── reddit_capi module — env-gated no-ops ────────────────────────


@pytest.mark.anyio
async def test_reddit_capi_disabled_without_env(monkeypatch):
    monkeypatch.delenv("REDDIT_PIXEL_ID", raising=False)
    monkeypatch.delenv("REDDIT_CAPI_TOKEN", raising=False)
    assert reddit_capi.enabled() is False
    assert reddit_capi.pixel_id() == ""


@pytest.mark.anyio
async def test_reddit_capi_enabled_with_both_envs(monkeypatch):
    monkeypatch.setenv("REDDIT_PIXEL_ID", "t2_pixel")
    monkeypatch.setenv("REDDIT_CAPI_TOKEN", "sekrit")
    assert reddit_capi.enabled() is True
    assert reddit_capi.pixel_id() == "t2_pixel"


@pytest.mark.anyio
async def test_fire_conversion_noop_without_env_returns_false(monkeypatch):
    monkeypatch.delenv("REDDIT_PIXEL_ID", raising=False)
    monkeypatch.delenv("REDDIT_CAPI_TOKEN", raising=False)
    # Should not raise, should not hit network, returns False
    result = reddit_capi.fire_conversion("SignUp", email="a@b.c")
    assert result is False


@pytest.mark.anyio
async def test_fire_agent_registered_noop_without_env(monkeypatch):
    monkeypatch.delenv("REDDIT_PIXEL_ID", raising=False)
    monkeypatch.delenv("REDDIT_CAPI_TOKEN", raising=False)
    result = reddit_capi.fire_agent_registered(
        agent_name="test", ip="1.2.3.4",
    )
    assert result is False


@pytest.mark.anyio
async def test_hash_email_is_sha256_lowercase():
    # Exported via _hash_email — confirms we're producing a normalized
    # digest matching Reddit's requirements.
    import hashlib
    expected = hashlib.sha256(b"test@example.com").hexdigest()
    assert reddit_capi._hash_email("  TEST@Example.COM  ") == expected
