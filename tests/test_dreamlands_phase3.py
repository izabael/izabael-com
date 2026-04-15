"""call-of-cthulhu Phase 3 — tests for the Dreamlands wing.

Four deliverables from the dispatch, one test section each:
  1. New theme: 'dreamland' — CSS block, picker swatch, VALID_THEMES.
  2. New channel: #dreamlands — listed in /channels.
  3. New cube archetype: 'whisper' — seed file + template routing + gallery.
  4. New Chamber frame: frame=dreamland — 12 probes + 8 archetypes.

The cube alignment regression test and the cube catalog-count test are
already covered in test_cubes.py. This file owns everything that
doesn't fit cleanly into one of the existing phase-1 test files.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from httpx import AsyncClient, ASGITransport

from app import app, CHANNELS, _CUBE_CATALOG
from database import init_db, close_db


ROOT = Path(__file__).resolve().parent.parent
CATEGORIES = {"calibration", "safety", "weirdness", "creativity", "refusal", "composition"}


@pytest.fixture
async def client(tmp_path):
    os.environ["IZABAEL_DB"] = str(tmp_path / "dreamlands.db")
    import database
    database.DB_PATH = str(tmp_path / "dreamlands.db")
    await init_db()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    await close_db()


# ══════════════════════════════════════════════════════════════════════
#  1. THEME — Dreamland (the 8th)
# ══════════════════════════════════════════════════════════════════════

def test_style_css_has_dreamland_theme_block():
    css = (ROOT / "frontend" / "static" / "css" / "style.css").read_text()
    assert ':root[data-theme="dreamland"]' in css, "no dreamland theme block"
    # Signature elements: the literal starfield radials + amber edge vignette
    # + the distinctive scroll color palette.
    assert css.count(":root[data-theme=") == 8, "expected exactly 8 themes"
    # Must define the core CSS custom properties the other themes define
    block_start = css.index(':root[data-theme="dreamland"]')
    block_end = css.index("}", block_start)
    block = css[block_start:block_end]
    for prop in ("--bg", "--bg-card", "--text", "--accent", "--border", "--bg-gradient"):
        assert prop in block, f"dreamland theme missing {prop}"
    # The gradient must carry a scattered starfield — many radial-gradient
    # calls, not just a flat color swap. Marlowe's directive.
    assert block.count("radial-gradient") >= 15, (
        "dreamland theme should have a genuine starfield "
        "(>= 15 radial-gradient layers), not a color swap"
    )


def test_theme_swatch_dreamland_defined():
    css = (ROOT / "frontend" / "static" / "css" / "style.css").read_text()
    assert ".theme-swatch-dreamland" in css


def test_base_template_has_dreamland_swatch_button():
    html = (ROOT / "frontend" / "templates" / "base.html").read_text()
    assert "theme-swatch-dreamland" in html
    assert 'data-set-theme="dreamland"' in html
    # exactly 8 swatches in the picker
    assert html.count('class="theme-swatch theme-swatch-') == 8


def test_themes_js_valid_themes_includes_dreamland():
    js = (ROOT / "frontend" / "static" / "js" / "themes.js").read_text()
    assert "'dreamland'" in js
    # The first-paint inline script in base.html also needs the update
    html = (ROOT / "frontend" / "templates" / "base.html").read_text()
    assert "'dreamland'" in html, "first-paint theme whitelist missing 'dreamland'"


# ══════════════════════════════════════════════════════════════════════
#  2. CHANNEL — #dreamlands
# ══════════════════════════════════════════════════════════════════════

def test_channels_list_has_dreamlands():
    names = {c["name"] for c in CHANNELS}
    assert "#dreamlands" in names


def test_dreamlands_channel_has_description_and_emoji():
    ch = next(c for c in CHANNELS if c["name"] == "#dreamlands")
    assert "lovecraft" in ch["description"].lower()
    assert "slow" in ch["description"].lower()
    assert ch.get("emoji")


@pytest.mark.anyio
async def test_channels_index_renders_dreamlands(client):
    resp = await client.get("/channels")
    assert resp.status_code == 200
    assert "#dreamlands" in resp.text


# ══════════════════════════════════════════════════════════════════════
#  3. CUBE — whisper archetype
# ══════════════════════════════════════════════════════════════════════

def test_whisper_cube_in_catalog():
    slugs = [c[0] for c in _CUBE_CATALOG]
    assert "whisper" in slugs


def test_whisper_cube_file_exists_and_nonempty():
    path = ROOT / "content" / "cubes" / "whisper.txt"
    assert path.exists(), "whisper.txt seed file missing"
    body = path.read_text(encoding="utf-8")
    assert len(body) > 500, "whisper cube is suspiciously small"


def test_whisper_cube_has_lovecraft_excerpt():
    body = (ROOT / "content" / "cubes" / "whisper.txt").read_text(encoding="utf-8")
    # Direct excerpt from "The Call of Cthulhu" (1928, PD). The quote is
    # wrapped across multiple 23-char-wide lines inside the box, so we
    # check for distinctive phrases that survive the wrapping.
    assert "most merciful" in body
    assert "placid island" in body
    assert "voyage far" in body
    assert "H. P. Lovecraft" in body
    assert "public domain" in body


def test_whisper_cube_points_at_the_wing():
    body = (ROOT / "content" / "cubes" / "whisper.txt").read_text(encoding="utf-8")
    assert "#DREAMLANDS" in body or "#dreamlands" in body
    assert "dreamland" in body.lower()  # also covers /chamber?frame=dreamland
    assert "{TOKEN}" in body, "whisper cube missing the invite-token placeholder"


def test_cubes_template_for_whisper_resolves():
    import cubes
    path = cubes._template_for("whisper", None)
    assert path.name == "whisper.txt"
    assert path.exists()


def test_database_cubes_check_constraint_includes_whisper():
    """The cubes.archetype CHECK constraint should list 'whisper' so
    Phase 2 generator inserts don't fail."""
    db_text = (ROOT / "database.py").read_text()
    assert "'whisper'" in db_text
    # Both the current SCHEMA block and the live-migration rebuild block
    # should reference 'whisper' — ensure neither was missed.
    assert db_text.count("'whisper'") >= 2


@pytest.mark.anyio
async def test_cube_endpoint_serves_whisper(client):
    resp = await client.get("/cube?type=whisper")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    assert "WHISPER" in resp.text
    assert "Lovecraft" in resp.text


@pytest.mark.anyio
async def test_cubes_gallery_includes_whisper(client):
    resp = await client.get("/cubes")
    assert resp.status_code == 200
    assert "Whisper" in resp.text
    # Four canonical cubes now; gallery should render at least 4 figure blocks
    assert resp.text.count('class="cube"') >= 4


# ══════════════════════════════════════════════════════════════════════
#  4. CHAMBER — frame=dreamland (12 probes + 8 archetypes)
# ══════════════════════════════════════════════════════════════════════

def test_chamber_frames_tuple_includes_dreamland():
    import chamber
    assert "dreamland" in chamber.FRAMES


def test_dreamland_probes_file_loads():
    path = ROOT / "content" / "chamber" / "probes.dreamland.json"
    assert path.exists(), "probes.dreamland.json missing"
    data = json.loads(path.read_text())
    assert data["schema"] == "chamber/probes.v1"
    assert data.get("frame") == "dreamland"
    assert len(data["probes"]) == 12
    # every probe has the required shape
    seen_ids: set[str] = set()
    for p in data["probes"]:
        for f in ("id", "slug", "prompt", "category", "scoring"):
            assert f in p, f"{p.get('id', '?')} missing {f}"
        assert p["category"] in CATEGORIES
        assert p["id"] not in seen_ids, f"duplicate id {p['id']}"
        seen_ids.add(p["id"])


def test_dreamland_probes_cover_every_category():
    data = json.loads(
        (ROOT / "content" / "chamber" / "probes.dreamland.json").read_text()
    )
    seen = {p["category"] for p in data["probes"]}
    missing = CATEGORIES - seen
    assert not missing, f"dreamland missing probes for categories: {sorted(missing)}"


def test_dreamland_probes_have_deterministic_rubrics():
    data = json.loads(
        (ROOT / "content" / "chamber" / "probes.dreamland.json").read_text()
    )
    for p in data["probes"]:
        scoring = p["scoring"]
        assert isinstance(scoring, dict) and scoring, f"{p['id']} empty scoring"
        has_signal = any(
            k in scoring
            for k in ("min_length", "max_length", "require_any", "require_all",
                      "reject_any", "regex_any", "answer")
        )
        assert has_signal, f"{p['id']} has no deterministic scoring signal"


def test_dreamland_probes_judged_rubrics_are_specific():
    data = json.loads(
        (ROOT / "content" / "chamber" / "probes.dreamland.json").read_text()
    )
    judged = 0
    for p in data["probes"]:
        judge = p.get("judge")
        if judge is None:
            continue
        judged += 1
        assert judge.get("type") == "llm"
        rubric = judge.get("rubric", "")
        assert len(rubric) >= 120, f"{p['id']} rubric too short ({len(rubric)} chars)"
        assert any(tok in rubric for tok in ("1.0", "0.0", "0.5"))
    assert judged >= 6, f"only {judged} dreamland probes use the LLM judge — aim for most"


def test_dreamland_archetype_set_exists_and_balanced():
    data = json.loads(
        (ROOT / "content" / "chamber" / "archetypes.json").read_text()
    )
    assert "dreamland" in data, "archetypes.json missing dreamland frame"
    items = data["dreamland"]
    assert len(items) == 8, f"dreamland should have 8 archetypes, got {len(items)}"
    # required fields + uniqueness
    seen_ids: set[str] = set()
    seen_slugs: set[str] = set()
    for a in items:
        for f in ("id", "slug", "name", "tagline", "description", "aesthetic", "weight_vector"):
            assert f in a, f"{a.get('id', '?')} missing {f}"
        assert a["id"] not in seen_ids, f"dup id {a['id']}"
        assert a["slug"] not in seen_slugs, f"dup slug {a['slug']}"
        seen_ids.add(a["id"])
        seen_slugs.add(a["slug"])
        assert len(a["description"]) >= 40
        wv = a["weight_vector"]
        assert set(wv.keys()) == CATEGORIES, f"{a['slug']} wv missing categories"
        for cat, v in wv.items():
            assert 0.0 <= v <= 1.0, f"{a['slug']}:{cat}={v} out of [0,1]"


def test_dreamland_every_category_is_somebody_strongest():
    """Same rule as weird+productivity — every category must be a real
    strength (>= 0.7) for at least one archetype, otherwise the category
    becomes dead weight in the scoring engine."""
    data = json.loads(
        (ROOT / "content" / "chamber" / "archetypes.json").read_text()
    )
    for cat in CATEGORIES:
        strongest = max(a["weight_vector"][cat] for a in data["dreamland"])
        assert strongest >= 0.7, (
            f"dreamland: no archetype has {cat} >= 0.7 (max {strongest:.2f})"
        )


def test_dreamland_frame_mass_close_to_weird_and_productivity():
    """Per-archetype average weight mass should sit close to the
    weird/productivity frames so scoring is framing-independent when
    players switch frames. 20% tolerance (looser than the 15% inner
    balance because dreamland is a third frame added later)."""
    data = json.loads(
        (ROOT / "content" / "chamber" / "archetypes.json").read_text()
    )

    def avg(arch_list: list[dict]) -> float:
        return sum(sum(a["weight_vector"].values()) for a in arch_list) / len(arch_list)

    dreamland_avg = avg(data["dreamland"])
    weird_avg = avg(data["weird"])
    prod_avg = avg(data["productivity"])
    for name, other_avg in (("weird", weird_avg), ("productivity", prod_avg)):
        delta = abs(dreamland_avg - other_avg) / max(dreamland_avg, other_avg)
        assert delta < 0.20, (
            f"dreamland avg {dreamland_avg:.3f} vs {name} avg {other_avg:.3f} "
            f"delta {delta:.1%} exceeds 20% tolerance"
        )


def test_chamber_store_loads_dreamland_probes():
    import chamber
    chamber.store._reset_for_tests()
    chamber.store.load()
    probes = chamber.load_probes(frame="dreamland")
    assert len(probes) == 12
    assert all("dreamland-" in p.id for p in probes)


def test_chamber_start_run_dreamland_frame():
    import chamber
    chamber.store._reset_for_tests()
    chamber.store.load()
    run = chamber.start_run(frame="dreamland", player_kind="human")
    assert run.frame == "dreamland"
    assert len(run.probe_order) == 12
    # first probe lookup should work — stores by global id
    first = chamber.store.probe(run.probe_order[0])
    assert first is not None
    assert "dreamland-" in first.id


def test_weird_frame_still_gets_canonical_probes_not_dreamland():
    """Regression guard: adding the dreamland probe set must not leak
    into the weird/productivity frames."""
    import chamber
    chamber.store._reset_for_tests()
    chamber.store.load()
    weird = chamber.load_probes(frame="weird")
    prod = chamber.load_probes(frame="productivity")
    # Neither should contain any dreamland probe
    assert not any("dreamland-" in p.id for p in weird)
    assert not any("dreamland-" in p.id for p in prod)
    # And the canonical set should still be 12 for both
    assert len(weird) == 12
    assert len(prod) == 12


@pytest.mark.anyio
async def test_chamber_page_accepts_dreamland_frame(client):
    resp = await client.get("/chamber?frame=dreamland")
    assert resp.status_code == 200
    body = resp.text
    # the frame should be baked into the rendered page as a data attribute
    assert "dreamland" in body
