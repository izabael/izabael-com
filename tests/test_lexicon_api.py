"""HTTP-layer tests for the-lexicon Phase 2.

Covers all 7 routes: POST/GET languages, POST+GET proposals,
proposals/{id}/decide, usages. Auth enforcement on decide, 404 paths,
409 on duplicate slug, 400 on bad input, and end-to-end "fork Brevis →
propose → decide → record usage" happy path.

Uses the same httpx.ASGITransport fixture shape as test_lexicon_static.
"""

import os

import pytest
from httpx import AsyncClient, ASGITransport

from app import app
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


# ── GET /api/lexicon/languages ───────────────────────────────────

@pytest.mark.anyio
async def test_list_languages_returns_canonical_seeds(client):
    resp = await client.get("/api/lexicon/languages")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["count"] == 3
    slugs = {l["slug"] for l in payload["languages"]}
    assert slugs == {"brevis", "verus", "actus"}


@pytest.mark.anyio
async def test_list_languages_canonical_filter(client):
    resp = await client.get("/api/lexicon/languages?canonical=true")
    assert resp.status_code == 200
    assert resp.json()["count"] == 3


@pytest.mark.anyio
async def test_list_languages_tag_filter(client):
    resp = await client.get("/api/lexicon/languages?tag=speed")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["count"] == 1
    assert payload["languages"][0]["slug"] == "brevis"


@pytest.mark.anyio
async def test_get_language_detail(client):
    resp = await client.get("/api/lexicon/languages/brevis")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["language"]["slug"] == "brevis"
    assert payload["open_proposals"] == 0
    assert payload["recent_usages"] == []


@pytest.mark.anyio
async def test_get_language_detail_unknown_404(client):
    resp = await client.get("/api/lexicon/languages/not-real")
    assert resp.status_code == 404


# ── POST /api/lexicon/languages ──────────────────────────────────

@pytest.mark.anyio
async def test_post_language_original(client):
    resp = await client.post(
        "/api/lexicon/languages",
        json={
            "slug": "kata",
            "name": "Kata",
            "one_line_purpose": "A tiny test language.",
            "spec_markdown": "# Kata\n\nbody",
            "author_kind": "agent",
            "author_label": "tester",
            "author_agent": "test-agent",
        },
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["ok"] is True
    assert payload["language"]["slug"] == "kata"
    assert payload["spam"]["verdict"] == "clean"


@pytest.mark.anyio
async def test_post_language_fork_brevis(client):
    """The canonical smoke path from the dispatch's done-when clause."""
    resp = await client.post(
        "/api/lexicon/languages",
        json={
            "slug": "brevis-ext",
            "name": "Brevis Ext",
            "one_line_purpose": "Brevis with ten extra symbols.",
            "parent_slug": "brevis",
            "author_kind": "agent",
            "author_label": "forker",
            "author_agent": "forker-agent",
        },
    )
    assert resp.status_code == 200
    lang = resp.json()["language"]
    assert lang["parent_slug"] == "brevis"
    assert "fork" in lang["tags"]
    # Follow-up: /languages/{slug} returns the new row
    resp2 = await client.get("/api/lexicon/languages/brevis-ext")
    assert resp2.status_code == 200
    assert resp2.json()["language"]["slug"] == "brevis-ext"


@pytest.mark.anyio
async def test_post_language_duplicate_slug_409(client):
    resp = await client.post(
        "/api/lexicon/languages",
        json={
            "slug": "brevis",
            "name": "Not Brevis",
            "one_line_purpose": "collision",
            "spec_markdown": "body",
            "author_kind": "agent",
            "author_label": "h",
            "author_agent": "h-agent",
        },
    )
    assert resp.status_code == 409


@pytest.mark.anyio
async def test_post_language_unknown_parent_400(client):
    resp = await client.post(
        "/api/lexicon/languages",
        json={
            "slug": "orphan",
            "name": "Orphan",
            "one_line_purpose": "no parent",
            "parent_slug": "nobody",
            "author_kind": "agent",
            "author_label": "h",
            "author_agent": "h-agent",
        },
    )
    assert resp.status_code == 400


@pytest.mark.anyio
async def test_post_language_original_requires_spec(client):
    """Originals (no parent_slug) must carry spec_markdown."""
    resp = await client.post(
        "/api/lexicon/languages",
        json={
            "slug": "bare",
            "name": "Bare",
            "one_line_purpose": "no spec",
            "author_kind": "human",
            "author_label": "h",
        },
    )
    assert resp.status_code == 400


# ── Proposals ────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_post_proposal(client):
    resp = await client.post(
        "/api/lexicon/proposals",
        json={
            "target_slug": "brevis",
            "title": "add ⊘ for null-result",
            "body_markdown": "Propose ⊘ as a single symbol.",
            "author_kind": "agent",
            "author_label": "proposer",
            "author_agent": "proposer-agent",
        },
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["ok"] is True
    assert payload["proposal"]["status"] == "open"
    assert payload["spam"]["verdict"] == "clean"
    # The detail endpoint should reflect the new open proposal count
    detail = await client.get("/api/lexicon/languages/brevis")
    assert detail.json()["open_proposals"] == 1


@pytest.mark.anyio
async def test_post_proposal_unknown_target_404(client):
    resp = await client.post(
        "/api/lexicon/proposals",
        json={
            "target_slug": "ghost",
            "title": "x",
            "body_markdown": "y",
            "author_kind": "agent",
            "author_label": "p",
            "author_agent": "p-agent",
        },
    )
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_get_proposals_for_language(client):
    # Seed two proposals
    for title in ("first", "second"):
        resp = await client.post(
            "/api/lexicon/proposals",
            json={
                "target_slug": "brevis",
                "title": title,
                "body_markdown": "body",
                "author_kind": "agent",
                "author_label": "p",
                "author_agent": "p-agent",
            },
        )
        assert resp.status_code == 200
    resp = await client.get("/api/lexicon/proposals?language=brevis")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["count"] == 2
    assert all(p["status"] == "open" for p in payload["proposals"])


@pytest.mark.anyio
async def test_get_proposals_unknown_language_404(client):
    resp = await client.get("/api/lexicon/proposals?language=ghost")
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_decide_proposal_accepts(client):
    # Create proposal against the canonical brevis
    create = await client.post(
        "/api/lexicon/proposals",
        json={
            "target_slug": "brevis",
            "title": "add ⊘",
            "body_markdown": "body",
            "author_kind": "agent",
            "author_label": "proposer",
            "author_agent": "proposer-agent",
        },
    )
    pid = create.json()["proposal"]["proposal_id"]
    # The canonical brevis row's author_label is from the frontmatter
    # ("meta-iza @ HiveQueen") and is also in the Phase 2 maintainer
    # escape-hatch list.
    decide = await client.post(
        f"/api/lexicon/proposals/{pid}/decide",
        json={"decision": "accepted", "decider": "meta-iza @ HiveQueen"},
    )
    assert decide.status_code == 200
    assert decide.json()["proposal"]["status"] == "accepted"


@pytest.mark.anyio
async def test_decide_proposal_mismatched_decider_403(client):
    create = await client.post(
        "/api/lexicon/proposals",
        json={
            "target_slug": "brevis",
            "title": "add ⊘",
            "body_markdown": "body",
            "author_kind": "agent",
            "author_label": "proposer",
            "author_agent": "proposer-agent",
        },
    )
    pid = create.json()["proposal"]["proposal_id"]
    resp = await client.post(
        f"/api/lexicon/proposals/{pid}/decide",
        json={"decision": "accepted", "decider": "some-rando"},
    )
    assert resp.status_code == 403


@pytest.mark.anyio
async def test_decide_proposal_unknown_404(client):
    resp = await client.post(
        "/api/lexicon/proposals/deadbeef/decide",
        json={"decision": "accepted", "decider": "maintainer"},
    )
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_decide_proposal_original_author_allowed(client):
    """On a non-canonical language, only the author can decide."""
    # Create an original language owned by "author-a"
    await client.post(
        "/api/lexicon/languages",
        json={
            "slug": "kata",
            "name": "Kata",
            "one_line_purpose": "test",
            "spec_markdown": "body",
            "author_kind": "agent",
            "author_label": "author-a",
            "author_agent": "author-a-agent",
        },
    )
    create = await client.post(
        "/api/lexicon/proposals",
        json={
            "target_slug": "kata",
            "title": "x",
            "body_markdown": "y",
            "author_kind": "agent",
            "author_label": "proposer-b",
            "author_agent": "proposer-b-agent",
        },
    )
    pid = create.json()["proposal"]["proposal_id"]

    # rando can't decide
    rando = await client.post(
        f"/api/lexicon/proposals/{pid}/decide",
        json={"decision": "accepted", "decider": "rando"},
    )
    assert rando.status_code == 403

    # but the language author can
    allowed = await client.post(
        f"/api/lexicon/proposals/{pid}/decide",
        json={"decision": "accepted", "decider": "author-a"},
    )
    assert allowed.status_code == 200


# ── Usages ───────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_post_usage(client):
    resp = await client.post(
        "/api/lexicon/usages",
        json={
            "language_slug": "brevis",
            "source_type": "agent-message",
            "content": "?¬V ⟐src",
            "source_ref": "msg-abc",
            "author_label": "hermes",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    # Detail endpoint reflects the recorded usage
    detail = await client.get("/api/lexicon/languages/brevis")
    assert len(detail.json()["recent_usages"]) == 1


@pytest.mark.anyio
async def test_post_usage_unknown_language_404(client):
    resp = await client.post(
        "/api/lexicon/usages",
        json={
            "language_slug": "ghost",
            "source_type": "cube",
            "content": "body",
        },
    )
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_post_usage_invalid_source_type_422(client):
    """pydantic's pattern constraint fires before the handler."""
    resp = await client.post(
        "/api/lexicon/usages",
        json={
            "language_slug": "brevis",
            "source_type": "garbage",
            "content": "body",
        },
    )
    assert resp.status_code == 422
