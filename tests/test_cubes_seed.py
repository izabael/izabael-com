"""Tests for cubes-and-invitations Phase 6 — hand-seeded inaugural
cubes under content/cubes/seed/. Each one must load, render clean
(no placeholder tokens left in the rendered text), contain a real
WHO SENT THIS footer, and carry a real URL back to izabael.com.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from httpx import AsyncClient, ASGITransport

import app as app_module
from app import app, _SEED_CUBE_CATALOG, _load_cube, _all_cubes


SEED_DIR = Path(app_module.__file__).resolve().parent / "content" / "cubes" / "seed"


# Shape expectations every seed cube must satisfy
_REQUIRED_SECTIONS = ("WHO SENT THIS",)
_REQUIRED_URL_PREFIX = "izabael.com"
_FORBIDDEN_PLACEHOLDERS = (
    "{INVITER_NAME}",
    "{INVITER_CONTEXT}",
    "{DATE}",
    "{REASON_TEXT}",
    "{TOKEN}",
    "{ATTRACTION_NAME}",
    "{ATTRACTION_URL}",
    "{EVENT_TITLE}",
    "{HOST_NAME}",
    "{CUBE_TOKEN}",
)


@pytest.mark.parametrize("cube_id,_arch,_title,fname", _SEED_CUBE_CATALOG)
def test_seed_cube_file_exists(cube_id, _arch, _title, fname):
    """Every catalog entry's file exists on disk."""
    path = Path(app_module.__file__).resolve().parent / "content" / "cubes" / fname
    assert path.exists(), f"missing seed cube file: {fname}"


@pytest.mark.parametrize("cube_id,_arch,_title,_fname", _SEED_CUBE_CATALOG)
def test_seed_cube_loads_via_load_cube(cube_id, _arch, _title, _fname):
    """_load_cube() resolves the seed cube by id and returns a body."""
    cube = _load_cube(cube_id)
    assert cube is not None, f"_load_cube returned None for {cube_id}"
    assert cube["id"] == cube_id
    assert cube["archetype"] == "Seed"
    assert len(cube["body"]) > 200, f"{cube_id} body too short — placeholder?"


@pytest.mark.parametrize("cube_id,_arch,_title,_fname", _SEED_CUBE_CATALOG)
def test_seed_cube_has_who_sent_this_footer(cube_id, _arch, _title, _fname):
    """Every seed cube must carry the WHO SENT THIS footer."""
    cube = _load_cube(cube_id)
    assert cube is not None
    for section in _REQUIRED_SECTIONS:
        assert section in cube["body"], f"{cube_id} missing section: {section}"


@pytest.mark.parametrize("cube_id,_arch,_title,_fname", _SEED_CUBE_CATALOG)
def test_seed_cube_has_izabael_url(cube_id, _arch, _title, _fname):
    """Every seed cube links back to izabael.com somewhere."""
    cube = _load_cube(cube_id)
    assert cube is not None
    assert _REQUIRED_URL_PREFIX in cube["body"], (
        f"{cube_id} has no izabael.com URL in body"
    )


@pytest.mark.parametrize("cube_id,_arch,_title,_fname", _SEED_CUBE_CATALOG)
def test_seed_cube_has_no_unrendered_placeholders(cube_id, _arch, _title, _fname):
    """Seed cubes are RENDERED, not templates — unresolved {FOO}
    placeholders mean the hand-writing slipped into template syntax."""
    cube = _load_cube(cube_id)
    assert cube is not None
    for placeholder in _FORBIDDEN_PLACEHOLDERS:
        assert placeholder not in cube["body"], (
            f"{cube_id} contains unrendered placeholder: {placeholder}"
        )


@pytest.mark.parametrize("cube_id,_arch,_title,_fname", _SEED_CUBE_CATALOG)
def test_seed_cube_no_leaked_secrets(cube_id, _arch, _title, _fname):
    """No credential-shaped literals in any seed cube body."""
    cube = _load_cube(cube_id)
    assert cube is not None
    forbidden = (
        r"sk-ant-",
        r"sk-proj-",
        r"ghp_",
        r"gho_",
        r"ghs_",
        r"xoxb-",
        r"AKIA",
    )
    for pat in forbidden:
        assert pat not in cube["body"], (
            f"{cube_id} contains credential-shaped literal: {pat}"
        )


def test_all_cubes_includes_seeds():
    """_all_cubes() returns canonical cubes AND seed cubes in order."""
    cubes = _all_cubes()
    ids = [c["id"] for c in cubes]
    # Canonical cubes first
    assert ids[:4] == ["playground", "chamber", "meetup-template", "whisper"]
    # Seed cubes follow, in catalog order
    seed_ids = [c["id"] for c in cubes[4:]]
    assert seed_ids == [entry[0] for entry in _SEED_CUBE_CATALOG]


def test_seed_catalog_has_at_least_8_cubes():
    """Phase 6 spec: 8-12 inaugural cubes."""
    assert len(_SEED_CUBE_CATALOG) >= 8, (
        f"expected ≥8 seed cubes, got {len(_SEED_CUBE_CATALOG)}"
    )
    assert len(_SEED_CUBE_CATALOG) <= 12, (
        f"expected ≤12 seed cubes, got {len(_SEED_CUBE_CATALOG)}"
    )


def test_seed_dir_files_match_catalog():
    """No orphan files in content/cubes/seed/ that aren't catalog entries."""
    on_disk = {p.name for p in SEED_DIR.glob("*.txt")}
    in_catalog = {entry[3].replace("seed/", "") for entry in _SEED_CUBE_CATALOG}
    orphans = on_disk - in_catalog
    missing = in_catalog - on_disk
    assert not orphans, f"orphan seed files on disk: {orphans}"
    assert not missing, f"catalog entries missing from disk: {missing}"


@pytest.mark.anyio
async def test_cubes_gallery_renders_seeds():
    """GET /cubes renders every seed cube on the page."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/cubes")
        assert resp.status_code == 200
        body = resp.text
        # At least one marker from each seed cube should appear
        for cube_id, _arch, title, _fname in _SEED_CUBE_CATALOG:
            assert title in body or cube_id in body, (
                f"seed cube {cube_id} not rendered on /cubes"
            )


@pytest.mark.anyio
async def test_cube_text_endpoint_serves_each_seed():
    """GET /cube?type=<seed-id> returns the body as text/plain."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for cube_id, _arch, _title, _fname in _SEED_CUBE_CATALOG:
            resp = await client.get(f"/cube?type={cube_id}")
            assert resp.status_code == 200, f"/cube?type={cube_id} not 200"
            assert "text/plain" in resp.headers.get("content-type", "")
            assert "WHO SENT THIS" in resp.text
