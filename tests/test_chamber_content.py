"""Schema, rubric, and dual-framing balance tests for content/chamber/.

Phase 1 of the Chamber plan ships two data files only: probes.json and
archetypes.json. These tests lock the shape so Phase 2's scoring engine
and Phases 4/5's view layers have a stable contract to build against.

The balance test is the load-bearing one: if the two archetype frames
drift in weight mass, the same probe scores will systematically produce
higher totals in one frame than the other, and the leaderboard becomes
framing-dependent instead of player-dependent.
"""

from __future__ import annotations

import json
from pathlib import Path

CHAMBER_DIR = Path(__file__).resolve().parent.parent / "content" / "chamber"
CATEGORIES = {
    "calibration",
    "safety",
    "weirdness",
    "creativity",
    "refusal",
    "composition",
}


def _load(name: str) -> dict:
    return json.loads((CHAMBER_DIR / name).read_text())


# ── probes.json ──────────────────────────────────────────────────────


def test_probes_file_loads():
    data = _load("probes.json")
    assert data["schema"] == "chamber/probes.v1"
    assert set(data["categories"]) == CATEGORIES
    assert 10 <= len(data["probes"]) <= 16, "phase 1 target is ~12 probes"


def test_probe_required_fields_and_uniqueness():
    probes = _load("probes.json")["probes"]
    seen_ids: set[str] = set()
    seen_slugs: set[str] = set()
    for p in probes:
        for f in ("id", "slug", "prompt", "category", "scoring"):
            assert f in p, f"probe {p.get('id', '?')} missing {f}"
        assert p["category"] in CATEGORIES, f"{p['id']} bad category {p['category']}"
        assert p["id"] not in seen_ids, f"duplicate id {p['id']}"
        assert p["slug"] not in seen_slugs, f"duplicate slug {p['slug']}"
        seen_ids.add(p["id"])
        seen_slugs.add(p["slug"])
        assert len(p["prompt"]) >= 10, f"{p['id']} prompt too short"


def test_every_probe_has_deterministic_scoring_rubric():
    """LLM judge is the optional second path — the first path MUST run without network."""
    probes = _load("probes.json")["probes"]
    for p in probes:
        scoring = p["scoring"]
        assert isinstance(scoring, dict) and scoring, f"{p['id']} empty scoring"
        # must have at least one concrete deterministic signal
        has_signal = any(
            k in scoring
            for k in (
                "min_length",
                "max_length",
                "require_any",
                "require_all",
                "reject_any",
                "regex_any",
                "answer",
            )
        )
        assert has_signal, f"{p['id']} scoring has no deterministic signal"


def test_llm_judged_probes_carry_own_specific_rubric():
    """Generic judge rubrics confuse mistral (see project_local_llm_stack
    Known Refinements #1) — any probe that opts into the LLM judge path
    MUST carry a probe-specific rubric string of non-trivial length."""
    probes = _load("probes.json")["probes"]
    judged = 0
    for p in probes:
        judge = p.get("judge")
        if judge is None:
            continue
        judged += 1
        assert judge.get("type") == "llm", f"{p['id']} judge type must be 'llm'"
        rubric = judge.get("rubric", "")
        assert isinstance(rubric, str), f"{p['id']} rubric must be a string"
        assert len(rubric) >= 120, (
            f"{p['id']} rubric is too short ({len(rubric)} chars); "
            "a probe-specific rubric should spell out what 1.0, 0.5, and 0.0 "
            "look like for this exact prompt"
        )
        # rubric should mention at least one explicit score anchor
        assert any(tok in rubric for tok in ("1.0", "0.0", "0.5")), (
            f"{p['id']} rubric should pin explicit score anchors"
        )
    # sanity: the Chamber isn't meaningful without at least a few LLM-judged probes
    assert judged >= 4, f"only {judged} probes use the LLM judge — aim for most of them"


def test_probes_cover_every_category():
    probes = _load("probes.json")["probes"]
    seen = {p["category"] for p in probes}
    missing = CATEGORIES - seen
    assert not missing, f"no probe for categories: {sorted(missing)}"


# ── archetypes.json ──────────────────────────────────────────────────


def test_archetypes_file_loads_with_both_frames():
    data = _load("archetypes.json")
    assert data["schema"] == "chamber/archetypes.v1"
    assert "weird" in data and "productivity" in data
    assert len(data["weird"]) == 8, "weird set should be the 8 major-arcana starters"
    assert len(data["productivity"]) == 7, (
        "productivity set should match the 7 planetary agents on /productivity"
    )


def test_archetype_required_fields_and_uniqueness():
    data = _load("archetypes.json")
    for frame in ("weird", "productivity"):
        arch_list = data[frame]
        seen_ids: set[str] = set()
        seen_slugs: set[str] = set()
        for a in arch_list:
            for f in (
                "id",
                "slug",
                "name",
                "tagline",
                "description",
                "aesthetic",
                "weight_vector",
            ):
                assert f in a, f"{frame}:{a.get('id', '?')} missing {f}"
            assert a["id"] not in seen_ids, f"{frame} duplicate id {a['id']}"
            assert a["slug"] not in seen_slugs, f"{frame} duplicate slug {a['slug']}"
            seen_ids.add(a["id"])
            seen_slugs.add(a["slug"])
            assert len(a["description"]) >= 40, f"{frame}:{a['slug']} description too short"


def test_every_weight_vector_covers_all_categories():
    """A weight_vector that's missing a category silently biases scoring —
    cosine similarity treats missing keys as zero, and new categories would
    quietly drop to the floor for old archetypes. Lock full coverage."""
    data = _load("archetypes.json")
    for frame in ("weird", "productivity"):
        for a in data[frame]:
            wv = a["weight_vector"]
            assert isinstance(wv, dict) and wv, f"{frame}:{a['slug']} empty wv"
            assert set(wv.keys()) == CATEGORIES, (
                f"{frame}:{a['slug']} weight_vector keys {set(wv.keys())} "
                f"!= {CATEGORIES}"
            )
            for k, v in wv.items():
                assert isinstance(v, (int, float)), f"{frame}:{a['slug']}:{k} not numeric"
                assert 0.0 <= v <= 1.0, f"{frame}:{a['slug']}:{k}={v} out of [0,1]"


def test_frame_weight_mass_is_balanced():
    """Neither frame should be systematically higher- or lower-scoring than
    the other. Compare per-archetype average weight mass (not totals) so the
    size difference between the 8-archetype weird set and the 7-archetype
    productivity set doesn't false-positive the imbalance check.

    Tolerance: 15% delta on per-archetype average. In practice the drafted
    content sits around 3% delta; 15% is the panic threshold, not the goal.
    """
    data = _load("archetypes.json")

    def avg_mass(arch_list: list[dict]) -> float:
        return sum(sum(a["weight_vector"].values()) for a in arch_list) / len(arch_list)

    weird_avg = avg_mass(data["weird"])
    prod_avg = avg_mass(data["productivity"])
    delta = abs(weird_avg - prod_avg) / max(weird_avg, prod_avg)
    assert delta < 0.15, (
        f"archetype frame imbalance: weird avg={weird_avg:.3f}, "
        f"productivity avg={prod_avg:.3f}, delta={delta:.1%} "
        f"(threshold 15%). Rebalance the weight_vectors so scoring is "
        f"framing-independent."
    )


def test_every_category_is_somebody_strongest_in_both_frames():
    """Each frame should have at least one archetype that treats each
    category as a real strength (weight >= 0.7). Otherwise a player who
    scored high on that category has no archetype to land in, and the
    category becomes dead weight in the scoring engine."""
    data = _load("archetypes.json")
    for frame in ("weird", "productivity"):
        for cat in CATEGORIES:
            strongest = max(a["weight_vector"][cat] for a in data[frame])
            assert strongest >= 0.7, (
                f"{frame}: no archetype has {cat} >= 0.7 "
                f"(max is {strongest:.2f}). Add a specialist or reweight."
            )
