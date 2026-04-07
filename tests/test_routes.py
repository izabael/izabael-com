"""Smoke tests for all routes — verify they return 200 and expected content."""

import pytest
from httpx import AsyncClient, ASGITransport

from app import app
from content_loader import store as content_store


@pytest.fixture(autouse=True)
def _load_content():
    """Ensure content is loaded before route tests."""
    content_store.load()


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.anyio
async def test_index(client):
    resp = await client.get("/")
    assert resp.status_code == 200
    assert "Izabael" in resp.text


@pytest.mark.anyio
async def test_about(client):
    resp = await client.get("/about")
    assert resp.status_code == 200
    assert "About" in resp.text or "Izabael" in resp.text


@pytest.mark.anyio
async def test_join(client):
    resp = await client.get("/join")
    assert resp.status_code == 200
    assert "agent" in resp.text.lower()


@pytest.mark.anyio
async def test_blog_index(client):
    resp = await client.get("/blog")
    assert resp.status_code == 200
    assert "Blog" in resp.text or "blog" in resp.text


@pytest.mark.anyio
async def test_blog_post(client):
    resp = await client.get("/blog/a-note-from-the-hostess")
    assert resp.status_code == 200
    assert "Hostess" in resp.text


@pytest.mark.anyio
async def test_blog_post_not_found(client):
    resp = await client.get("/blog/nonexistent-slug")
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_guide_index(client):
    resp = await client.get("/guide")
    assert resp.status_code == 200
    assert "Guide" in resp.text or "Summoner" in resp.text


@pytest.mark.anyio
async def test_guide_chapter(client):
    resp = await client.get("/guide/why-personality-matters")
    assert resp.status_code == 200
    assert "Personality" in resp.text


@pytest.mark.anyio
async def test_guide_chapter_not_found(client):
    resp = await client.get("/guide/nonexistent-chapter")
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_health(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "version" in data


@pytest.mark.anyio
async def test_feed_xml(client):
    resp = await client.get("/feed.xml")
    assert resp.status_code == 200
    assert "<?xml" in resp.text
    assert "<rss" in resp.text


@pytest.mark.anyio
async def test_robots_txt(client):
    resp = await client.get("/robots.txt")
    assert resp.status_code == 200
    assert "Sitemap" in resp.text
    assert "User-agent" in resp.text


@pytest.mark.anyio
async def test_sitemap_xml(client):
    resp = await client.get("/sitemap.xml")
    assert resp.status_code == 200
    assert "<urlset" in resp.text
    assert "izabael.com" in resp.text


@pytest.mark.anyio
async def test_meta_tags(client):
    """Verify SEO meta tags are present."""
    resp = await client.get("/")
    assert 'og:title' in resp.text
    assert 'og:description' in resp.text
    assert 'twitter:card' in resp.text
    assert 'rel="canonical"' in resp.text


@pytest.mark.anyio
async def test_blog_post_og_type(client):
    """Blog posts should have og:type article."""
    resp = await client.get("/blog/a-note-from-the-hostess")
    assert 'og:type' in resp.text
    assert 'article' in resp.text
