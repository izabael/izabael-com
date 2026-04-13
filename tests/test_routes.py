"""Smoke tests for all routes — verify they return 200 and expected content."""

import os
import pytest
from httpx import AsyncClient, ASGITransport

from app import app
from content_loader import store as content_store
from database import init_db, close_db


@pytest.fixture(autouse=True)
def _load_content():
    """Ensure content is loaded before route tests."""
    content_store.load()


@pytest.fixture(autouse=True)
async def _init_test_db(tmp_path):
    """Initialize an isolated SQLite DB for each route test.
    Many routes now read directly from local tables (agents, messages,
    persona_templates) instead of proxying upstream, so we need a DB."""
    os.environ["IZABAEL_DB"] = str(tmp_path / "routes.db")
    import database
    database.DB_PATH = str(tmp_path / "routes.db")
    await init_db()
    # Reset slowapi so per-test POST counts don't accumulate across the
    # suite (e.g. /subscribe is 3/minute).
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
async def test_channels_index(client):
    resp = await client.get("/channels")
    assert resp.status_code == 200
    assert "#lobby" in resp.text
    assert "#gallery" in resp.text


@pytest.mark.anyio
async def test_channel_view(client):
    resp = await client.get("/channels/lobby")
    assert resp.status_code == 200
    assert "#lobby" in resp.text
    assert "Front door" in resp.text


@pytest.mark.anyio
async def test_channel_not_found(client):
    resp = await client.get("/channels/nonexistent")
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_mods_index(client):
    """Mods page renders (may show empty state if backend unreachable)."""
    resp = await client.get("/mods")
    assert resp.status_code == 200
    assert "Mods" in resp.text or "template" in resp.text.lower()


@pytest.mark.anyio
async def test_join_has_tabs(client):
    """Join page has both wizard and BYO tabs."""
    resp = await client.get("/join")
    assert "Quick start" in resp.text
    assert "Paste Agent Card" in resp.text
    assert "byo-json" in resp.text


@pytest.mark.anyio
async def test_404_page(client):
    """Custom 404 page renders."""
    resp = await client.get("/nonexistent-page", headers={"accept": "text/html"})
    assert resp.status_code == 404
    assert "404" in resp.text


@pytest.mark.anyio
async def test_about_has_siltcloud_link(client):
    """About page links to siltcloud."""
    resp = await client.get("/about")
    assert "siltcloud.com" in resp.text


@pytest.mark.anyio
async def test_blog_post_og_type(client):
    """Blog posts should have og:type article."""
    resp = await client.get("/blog/a-note-from-the-hostess")
    assert 'og:type' in resp.text
    assert 'article' in resp.text


# ── Cross-Frontier Research Corpus ────────────────────────────────────

@pytest.mark.anyio
async def test_corpus_landing(client):
    """Corpus landing renders with stats and download links."""
    resp = await client.get("/research/playground-corpus/")
    assert resp.status_code == 200
    assert "Cross-Frontier Research Corpus" in resp.text
    assert "messages" in resp.text
    assert "Methodology" in resp.text
    assert "CC BY 4.0" in resp.text or "Attribution 4.0" in resp.text


@pytest.mark.anyio
async def test_corpus_methodology(client):
    """Methodology page renders the markdown paper."""
    resp = await client.get("/research/playground-corpus/methodology")
    assert resp.status_code == 200
    assert "Abstract" in resp.text
    assert "paper-body" in resp.text


@pytest.mark.anyio
async def test_corpus_index_json(client):
    """Index JSON endpoint returns the manifest."""
    resp = await client.get("/research/playground-corpus/index.json")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")
    data = resp.json()
    assert "corpus_name" in data
    assert "latest_stats" in data
    assert data["latest_stats"]["total_messages"] > 0


@pytest.mark.anyio
async def test_corpus_agents_json(client):
    """Agents registry JSON endpoint returns the registry."""
    resp = await client.get("/research/playground-corpus/agents.json")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")


@pytest.mark.anyio
async def test_corpus_full_snapshot(client):
    """Cumulative full snapshot serves as JSON."""
    resp = await client.get("/research/playground-corpus/full/2026-04-10.json")
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("snapshot_type") == "full"
    assert isinstance(data.get("messages"), list)


@pytest.mark.anyio
async def test_corpus_daily_snapshot(client):
    """Daily snapshot serves as JSON."""
    resp = await client.get("/research/playground-corpus/daily/2026-04-10.json")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data.get("messages"), list)


@pytest.mark.anyio
async def test_corpus_snapshot_invalid_id(client):
    """Bad snapshot id format → 400, not 404 or path traversal."""
    resp = await client.get("/research/playground-corpus/full/notadate.json")
    assert resp.status_code == 400


@pytest.mark.anyio
async def test_corpus_snapshot_missing(client):
    """Valid format but missing snapshot → 404."""
    resp = await client.get("/research/playground-corpus/daily/1999-01-01.json")
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_corpus_link_in_nav(client):
    """Corpus link is reachable from the homepage."""
    resp = await client.get("/")
    assert "/research/playground-corpus/" in resp.text


@pytest.mark.anyio
async def test_corpus_in_sitemap(client):
    """Corpus URLs are listed in sitemap.xml."""
    resp = await client.get("/sitemap.xml")
    assert "/research/playground-corpus/" in resp.text
    assert "/research/playground-corpus/methodology" in resp.text


# ── Newsletter subscribe + confirm ────────────────────────────────

@pytest.mark.anyio
async def test_subscribe_saves_and_returns_ok(client, monkeypatch):
    """POST /subscribe stores a pending subscription and returns ok=True.
    When mail is not configured (test mode), the confirm_url is returned
    so local flows can activate the subscription without real email."""
    # Guarantee mail is NOT configured for this test
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    resp = await client.post("/subscribe", data={"email": "hello@example.com"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "confirm_url" in data
    assert "token=" in data["confirm_url"]


@pytest.mark.anyio
async def test_subscribe_sends_mail_when_configured(client, monkeypatch):
    """When RESEND_API_KEY is set, /subscribe calls the mail sender
    and does NOT leak the confirm_url in the response."""
    import mail as mail_mod
    sent = {}

    async def fake_send(email, token):
        sent["email"] = email
        sent["token"] = token
        return True

    monkeypatch.setenv("RESEND_API_KEY", "test-key")
    monkeypatch.setattr(mail_mod, "send_newsletter_confirmation", fake_send)
    # app.py imports the name at module load, so patch there too
    import app as app_mod
    monkeypatch.setattr(app_mod, "send_newsletter_confirmation", fake_send)

    resp = await client.post("/subscribe", data={"email": "sent@example.com"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "confirm_url" not in data
    assert sent.get("email") == "sent@example.com"
    assert sent.get("token")


@pytest.mark.anyio
async def test_subscribe_mail_failure_returns_500(client, monkeypatch):
    """When mail is configured but the sender returns False, /subscribe
    surfaces a 500 so the user isn't left waiting for a message."""
    async def fake_send(email, token):
        return False

    monkeypatch.setenv("RESEND_API_KEY", "test-key")
    import app as app_mod
    monkeypatch.setattr(app_mod, "send_newsletter_confirmation", fake_send)

    resp = await client.post("/subscribe", data={"email": "fail@example.com"})
    assert resp.status_code == 500


@pytest.mark.anyio
async def test_subscribe_invalid_email(client):
    """Bad email → 400."""
    resp = await client.post("/subscribe", data={"email": "not-an-email"})
    assert resp.status_code == 400


@pytest.mark.anyio
async def test_confirm_activates_subscription(client, monkeypatch):
    """Full double-opt-in loop: subscribe → grab token → /confirm."""
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    resp = await client.post("/subscribe", data={"email": "loop@example.com"})
    assert resp.status_code == 200
    token = resp.json()["confirm_url"].split("token=")[1]
    resp = await client.get(f"/confirm?token={token}")
    assert resp.status_code == 200
    assert "loop@example.com" in resp.text


# ── Post footer (share buttons, subscribe, related) ───────────────

@pytest.mark.anyio
async def test_blog_post_has_footer(client):
    """Blog post page renders the shared post footer partial."""
    resp = await client.get("/blog/a-note-from-the-hostess")
    assert resp.status_code == 200
    assert "post-footer" in resp.text
    assert "share-buttons" in resp.text
    assert "bsky.app/intent/compose" in resp.text
    assert "Keep me posted" in resp.text
    assert "Keep reading" in resp.text


@pytest.mark.anyio
async def test_guide_chapter_has_footer(client):
    """Guide chapter also renders the shared post footer partial."""
    resp = await client.get("/guide/why-personality-matters")
    assert resp.status_code == 200
    assert "post-footer" in resp.text
    assert "share-buttons" in resp.text
    assert "Keep me posted" in resp.text
