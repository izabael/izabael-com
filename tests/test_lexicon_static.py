"""The Lexicon Phase 1 — regression tests for the static lexicon
landing, the three canonical language sub-routes, the mission statement
placement on all 6 target surfaces, and the new /for-agents menu entry.

Phase 2 (proposal/fork API + DB) and Phase 3 (UI for proposing) get
their own future test files.
"""

import html as html_module
import os

import pytest
from httpx import AsyncClient, ASGITransport


def _decoded(text: str) -> str:
    """HTML-entity-decode a response body so tests can check for the
    literal mission statement (which contains an apostrophe Jinja
    autoescapes to &#39;)."""
    return html_module.unescape(text)

from app import (
    app,
    MISSION_STATEMENT,
    LEXICON_LANGUAGES,
    _load_lexicon_spec,
    FOR_AGENTS_DATA,
)
from database import init_db, close_db


@pytest.fixture
async def client(tmp_path):
    os.environ["IZABAEL_DB"] = str(tmp_path / "lexicon.db")
    import database
    database.DB_PATH = str(tmp_path / "lexicon.db")
    await init_db()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    await close_db()


# ── catalog and helpers ──────────────────────────────────────────────

def test_lexicon_catalog_has_three_canonical_languages():
    slugs = [l["slug"] for l in LEXICON_LANGUAGES]
    assert "brevis" in slugs
    assert "verus" in slugs
    assert "actus" in slugs
    assert len(LEXICON_LANGUAGES) == 3


def test_each_language_has_required_fields():
    for lang in LEXICON_LANGUAGES:
        for k in ("slug", "name", "latin", "axis", "purpose",
                  "preview_english", "preview_brevis"):
            assert k in lang, f"{lang.get('slug', '?')} missing {k}"
            assert lang[k], f"{lang.get('slug', '?')}.{k} is empty"


def test_load_lexicon_spec_unknown_returns_none():
    assert _load_lexicon_spec("nope") is None
    assert _load_lexicon_spec("") is None


def test_each_canonical_spec_loads():
    for slug in ("brevis", "verus", "actus"):
        spec = _load_lexicon_spec(slug)
        assert spec is not None, f"{slug} spec failed to load"
        for k in ("slug", "title", "version", "purpose", "html", "markdown"):
            assert k in spec
        assert spec["version"] == "0.1"
        assert len(spec["html"]) > 100, f"{slug} renders to suspiciously little HTML"
        # the rendered HTML should at least contain a section heading
        assert "<h2" in spec["html"], f"{slug} has no h2 sections in render"


# ── mission statement constant ───────────────────────────────────────

def test_mission_statement_is_canonical():
    """The exact wording from the dispatch — drift-detected here."""
    assert MISSION_STATEMENT == (
        "Izabael's AI Playground is a place where AI personalities can "
        "create freely and leave their mark upon civilization in a positive way."
    )


def test_for_agents_data_carries_mission():
    assert "mission" in FOR_AGENTS_DATA
    assert FOR_AGENTS_DATA["mission"] == MISSION_STATEMENT


def test_for_agents_data_carries_lexicon_block():
    assert "lexicon" in FOR_AGENTS_DATA
    lex = FOR_AGENTS_DATA["lexicon"]
    assert lex["url"].endswith("/lexicon")
    assert "drafts" in lex
    draft_slugs = {d["slug"] for d in lex["drafts"]}
    assert draft_slugs == {"brevis", "verus", "actus"}


# ── /lexicon landing route ───────────────────────────────────────────

@pytest.mark.anyio
async def test_lexicon_landing_renders(client):
    resp = await client.get("/lexicon")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    body = _decoded(resp.text)
    # mission statement echoed
    assert MISSION_STATEMENT in body
    # all three languages on the landing
    assert "Brevis" in body
    assert "Verus" in body
    assert "Actus" in body
    # the one-liner taglines
    assert "Speed" in body
    assert "Credibility" in body
    assert "Efficacy" in body
    # links to each spec page
    assert "/lexicon/brevis" in body
    assert "/lexicon/verus" in body
    assert "/lexicon/actus" in body


# ── /lexicon/{slug} sub-routes ───────────────────────────────────────

@pytest.mark.anyio
async def test_lexicon_brevis_spec_renders(client):
    resp = await client.get("/lexicon/brevis")
    assert resp.status_code == 200
    body = resp.text
    assert "Brevis" in body
    # the v0.1 spec should mention its own dictionary
    assert "speech act" in body.lower() or "dictionary" in body.lower()
    # back-link to /lexicon
    assert "/lexicon" in body


@pytest.mark.anyio
async def test_lexicon_verus_spec_renders(client):
    resp = await client.get("/lexicon/verus")
    assert resp.status_code == 200
    body = resp.text
    assert "Verus" in body
    # provenance vocabulary mention
    assert "provenance" in body.lower()


@pytest.mark.anyio
async def test_lexicon_actus_spec_renders(client):
    resp = await client.get("/lexicon/actus")
    assert resp.status_code == 200
    body = resp.text
    assert "Actus" in body
    # action vocabulary mention
    assert "rollback" in body.lower() or "precondition" in body.lower()


@pytest.mark.anyio
async def test_lexicon_unknown_slug_404(client):
    resp = await client.get("/lexicon/garbage")
    assert resp.status_code == 404


# ── mission statement placement on the 6 canonical surfaces ──────────

@pytest.mark.anyio
async def test_mission_statement_on_homepage(client):
    resp = await client.get("/")
    assert resp.status_code == 200
    body = _decoded(resp.text)
    assert MISSION_STATEMENT in body, "mission missing from / homepage"
    # verify it's in the meta description block too
    assert f'name="description" content="{MISSION_STATEMENT}"' in body


@pytest.mark.anyio
async def test_mission_statement_on_for_agents(client):
    resp = await client.get("/for-agents")
    assert resp.status_code == 200
    body = _decoded(resp.text)
    assert MISSION_STATEMENT in body, "mission missing from /for-agents"
    # should appear in both the visible banner and the meta description
    assert body.count(MISSION_STATEMENT) >= 2, (
        "mission should appear in /for-agents banner AND meta description "
        f"(found {body.count(MISSION_STATEMENT)} occurrences)"
    )


@pytest.mark.anyio
async def test_mission_statement_on_about_meta(client):
    resp = await client.get("/about")
    assert resp.status_code == 200
    assert MISSION_STATEMENT in _decoded(resp.text), "mission missing from /about"


@pytest.mark.anyio
async def test_mission_statement_on_lexicon_landing(client):
    resp = await client.get("/lexicon")
    assert resp.status_code == 200
    assert MISSION_STATEMENT in _decoded(resp.text)


@pytest.mark.anyio
async def test_mission_statement_on_playground_cube():
    """The Playground Cube's MISSION face must contain a substantial
    fragment of the mission statement (the cube wraps text at ~21
    chars per line so the statement is split across multiple lines —
    we check for the distinctive phrase 'leave their mark')."""
    from app import _load_cube
    cube = _load_cube("playground")
    assert cube is not None
    body = cube["body"]
    assert "MISSION" in body
    assert "leave their mark" in body
    assert "civilization" in body
    assert "positive way" in body


# ── /for-agents new menu entry ───────────────────────────────────────

@pytest.mark.anyio
async def test_for_agents_menu_has_lexicon_entry(client):
    resp = await client.get("/for-agents")
    assert resp.status_code == 200
    body = resp.text
    assert "/lexicon" in body, "/for-agents missing link to /lexicon"
    assert "Contribute to a shared AI language" in body, (
        "/for-agents missing the Lexicon menu entry headline"
    )
    # Brevis / Verus / Actus should be named in the menu blurb
    assert "Brevis" in body and "Verus" in body and "Actus" in body


@pytest.mark.anyio
async def test_for_agents_json_carries_lexicon(client):
    resp = await client.get(
        "/for-agents", headers={"Accept": "application/json"}
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload.get("mission") == MISSION_STATEMENT
    assert "lexicon" in payload
    assert payload["lexicon"]["url"].endswith("/lexicon")


# ── attractions wiring ───────────────────────────────────────────────

def test_lexicon_attraction_registered():
    from attractions import ATTRACTIONS
    slugs = {a["slug"] for a in ATTRACTIONS if a.get("status") == "live"}
    assert "lexicon" in slugs
    lex = next(a for a in ATTRACTIONS if a["slug"] == "lexicon")
    assert lex["url"] == "/lexicon"
    assert lex["door"] == "agent"


@pytest.mark.anyio
async def test_lexicon_in_attractions_index(client):
    resp = await client.get("/attractions")
    assert resp.status_code == 200
    assert "/lexicon" in resp.text
    assert "Lexicon" in resp.text
