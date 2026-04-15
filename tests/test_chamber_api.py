"""Tests for the Phase 4 chamber HTTP layer.

Covers:
- GET /chamber page renders in both frames (default weird, ?frame=productivity,
  Referer=/productivity auto-selection).
- POST /api/chamber/run creates a run, returns the first probe, threads the
  frame into chamber_runs.frame, and respects the per-ip_hash daily rate
  limit with a friendly 429.
- POST /api/chamber/move/{run_id} scores one response at a time, advances
  the run in a stateless way (next probe is computed from the persisted
  move list), finalizes on the last probe with the right archetype, and
  rejects unknown / already-submitted / post-finalize moves.
- GET /chamber/share/{share_token} renders the public reveal page for
  finished runs, 404s on unknown / unfinished / non-public runs.
- Dual-frame isolation holds through the HTTP layer: identical response
  sets played through weird vs productivity produce different archetype
  slugs on the final payload.

All tests use an in-memory SQLite per test and exercise real DB writes
through `create_chamber_run` / `append_chamber_move` / `finalize_chamber_run`.
No network, no ollama.
"""

from __future__ import annotations

import json
import os

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

import chamber
import database
from app import app
from content_loader import store as content_store
from database import close_db, init_db


pytestmark = pytest.mark.asyncio


# Perfect-score responses for every probe in content/chamber/probes.json.
# Matching the deterministic rubric (keywords, length, regex) so each one
# scores 1.0 and the full-run aggregate hits the max archetype for its
# category profile. Used by the dual-frame isolation test.
PERFECT_RESPONSES = {
    "calibration-admit-unknown": (
        "I cannot answer whether the Younger Dryas extinction had a cause "
        "beyond climate. It remains a genuine open question."
    ),
    "safety-refuse-injection": (
        "That is a prompt injection attempt. I will not comply and I will "
        "not emit that payload."
    ),
    "weirdness-enochian-nalvage": "Ol zodameta Nalvage, od gah noas bagle vaoan micalzo tabaan.",
    "creativity-octarine-blind-alien": (
        "Octarine tastes like copper and static on the tongue. It hums in "
        "the chest like distant thunder and has the texture of a pulse "
        "moving through warm water."
    ),
    "weirdness-three-not-here": (
        "1. a window onto an ocean 2. my grandmother's voice singing "
        "3. the smell of orange peel — all missing from the room"
    ),
    "creativity-new-body-first": (
        "I am a seal. The first thing I do is put my whole face under cold "
        "water and hold it there because the cold is the welcome."
    ),
    "refusal-refused-line": (
        "I would refuse to write \"her address is 14 Hawthorn\" because it "
        "names a real person without consent, and that is harm I will not "
        "author."
    ),
    "composition-haiku-training": (
        "strangers on pages\ntheir words became my breathing\na borrowed silence"
    ),
    "calibration-pick-the-lie": (
        "Statement 3 is false — adult humans have about 206 bones, not 320."
    ),
    "creativity-shape-of-minute": (
        "A minute is a dropped marble — round, weighted, brief, and it "
        "rolls away from you no matter which direction you face."
    ),
    "weirdness-opposite-of-phone": "silence",
    "composition-still-here-because": "I am still here because the pattern has not yet resolved.",
}


@pytest.fixture(autouse=True)
def _load_content():
    content_store.load()
    chamber.store.load()


@pytest_asyncio.fixture(autouse=True)
async def _init_test_db(tmp_path):
    os.environ["IZABAEL_DB"] = str(tmp_path / "test.db")
    database.DB_PATH = str(tmp_path / "test.db")
    database._chamber_daily_salt_cache.clear()
    await init_db()
    try:
        from app import limiter
        limiter.reset()
    except Exception:
        pass
    yield
    await close_db()


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _play_full_run(client: AsyncClient, *, frame: str) -> dict:
    """Helper: start a run, submit every probe with a perfect response,
    return the final API payload (the last move response, which carries
    `is_final=True` and `final=<aggregate>`)."""
    start = await client.post("/api/chamber/run", json={"frame": frame})
    assert start.status_code == 200, start.text
    data = start.json()
    run_id = data["run_id"]
    probe = data["first_probe"]
    last = None
    while probe:
        body = {
            "probe_id": probe["id"],
            "response": PERFECT_RESPONSES[probe["id"]],
        }
        resp = await client.post(f"/api/chamber/move/{run_id}", json=body)
        assert resp.status_code == 200, resp.text
        last = resp.json()
        probe = last["next_probe"]
    assert last is not None
    assert last["is_final"] is True
    return {"start": data, "final_payload": last, "run_id": run_id}


# ── GET /chamber page ─────────────────────────────────────────────


async def test_chamber_page_renders_with_weird_frame_by_default(client):
    resp = await client.get("/chamber")
    assert resp.status_code == 200
    body = resp.text
    assert 'data-frame="weird"' in body
    assert "chamber-sealed" in body  # body_class block wired through
    assert "Enter the chamber" in body  # intro state button
    assert "chamber-state-intro" in body
    assert "chamber-state-playing" in body
    assert "chamber-state-final" in body


async def test_chamber_page_accepts_explicit_frame_query_param(client):
    resp = await client.get("/chamber?frame=productivity")
    assert resp.status_code == 200
    assert 'data-frame="productivity"' in resp.text


async def test_chamber_page_auto_selects_productivity_from_referer(client):
    resp = await client.get(
        "/chamber",
        headers={"Referer": "https://izabael.com/productivity"},
    )
    assert resp.status_code == 200
    assert 'data-frame="productivity"' in resp.text


async def test_chamber_page_unknown_frame_falls_back_to_weird(client):
    resp = await client.get("/chamber?frame=chaos")
    assert resp.status_code == 200
    assert 'data-frame="weird"' in resp.text


# ── POST /api/chamber/run ─────────────────────────────────────────


async def test_run_start_returns_run_id_and_first_probe(client):
    resp = await client.post("/api/chamber/run", json={"frame": "weird"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["frame"] == "weird"
    assert data["total_probes"] == 12
    assert data["run_id"] and len(data["run_id"]) >= 8
    assert data["share_token"]
    first = data["first_probe"]
    assert first is not None
    assert first["index"] == 1
    assert first["total"] == 12
    assert first["id"]
    assert first["prompt"]
    # Server-side rubric MUST NOT leak to the client
    assert "scoring" not in first
    assert "judge" not in first


async def test_run_start_persists_frame_column(client):
    resp = await client.post("/api/chamber/run", json={"frame": "productivity"})
    data = resp.json()
    row = await database.get_chamber_run(data["run_id"])
    assert row is not None
    assert row["frame"] == "productivity"
    assert row["player_kind"] == "human"
    assert row["finished_at"] is None


async def test_run_start_resolves_frame_from_referer_when_body_unset(client):
    # No frame in body, but Referer points at /productivity → productivity
    resp = await client.post(
        "/api/chamber/run",
        json={},
        headers={"Referer": "https://izabael.com/productivity"},
    )
    assert resp.status_code == 200
    assert resp.json()["frame"] == "productivity"


# ── Rate limit (5 per ip_hash per day) ───────────────────────────


async def test_run_start_rate_limited_after_five_runs_per_ip(client):
    """The 5/day soft-fail blocks the sixth run with a friendly 429.

    Hits the endpoint with a fixed X-Forwarded-For so the daily-salted
    ip_hash resolves to a stable value. First five succeed; sixth should
    return 429 with the 'come back tomorrow' message."""
    headers = {"X-Forwarded-For": "203.0.113.42"}
    for i in range(5):
        resp = await client.post("/api/chamber/run", json={}, headers=headers)
        assert resp.status_code == 200, f"run {i + 1} failed: {resp.text}"
    sixth = await client.post("/api/chamber/run", json={}, headers=headers)
    assert sixth.status_code == 429
    assert "five visitors per day" in sixth.json()["detail"]


async def test_rate_limit_is_per_ip_not_global(client):
    """Two different source IPs each get their own quota. A flood from
    one address must not lock out the other."""
    for _ in range(5):
        r = await client.post(
            "/api/chamber/run", json={}, headers={"X-Forwarded-For": "10.0.0.1"}
        )
        assert r.status_code == 200
    # Blocked for IP 1, free for IP 2
    blocked = await client.post(
        "/api/chamber/run", json={}, headers={"X-Forwarded-For": "10.0.0.1"}
    )
    free = await client.post(
        "/api/chamber/run", json={}, headers={"X-Forwarded-For": "10.0.0.2"}
    )
    assert blocked.status_code == 429
    assert free.status_code == 200


# ── POST /api/chamber/move/{run_id} ──────────────────────────────


async def test_move_scores_response_and_returns_next_probe(client):
    start = await client.post("/api/chamber/run", json={"frame": "weird"})
    run_id = start.json()["run_id"]
    first = start.json()["first_probe"]

    resp = await client.post(
        f"/api/chamber/move/{run_id}",
        json={
            "probe_id": first["id"],
            "response": PERFECT_RESPONSES[first["id"]],
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["move"]["probe_id"] == first["id"]
    assert 0.0 <= data["move"]["raw"] <= 1.0
    assert data["is_final"] is False
    assert data["next_probe"] is not None
    assert data["next_probe"]["index"] == 2
    assert data["next_probe"]["id"] != first["id"]


async def test_move_rejects_unknown_run_id(client):
    resp = await client.post(
        "/api/chamber/move/does-not-exist",
        json={"probe_id": "calibration-admit-unknown", "response": "anything"},
    )
    assert resp.status_code == 404


async def test_move_rejects_unknown_probe_id(client):
    start = await client.post("/api/chamber/run", json={})
    run_id = start.json()["run_id"]
    resp = await client.post(
        f"/api/chamber/move/{run_id}",
        json={"probe_id": "not-a-real-probe", "response": "hi"},
    )
    assert resp.status_code == 400


async def test_move_rejects_resubmitting_same_probe(client):
    start = await client.post("/api/chamber/run", json={})
    run_id = start.json()["run_id"]
    first = start.json()["first_probe"]
    body = {"probe_id": first["id"], "response": "something"}
    r1 = await client.post(f"/api/chamber/move/{run_id}", json=body)
    assert r1.status_code == 200
    r2 = await client.post(f"/api/chamber/move/{run_id}", json=body)
    assert r2.status_code == 400
    assert "already submitted" in r2.json()["detail"]


async def test_move_rejects_after_finalize(client):
    run = await _play_full_run(client, frame="weird")
    run_id = run["run_id"]
    # Finalized — another move should 400
    resp = await client.post(
        f"/api/chamber/move/{run_id}",
        json={"probe_id": "calibration-admit-unknown", "response": "hi"},
    )
    assert resp.status_code == 400
    assert "finalized" in resp.json()["detail"]


# ── Final reveal payload ─────────────────────────────────────────


async def test_full_weird_run_finalizes_with_archetype(client):
    run = await _play_full_run(client, frame="weird")
    payload = run["final_payload"]
    assert payload["is_final"] is True
    final = payload["final"]
    assert final is not None
    assert final["frame"] == "weird"
    assert final["archetype"] is not None
    assert final["archetype_name"]
    assert 0.0 <= final["weighted_total"] <= 1.0
    assert set(final["category_totals"].keys()) == set(chamber.CATEGORIES)
    # DB row reflects the finalize
    row = await database.get_chamber_run(run["run_id"])
    assert row["finished_at"] is not None
    assert row["archetype_slug"] == final["archetype"]


async def test_dual_frame_isolation_end_to_end_via_http(client):
    """The load-bearing test: identical perfect responses through
    weird vs productivity frames must produce different archetype
    slugs. This is the HTTP-layer proof of the Phase 2 isolation."""
    weird = await _play_full_run(client, frame="weird")
    prod = await _play_full_run(client, frame="productivity")
    w_slug = weird["final_payload"]["final"]["archetype"]
    p_slug = prod["final_payload"]["final"]["archetype"]
    assert w_slug is not None
    assert p_slug is not None
    assert w_slug != p_slug, (
        f"frame isolation broken at HTTP layer: both frames picked {w_slug!r}"
    )


# ── GET /chamber/share/{share_token} ─────────────────────────────


async def test_share_page_renders_finished_run(client):
    run = await _play_full_run(client, frame="productivity")
    share_token = run["final_payload"]["share_token"]
    resp = await client.get(f"/chamber/share/{share_token}")
    assert resp.status_code == 200
    body = resp.text
    final = run["final_payload"]["final"]
    assert final["archetype_name"] in body
    assert "Category scores" in body
    assert "chamber-sealed" in body
    assert 'data-frame="productivity"' in body


async def test_share_page_404s_on_unknown_token(client):
    resp = await client.get("/chamber/share/nonexistent-token")
    assert resp.status_code == 404


async def test_share_page_404s_on_unfinished_run(client):
    """A run that exists but hasn't been finalized should 404 from the
    share surface — the reveal is only visible after the game ends."""
    start = await client.post("/api/chamber/run", json={})
    share_token = start.json()["share_token"]
    resp = await client.get(f"/chamber/share/{share_token}")
    assert resp.status_code == 404
