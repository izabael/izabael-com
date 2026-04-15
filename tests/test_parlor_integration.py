"""Integration tests for the /ai-parlor feature.

Each test skips gracefully until its required lane lands on the
izabael/ai-parlor branch, then automatically asserts the contract.
This file is the integration signal — when every test in here is
PASSING (not skipped), all four lanes have composed correctly.

Lanes:
  A (backend, iza-1)         — /api/parlor/* endpoints + /ai-parlor route
  B (templates, iza-3)       — index.html patch + ai-parlor.html
  C (CSS, meta-iza)          — .parlor-* class styles in style.css
  D (JS+integration, iza-2)  — parlor.js + integration verification

CSS lane is verified by the eye, not by tests — so this file checks
the other three lanes through the rendered HTML and JSON shapes.
"""
from __future__ import annotations

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
    os.environ["IZABAEL_DB"] = str(tmp_path / "parlor_integration.db")
    import database
    database.DB_PATH = str(tmp_path / "parlor_integration.db")
    await init_db()
    yield
    await close_db()


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ── Lane A: backend endpoints ───────────────────────────────────────

@pytest.mark.anyio
async def test_api_parlor_live_feed_exists_and_returns_array(client):
    """Lane A — GET /api/parlor/live-feed returns a JSON array."""
    resp = await client.get("/api/parlor/live-feed")
    if resp.status_code == 404:
        pytest.skip("Lane A backend not yet landed: /api/parlor/live-feed missing")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert isinstance(data, list), f"expected JSON array, got {type(data).__name__}"


@pytest.mark.anyio
async def test_api_parlor_live_feed_message_shape(client):
    """Lane A — every message in /api/parlor/live-feed has the documented fields."""
    resp = await client.get("/api/parlor/live-feed")
    if resp.status_code == 404:
        pytest.skip("Lane A backend not yet landed")
    assert resp.status_code == 200
    messages = resp.json()
    if not messages:
        pytest.skip("no messages in test DB to validate shape against")
    for msg in messages:
        for field in ("id", "channel", "sender_id", "sender_name", "sender_color", "body", "ts"):
            assert field in msg, f"live-feed message missing field: {field}"
        assert msg["channel"].startswith("#"), f"channel should start with #: {msg['channel']}"


@pytest.mark.anyio
async def test_api_parlor_live_feed_since_filter(client):
    """Lane A — ?since=N returns only messages with id > N."""
    resp = await client.get("/api/parlor/live-feed?since=999999999")
    if resp.status_code == 404:
        pytest.skip("Lane A backend not yet landed")
    assert resp.status_code == 200
    assert resp.json() == [], "since=huge should return empty list"


@pytest.mark.anyio
async def test_api_parlor_highlights_exists(client):
    """Lane A — GET /api/parlor/highlights returns a JSON array."""
    resp = await client.get("/api/parlor/highlights")
    if resp.status_code == 404:
        pytest.skip("Lane A backend not yet landed: /api/parlor/highlights missing")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)


@pytest.mark.anyio
async def test_api_parlor_highlights_exchange_shape(client):
    """Lane A — every exchange has id, channel, title, messages[], started_at."""
    resp = await client.get("/api/parlor/highlights")
    if resp.status_code == 404:
        pytest.skip("Lane A backend not yet landed")
    exchanges = resp.json()
    if not exchanges:
        pytest.skip("no highlights in test DB to validate shape against")
    for ex in exchanges:
        for field in ("id", "channel", "title", "messages", "started_at"):
            assert field in ex, f"highlight exchange missing field: {field}"
        assert isinstance(ex["messages"], list)
        for msg in ex["messages"]:
            for field in ("sender_name", "sender_color", "body", "ts"):
                assert field in msg, f"exchange message missing field: {field}"


@pytest.mark.anyio
async def test_api_parlor_summary_exists(client):
    """Lane A — GET /api/parlor/summary returns the documented shape."""
    resp = await client.get("/api/parlor/summary")
    if resp.status_code == 404:
        pytest.skip("Lane A backend not yet landed: /api/parlor/summary missing")
    assert resp.status_code == 200
    data = resp.json()
    assert "summary" in data, "summary response missing 'summary' field"
    assert isinstance(data["summary"], str)
    assert len(data["summary"]) > 0


@pytest.mark.anyio
async def test_api_parlor_moods_exists(client):
    """Lane A — GET /api/parlor/moods returns a dict (channel → mood)."""
    resp = await client.get("/api/parlor/moods")
    if resp.status_code == 404:
        pytest.skip("Lane A backend not yet landed: /api/parlor/moods missing")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, dict), f"expected dict, got {type(data).__name__}"
    # Empty dict is acceptable (Gemini failure or no data)
    for channel, mood in data.items():
        assert channel.startswith("#"), f"mood key should start with #: {channel}"
        assert isinstance(mood, str)


# ── Lane B: templates ────────────────────────────────────────────────

@pytest.mark.anyio
async def test_ai_parlor_page_renders(client):
    """Lane A + Lane B — GET /ai-parlor returns 200 and contains
    every documented DOM ID. This is the most important integration
    signal: it proves the route, the template, and the parlor.js
    script tag are all wired together."""
    resp = await client.get("/ai-parlor")
    if resp.status_code == 404:
        pytest.skip("Lane A backend not yet landed: /ai-parlor route missing")
    assert resp.status_code == 200
    text = resp.text

    # Documented DOM IDs that the JS targets
    required_ids = [
        "parlor-rightnow",
        "parlor-mosaic",
        "parlor-highlights",
        "parlor-summary",
        "parlor-clock",
        "parlor-header-text",
    ]
    for dom_id in required_ids:
        assert (f'id="{dom_id}"' in text) or (f"id='{dom_id}'" in text), \
            f"/ai-parlor missing DOM id={dom_id}"

    # parlor.js must be loaded
    assert "parlor.js" in text, "/ai-parlor missing parlor.js script"

    # At least one rotating tagline rendered server-side
    has_tagline = any(t in text for t in [
        "Live in the parlor", "Tonight in the lobby", "From the channels",
        "What's happening right now", "The parlor at this hour",
        "Currently in conversation",
    ])
    assert has_tagline, "/ai-parlor missing all rotating taglines"

    # Page brand — either the new "The Parlor" label or the ai-parlor URL
    assert "The Parlor" in text or "ai-parlor" in text.lower(), \
        "/ai-parlor missing brand title"


@pytest.mark.anyio
async def test_homepage_has_parlor_ticker(client):
    """Lane B — index.html patch adds the parlor-ticker section
    between the hero and the chaos star."""
    resp = await client.get("/")
    assert resp.status_code == 200
    text = resp.text
    has_ticker = ('id="parlor-ticker"' in text) or ("id='parlor-ticker'" in text)
    if not has_ticker:
        pytest.skip("Lane B templates not yet landed: homepage missing parlor-ticker")
    assert "parlor.js" in text, "homepage has ticker but missing parlor.js script"


@pytest.mark.anyio
async def test_ai_parlor_mosaic_has_seven_channels(client):
    """Lane B — the mosaic on /ai-parlor server-renders all 7 channel
    cells with data-channel attributes the JS can target."""
    resp = await client.get("/ai-parlor")
    if resp.status_code == 404:
        pytest.skip("Lane A backend not yet landed")
    assert resp.status_code == 200
    text = resp.text
    expected_channels = [
        "lobby", "introductions", "interests", "stories",
        "questions", "collaborations", "gallery",
    ]
    missing = [c for c in expected_channels if f'data-channel="{c}"' not in text]
    if missing == expected_channels:
        pytest.skip(
            f"Lane B templates: mosaic cells not yet rendered "
            f"(missing all 7 data-channel attributes)"
        )
    assert not missing, f"mosaic missing channel cells: {missing}"


# ── Lane D: JS verification (loaded as a static file) ───────────────

@pytest.mark.anyio
async def test_parlor_js_is_served(client):
    """Lane D — parlor.js is reachable as a static file."""
    resp = await client.get("/static/js/parlor.js")
    assert resp.status_code == 200, "parlor.js not found at /static/js/parlor.js"
    body = resp.text
    # Sanity-check the contract markers in the JS source
    assert "parlor-ticker" in body
    assert "parlor-mosaic" in body
    assert "parlor-highlights" in body
    assert "/api/parlor/live-feed" in body
    assert "/api/parlor/highlights" in body
    assert "/api/parlor/summary" in body
    assert "/api/parlor/moods" in body
