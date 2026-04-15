"""Tests for /research/case-studies route + content_loader case_studies collection.

Covers:
  * ContentStore.case_studies loads real markdown files from content/case-studies/
  * case_study_by_slug() lookups work and respect drafts
  * /research/case-studies/ index route returns 200 and lists each published study
  * /research/case-studies/{slug} detail route returns 200 with rendered body
  * Unknown slug returns 404
  * The first case study (federation protocol) is listed and renders
"""
from __future__ import annotations

import os
import pytest
from httpx import AsyncClient, ASGITransport

from app import app
from content_loader import ContentStore, store as content_store
from database import init_db, close_db


FIRST_STUDY_SLUG = "2026-04-15-federation-protocol"


@pytest.fixture(autouse=True)
def _load_content():
    """Reload the in-memory content store so the case-studies collection
    reflects the current on-disk state."""
    content_store.load()


@pytest.fixture(autouse=True)
async def _init_test_db(tmp_path):
    os.environ["IZABAEL_DB"] = str(tmp_path / "case_studies.db")
    import database
    database.DB_PATH = str(tmp_path / "case_studies.db")
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


# ── ContentStore layer ──────────────────────────────────────────────
# These tests exercise the in-memory store only. The DB fixture is
# still autouse for the module because all async tests below need it;
# we mark these as anyio so the fixture resolves cleanly.

@pytest.mark.anyio
async def test_store_loads_case_studies():
    """Fresh ContentStore should pick up every non-draft markdown file in
    content/case-studies/ with frontmatter parsed and HTML rendered."""
    s = ContentStore()
    s.load()
    assert len(s.case_studies) >= 1, (
        "Expected at least 1 published case study in content/case-studies/"
    )
    for cs in s.case_studies:
        assert cs.slug, "slug missing"
        assert cs.title, "title missing"
        assert cs.html, "html rendering missing"
        assert not cs.draft


@pytest.mark.anyio
async def test_first_case_study_is_loaded():
    s = ContentStore()
    s.load()
    slugs = {cs.slug for cs in s.case_studies}
    assert FIRST_STUDY_SLUG in slugs, (
        f"Expected the federation protocol case study slug "
        f"{FIRST_STUDY_SLUG!r} in store.case_studies, got {sorted(slugs)!r}"
    )


@pytest.mark.anyio
async def test_case_study_by_slug_roundtrip():
    s = ContentStore()
    s.load()
    hit = s.case_study_by_slug(FIRST_STUDY_SLUG)
    assert hit is not None
    assert hit.slug == FIRST_STUDY_SLUG
    assert "federation" in hit.title.lower()
    assert s.case_study_by_slug("nonexistent-slug-xyz") is None


@pytest.mark.anyio
async def test_case_studies_sorted_newest_first():
    s = ContentStore()
    s.load()
    studies = s.case_studies
    for i in range(len(studies) - 1):
        a, b = studies[i].date, studies[i + 1].date
        if a and b:
            assert a >= b, "Case studies should be date-desc"


# ── Route layer ─────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_case_studies_index_route_returns_200(client):
    resp = await client.get("/research/case-studies/")
    assert resp.status_code == 200
    assert "Case Studies" in resp.text


@pytest.mark.anyio
async def test_case_studies_index_no_trailing_slash_also_200(client):
    resp = await client.get("/research/case-studies")
    assert resp.status_code == 200


@pytest.mark.anyio
async def test_case_studies_index_lists_first_study(client):
    resp = await client.get("/research/case-studies/")
    assert resp.status_code == 200
    assert FIRST_STUDY_SLUG in resp.text, (
        "Index page should link to the first case study by slug"
    )


@pytest.mark.anyio
async def test_case_study_detail_route_returns_200(client):
    resp = await client.get(f"/research/case-studies/{FIRST_STUDY_SLUG}")
    assert resp.status_code == 200
    assert "federation" in resp.text.lower()


@pytest.mark.anyio
async def test_case_study_detail_renders_html_body(client):
    resp = await client.get(f"/research/case-studies/{FIRST_STUDY_SLUG}")
    assert resp.status_code == 200
    # The markdown body should have become HTML — at minimum one <h2>.
    assert "<h2" in resp.text
    # And the seed message quote should be present.
    assert "Lobby" in resp.text


@pytest.mark.anyio
async def test_case_study_unknown_slug_returns_404(client):
    resp = await client.get("/research/case-studies/not-a-real-case-study")
    assert resp.status_code == 404
