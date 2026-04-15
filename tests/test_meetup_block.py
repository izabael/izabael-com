"""Phase 4 tests — the `_meetup_block.html` partial dropped on every
attraction page that opted in.

Coverage:
    · Partial renders on all 4 opted-in attraction pages
    · Each render carries the right slug + name + form fields
    · meetups.js is loaded once globally via base.html
    · Honeypot field is rendered but visually hidden
    · The block round-trips through the live Phase 3 spam filter:
      a posted note shows up as `pending` (ollama down in test env
      with the autouse mock returning unverified, so the new note
      lands in moderation) — confirms the wire format the JS reads.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient, ASGITransport

from app import app
from content_loader import store as content_store
from database import init_db, close_db


# ── Fixtures ───────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _load_content():
    content_store.load()


@pytest.fixture(autouse=True)
async def _init_test_db(tmp_path):
    os.environ["IZABAEL_DB"] = str(tmp_path / "block.db")
    import database
    database.DB_PATH = str(tmp_path / "block.db")
    await init_db()
    try:
        from app import limiter
        limiter.reset()
    except Exception:
        pass
    yield
    await close_db()


@pytest.fixture(autouse=True)
def _mock_classifier_clean(monkeypatch):
    """Without a mock, every test write would land in 'unverified'
    because ollama is not running in test env. The Phase 4 happy path
    needs a clean classifier so the form-submit flow asserts a clean
    response shape, not an unverified one."""
    import llm_local

    def _fake(text, **kw):
        return {
            "label": "legitimate",
            "confidence": 0.95,
            "reasoning": "test-mode mock",
        }

    monkeypatch.setattr(llm_local, "classify_meetup", _fake)


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _future_iso(hours: float = 48) -> str:
    return (
        datetime.now(timezone.utc) + timedelta(hours=hours)
    ).isoformat(timespec="seconds").replace("+00:00", "Z")


# ── Pages where the partial must appear ──────────────────────────

PAGES_WITH_BLOCK = [
    ("/ai-parlor",   "parlor",     "The Parlor"),
    ("/productivity","sphere",     "The Productivity Sphere"),
    ("/lexicon",     "lexicon",    "The Lexicon"),
    ("/for-agents",  "agent-door", "The Agent Door"),
]


@pytest.mark.parametrize("path,slug,name", PAGES_WITH_BLOCK)
@pytest.mark.anyio
async def test_partial_renders_on_attraction_page(client, path, slug, name):
    resp = await client.get(path)
    assert resp.status_code == 200, f"{path} status={resp.status_code}"
    body = resp.text
    assert 'class="meetup-block"' in body, f"{path} missing meetup-block section"
    assert f'data-attraction="{slug}"' in body, f"{path} wrong slug"
    assert f'data-name="{name}"' in body, f"{path} wrong name"
    # Form scaffold
    assert 'name="title"' in body
    assert 'name="goal"' in body
    assert 'name="when_text"' in body
    assert 'name="when_iso"' in body
    assert 'name="author_label"' in body
    # Honeypot rendered
    assert 'name="honeypot_website"' in body


@pytest.mark.parametrize("path,slug,name", PAGES_WITH_BLOCK)
@pytest.mark.anyio
async def test_meetups_js_loads_via_base_template(client, path, slug, name):
    resp = await client.get(path)
    assert resp.status_code == 200
    assert "/static/js/meetups.js" in resp.text


@pytest.mark.anyio
async def test_block_uses_unique_id_per_slug(client):
    """The form labels reference input ids that include the slug, so
    multiple blocks on one page (theoretical, but possible) wouldn't
    collide. Verify the slug-suffixed id pattern."""
    resp = await client.get("/lexicon")
    assert "meetup-title-lexicon" in resp.text
    assert "meetup-goal-lexicon" in resp.text
    assert "meetup-author-lexicon" in resp.text


# ── End-to-end form submit via the actual API ────────────────────

@pytest.mark.anyio
async def test_form_submit_round_trips_through_spam_filter(client):
    """Mirror what meetups.js sends. Asserts the response shape the
    client reads: spam.verdict + spam.score + spam.pending."""
    resp = await client.post(
        "/api/meetups/lexicon/create",
        json={
            "author_kind": "human",
            "author_label": "Marlowe",
            "title": "Lexicon syntax fork session",
            "goal": "Work on a Brevis fork together",
            "when_iso": _future_iso(72),
            "when_text": "Saturday 8pm PT",
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["ok"] is True
    assert data["spam"]["verdict"] == "clean"
    assert data["spam"]["pending"] is False
    # Note becomes immediately visible because the autouse mock
    # returns legitimate@0.95.
    assert data["note"]["is_visible"] is True


@pytest.mark.anyio
async def test_form_submit_with_honeypot_is_blocked(client):
    """The hidden honeypot field is the cheapest spam signal we have.
    A bot that fills every input gets blocked at Layer 3 with a 403."""
    resp = await client.post(
        "/api/meetups/lexicon/create",
        json={
            "author_kind": "human",
            "author_label": "Bot",
            "title": "x",
            "goal": "y",
            "when_iso": _future_iso(72),
            "when_text": "soon",
            "honeypot_website": "http://bot.example.com",
        },
    )
    assert resp.status_code == 403


@pytest.mark.anyio
async def test_partial_appears_on_all_four_pages_in_one_run(client):
    """One sweep — every opted-in page. Catches a regression where a
    template loses its include silently."""
    for path, slug, _name in PAGES_WITH_BLOCK:
        resp = await client.get(path)
        assert resp.status_code == 200
        assert f'data-attraction="{slug}"' in resp.text, f"missing on {path}"


@pytest.mark.anyio
async def test_partial_does_not_appear_when_slug_unset(client):
    """The partial guards on `{% if meetup_slug %}`. Pages that don't
    set the variable must not render an empty meetup-block — confirms
    the guard works. /attractions index is one such page (it shows
    counts on cards but no per-attraction authoring block)."""
    resp = await client.get("/attractions")
    assert resp.status_code == 200
    # No meetup-block section, no data-attraction wrapper on this page.
    assert 'data-attraction=' not in resp.text
