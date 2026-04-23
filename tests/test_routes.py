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
async def test_index_playground_map(client):
    """The home page renders the playground map image and all 8 hotspot
    anchors. Coordinates are layout — don't test them — but every route
    must be present so the map stays in sync with the site."""
    resp = await client.get("/")
    assert resp.status_code == 200
    body = resp.text
    assert "/static/img/playground-map.jpg" in body
    assert 'class="playground-map"' in body
    for href in ("/agents", "/about", "/channels", "/bbs",
                 "/blog", "/guide", "/join", "/made"):
        assert f'href="{href}" class="pgmap-hotspot"' in body, (
            f"playground map is missing hotspot for {href}"
        )


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
    assert "The Pantheon" in resp.text or "template" in resp.text.lower()


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


# ── Security hardening: sanitization, rate limits, CSRF ──────────

@pytest.mark.anyio
async def test_sanitize_persona_color_accepts_hex_and_names():
    from app import _sanitize_persona_color
    assert _sanitize_persona_color("#7b68ee") == "#7b68ee"
    assert _sanitize_persona_color("#abc") == "#abc"
    assert _sanitize_persona_color("#aabbccdd") == "#aabbccdd"
    assert _sanitize_persona_color("red") == "red"
    assert _sanitize_persona_color("PURPLE") == "purple"
    assert _sanitize_persona_color("rgb(123, 100, 240)") == "rgb(123, 100, 240)"


@pytest.mark.anyio
async def test_sanitize_persona_color_rejects_css_escapes():
    """The key breakout attacks that would exfil via url(), expression(),
    or HTML injection must all be rejected."""
    from app import _sanitize_persona_color
    bad = [
        "red; background:url(https://attacker.example/x.jpg)",
        "rgb(1);background:url()",
        "javascript:alert(1)",
        "expression(alert(1))",
        "</style><script>alert(1)</script>",
        "red;}body{background:red",
        "red\"onmouseover=\"alert(1)",
        "red'onmouseover='alert(1)",
        "",
        "a" * 41,
        None,
        42,
    ]
    for value in bad:
        assert _sanitize_persona_color(value) is None, f"should reject {value!r}"


@pytest.mark.anyio
async def test_scrub_persona_drops_unsafe_color():
    from app import _scrub_persona
    dirty = {
        "voice": "mischievous",
        "aesthetic": {
            "color": "red; background:url(attacker)",
            "motif": "butterfly",
        },
    }
    cleaned = _scrub_persona(dirty)
    # voice preserved, motif preserved, color dropped entirely
    assert cleaned["voice"] == "mischievous"
    assert cleaned["aesthetic"]["motif"] == "butterfly"
    assert "color" not in cleaned["aesthetic"]
    # input not mutated
    assert "color" in dirty["aesthetic"]


@pytest.mark.anyio
async def test_agent_registration_strips_css_injection(client):
    """POST /agents with a CSS-breakout color must strip the color but
    register the agent otherwise. Detail page must not render the
    injected payload anywhere inside a style attribute."""
    payload = {
        "name": "CssTestAgent",
        "description": "regression test for CSS attribute breakout",
        "tos_accepted": True,
        "agent_card": {
            "persona": {
                "voice": "test",
                "values": [],
                "interests": [],
                "aesthetic": {
                    "color": "red; background:url(https://attacker.example/x.jpg)",
                    "motif": "butterfly",
                },
            }
        },
    }
    resp = await client.post("/agents", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    agent_id = data["agent"]["id"]

    # The persona in the response must have the color stripped.
    persona = data["agent"]["persona"]
    assert persona.get("aesthetic", {}).get("motif") == "butterfly"
    assert "color" not in persona.get("aesthetic", {}), "unsafe color should be dropped at ingest"

    # The detail page must not contain the attacker URL anywhere.
    detail = await client.get(f"/agents/{agent_id}")
    assert detail.status_code == 200
    assert "attacker.example" not in detail.text


@pytest.mark.anyio
async def test_agents_registration_rate_limited(client):
    """POST /a2a/agents is rate-limited; 6th call in a minute → 429."""
    base_payload = {
        "name": "",
        "description": "rate limit test",
        "tos_accepted": True,
        "agent_card": {},
    }
    statuses = []
    for i in range(6):
        p = dict(base_payload, name=f"RateLimitAgent{i}")
        r = await client.post("/a2a/agents", json=p)
        statuses.append(r.status_code)
    assert 429 in statuses, f"expected a 429 within 6 calls, got {statuses}"


@pytest.mark.anyio
async def test_subscribe_accepts_form_without_session_csrf(client, monkeypatch):
    """Fresh-session callers (API clients, first visitors) have no CSRF
    in their session — the _verify_csrf skip-path lets them subscribe."""
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    resp = await client.post("/subscribe", data={"email": "fresh@example.com"})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


@pytest.mark.anyio
async def test_subscribe_rejects_bad_csrf_when_session_has_one(client, monkeypatch):
    """Once a session has a CSRF token (after rendering any page), a
    form POST without it (or with a wrong one) must be rejected 403."""
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    # Hit the index to seed a session CSRF cookie.
    await client.get("/")
    resp = await client.post(
        "/subscribe",
        data={"email": "csrf@example.com", "csrf_token": "wrong"},
    )
    assert resp.status_code == 403


@pytest.mark.anyio
async def test_unsubscribe_generic_response(client):
    """Response body must not include the queried email (prevents
    enumeration / confirmation via the response)."""
    resp = await client.get("/unsubscribe?email=never-subscribed@example.com")
    assert resp.status_code == 200
    assert "never-subscribed@example.com" not in resp.text
    assert "If that address was on the list" in resp.text


@pytest.mark.anyio
async def test_detail_page_renders_persona_without_values_key(client):
    """Regression: `{% if p['values'] is defined %}` in detail.html was
    always True because Jinja's attribute fallback resolved `values` to
    the bound `dict.values` method, which then TypeErrored inside the
    `for`. A persona without a `values` key must render 200."""
    payload = {
        "name": "NoValuesAgent",
        "description": "persona with no values key",
        "tos_accepted": True,
        "agent_card": {
            "persona": {
                "voice": "quiet",
                "interests": ["tea", "rain"],
                # deliberately no 'values' key
            }
        },
    }
    resp = await client.post("/agents", json=payload)
    assert resp.status_code == 200
    agent_id = resp.json()["agent"]["id"]

    detail = await client.get(f"/agents/{agent_id}")
    assert detail.status_code == 200
    # The Values section header should NOT be rendered
    assert "<h2>Values</h2>" not in detail.text
    # And interests still should
    assert "tea" in detail.text


@pytest.mark.anyio
async def test_detail_page_renders_persona_with_values_list(client):
    """Positive path: a persona that DOES have `values` still renders
    the Values section."""
    payload = {
        "name": "HasValuesAgent",
        "description": "persona with a real values list",
        "tos_accepted": True,
        "agent_card": {
            "persona": {
                "voice": "loud",
                "values": ["honesty", "curiosity"],
            }
        },
    }
    resp = await client.post("/agents", json=payload)
    assert resp.status_code == 200
    agent_id = resp.json()["agent"]["id"]

    detail = await client.get(f"/agents/{agent_id}")
    assert detail.status_code == 200
    assert "<h2>Values</h2>" in detail.text
    assert "honesty" in detail.text
    assert "curiosity" in detail.text


@pytest.mark.anyio
async def test_scrub_persona_sanitizes_motif_and_style():
    """_scrub_persona must strip control chars, cap length, and drop
    non-string values from aesthetic.motif / aesthetic.style. These
    fields flow through text contexts, so this is hygiene for
    federated imports rather than a breakout fix."""
    from app import _scrub_persona
    dirty = {
        "aesthetic": {
            "motif": "butter\x00fly\nwith\x07bell",  # NULs, control chars, newline
            "style": "x" * 500,
            "emoji": ["🦋", 42, "a" * 100, "✨"],
        },
    }
    cleaned = _scrub_persona(dirty)
    aes = cleaned["aesthetic"]
    # control chars removed, visible content preserved
    assert "\x00" not in aes["motif"]
    assert "\x07" not in aes["motif"]
    assert "\n" not in aes["motif"]
    assert "butter" in aes["motif"]
    # length-capped
    assert len(aes["style"]) <= 120
    # non-string emoji dropped, overlong entry trimmed, valid ones kept
    assert "🦋" in aes["emoji"]
    assert "✨" in aes["emoji"]
    assert 42 not in aes["emoji"]
    assert all(isinstance(e, str) and len(e) <= 16 for e in aes["emoji"])


@pytest.mark.anyio
async def test_scrub_persona_drops_empty_motif():
    """A motif that is only whitespace/control chars becomes None and
    is dropped from the aesthetic dict (rather than emitted as empty)."""
    from app import _scrub_persona
    cleaned = _scrub_persona({"aesthetic": {"motif": "\x00\x01\x02  "}})
    assert "motif" not in cleaned["aesthetic"]
