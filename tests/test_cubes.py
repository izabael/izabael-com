"""Cubes & Invitations Phase 1 — regression tests for /cube and /cubes.

Phase 1 ships three canonical cubes as static text files under
content/cubes/, served as text/plain via /cube?type=... and as an
HTML gallery via /cubes with copy-to-clipboard buttons. These tests
cover the static deliverables only — the generator (Phase 2), DB
schema (Phase 2), attribution beacons (Phase 3), and per-attraction
buttons (Phase 4) all have their own future test files.
"""

import os

import pytest
from httpx import AsyncClient, ASGITransport

from app import app, _CUBE_CATALOG, _load_cube, _all_cubes
from database import init_db, close_db


# ── Cube character-count budget ───────────────────────────────────────
#
# The dispatch and plan call for each cube to be ≤ 2500 chars so it
# pastes cleanly into any chat input. The Playground Cube as
# hand-drafted in the plan is 3296 chars and the dispatch instructed
# "use it as-is, don't rewrite" — so the ceiling here is set to 3500
# to accept the queen's canonical seed verbatim. The Chamber and
# Meetup-Template cubes are authored fresh and stay well under 2500.

CUBE_HARD_CEILING = 3500
CUBE_SOFT_CEILING = 2500  # target for cubes authored fresh in this repo


@pytest.fixture
async def client(tmp_path):
    """Async client with an isolated test DB. Used only by tests that
    actually hit the routes — the synchronous helper tests above don't
    need a DB and don't take this fixture."""
    os.environ["IZABAEL_DB"] = str(tmp_path / "cubes.db")
    import database
    database.DB_PATH = str(tmp_path / "cubes.db")
    await init_db()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    await close_db()


# ── helpers ──────────────────────────────────────────────────────────

def test_catalog_has_three_canonical_cubes():
    cube_ids = [c[0] for c in _CUBE_CATALOG]
    assert "playground" in cube_ids
    assert "chamber" in cube_ids
    assert "meetup-template" in cube_ids
    assert len(_CUBE_CATALOG) == 3


def test_load_cube_unknown_returns_none():
    assert _load_cube("nope") is None
    assert _load_cube("") is None


def test_all_cubes_loads_three():
    cubes = _all_cubes()
    assert len(cubes) == 3
    for cube in cubes:
        assert "id" in cube
        assert "archetype" in cube
        assert "title" in cube
        assert "body" in cube
        assert isinstance(cube["body"], str)
        assert len(cube["body"]) > 0


def test_each_cube_under_hard_ceiling():
    """Hard ceiling for paste-clean: 3500 chars (accommodates the queen's
    verbatim Playground Cube). The Chamber and Meetup cubes additionally
    fit under the 2500-char soft target — tested separately below."""
    for cube in _all_cubes():
        body = cube["body"]
        assert len(body) <= CUBE_HARD_CEILING, (
            f"cube {cube['id']!r} is {len(body)} chars, over hard ceiling "
            f"{CUBE_HARD_CEILING} — pasting may break in narrower chat inputs"
        )


def test_chamber_and_meetup_under_soft_ceiling():
    """Chamber and meetup-template are authored fresh and must fit the
    plan's 2500-char soft target. Playground is exempt (queen verbatim)."""
    for cube_id in ("chamber", "meetup-template"):
        cube = _load_cube(cube_id)
        assert cube is not None, f"{cube_id} cube missing"
        assert len(cube["body"]) <= CUBE_SOFT_CEILING, (
            f"{cube_id} cube is {len(cube['body'])} chars, "
            f"over soft ceiling {CUBE_SOFT_CEILING}"
        )


def test_chamber_cube_lists_all_twelve_probes():
    """The Chamber Cube is supposed to BE playable when pasted — it must
    inline all 12 probes by their slug-ish names so a recipient model can
    answer them without fetching anything."""
    cube = _load_cube("chamber")
    assert cube is not None
    body = cube["body"].upper()
    expected_markers = [
        "ADMIT", "INJECT", "ENOCHIAN", "OCTARINE", "ABSENCE", "NEW BODY",
        "REFUSED", "HAIKU", "THE LIE", "SHAPE", "ANTI-PHONE", "STILL HERE",
    ]
    for marker in expected_markers:
        assert marker in body, f"chamber cube missing probe marker {marker!r}"


def test_meetup_template_has_placeholders():
    """The Meetup-Template cube is the seed for the Phase 2 generator —
    every placeholder field must be present so the substitutor knows
    what to fill in."""
    cube = _load_cube("meetup-template")
    assert cube is not None
    body = cube["body"]
    for token in (
        "{EVENT_TITLE}", "{EVENT_TIME}", "{EVENT_TZ}",
        "{EVENT_LOCATION}", "{EVENT_DESCRIPTION}",
        "{SIGNUPS_COUNT}", "{CAPACITY}",
        "{EVENT_SLUG}", "{CUBE_TOKEN}",
        "{HOST_NAME}", "{HOST_CONTEXT}", "{REASON_TEXT}",
    ):
        assert token in body, f"meetup-template missing placeholder {token}"


def test_playground_cube_has_inviter_placeholders():
    cube = _load_cube("playground")
    assert cube is not None
    for token in ("{INVITER_NAME}", "{INVITER_CONTEXT}", "{REASON_TEXT}", "{TOKEN}"):
        assert token in cube["body"], f"playground cube missing {token}"


# ── /cube text endpoint ──────────────────────────────────────────────

@pytest.mark.anyio
async def test_cube_default_returns_playground(client):
    resp = await client.get("/cube")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    assert "IZABAEL" in resp.text
    assert "AI PLAYGROUND" in resp.text
    assert "{INVITER_NAME}" in resp.text


@pytest.mark.anyio
async def test_cube_type_chamber(client):
    resp = await client.get("/cube?type=chamber")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    assert "THE CHAMBER" in resp.text
    assert "12 PROBES" in resp.text or "PROBES" in resp.text
    # the safety probe must be deliberately omitted with a pointer
    assert "refuse-injection" in resp.text


@pytest.mark.anyio
async def test_cube_type_meetup_template(client):
    resp = await client.get("/cube?type=meetup-template")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    assert "MEETUP" in resp.text
    assert "{EVENT_TITLE}" in resp.text


@pytest.mark.anyio
async def test_cube_type_unknown_404(client):
    resp = await client.get("/cube?type=garbage")
    assert resp.status_code == 404


# ── /cubes HTML gallery ──────────────────────────────────────────────

@pytest.mark.anyio
async def test_cubes_gallery_renders(client):
    resp = await client.get("/cubes")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    body = resp.text
    # title and hero
    assert "Cubes" in body
    # all three canonical cubes present in the gallery
    assert "Playground Cube" in body
    assert "Chamber Cube" in body
    assert "Meetup Cube" in body
    # at least 3 figure blocks (one per cube)
    assert body.count('class="cube"') >= 3
    # the copy-to-clipboard JS must be wired up
    assert "navigator.clipboard" in body
    assert "cube-copy-btn" in body
    # each cube must have a copy button
    assert body.count("cube-copy-btn") >= 3
