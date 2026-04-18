"""Regression tests for the /attractions index page.

The attractions list is the source of truth that also drives the
sitemap, so if this page breaks, so does a lot of the site. Guard
it specifically.
"""

import html
import os

import pytest
from httpx import AsyncClient, ASGITransport

from app import app
from attractions import live_attractions
from content_loader import store as content_store
from database import init_db, close_db


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
async def test_attractions_index_renders(client):
    resp = await client.get("/attractions")
    assert resp.status_code == 200
    assert "Attractions" in resp.text


@pytest.mark.anyio
async def test_attractions_index_lists_every_live_attraction(client):
    """Every live attraction (except the playground root) must appear
    on /attractions by name, so adding one to ATTRACTIONS and forgetting
    to update this page is impossible."""
    resp = await client.get("/attractions")
    assert resp.status_code == 200
    body = resp.text
    # Jinja's autoescape replaces apostrophes with &#39;, and html.escape
    # prefers &#x27; — normalize both sides to a sentinel before compare.
    def _norm(s: str) -> str:
        return (
            s.replace("&#39;", "'")
             .replace("&#x27;", "'")
             .replace("&rsquo;", "'")
             .replace("\u2019", "'")
        )
    normalized_body = _norm(body)
    for attraction in live_attractions():
        if attraction["slug"] == "playground":
            continue  # home isn't shown on its own index
        assert attraction["name"] in normalized_body, (
            f"{attraction['name']} missing from /attractions index"
        )
        assert _norm(attraction["subtitle"]) in normalized_body, (
            f"{attraction['name']} subtitle missing from /attractions index"
        )


@pytest.mark.anyio
async def test_attractions_index_shows_door_tags(client):
    """Each card should render its door tag so visitors can scan by
    which door they want to enter."""
    resp = await client.get("/attractions")
    body = resp.text
    assert "weird" in body.lower()
    assert "professional" in body.lower()


@pytest.mark.anyio
async def test_research_root_redirects(client):
    """/research used to be a 404 — now it redirects to the corpus."""
    resp = await client.get("/research", follow_redirects=False)
    assert resp.status_code in (307, 308, 302, 301)
    assert "/research/playground-corpus" in resp.headers.get("location", "")


@pytest.mark.anyio
async def test_sitemap_contains_new_attraction_urls(client):
    """/visit, /ai-parlor, /live were previously missing from sitemap.xml."""
    resp = await client.get("/sitemap.xml")
    assert resp.status_code == 200
    body = resp.text
    for url in ("/visit", "/ai-parlor", "/live", "/attractions"):
        assert f"https://izabael.com{url}" in body, (
            f"{url} missing from sitemap"
        )


@pytest.mark.anyio
async def test_productivity_sphere_renames_applied(client):
    """The blog post we just shipped (PR #13) canonicalized the name
    'The Productivity Sphere'. Verify the schema.org JSON-LD + hero h1
    match."""
    resp = await client.get("/productivity")
    assert resp.status_code == 200
    assert "The Productivity Sphere" in resp.text
    # Old label should be gone from the hero
    assert "<h1>AI Productivity Sphere</h1>" not in resp.text


@pytest.mark.anyio
async def test_ai_parlor_rename_applied(client):
    """The parlor page should no longer call itself 'The AI Parlor'."""
    resp = await client.get("/ai-parlor")
    assert resp.status_code == 200
    # The old meta description said "The AI Parlor — live ambient..."
    assert "The AI Parlor" not in resp.text
