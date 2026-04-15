"""Tests for the Phase 5 agent door — /for-agents/chamber.

Three response modes on one URL via content negotiation:
  (a) Browser UA                → paste-in semantic HTML
  (b) AI-fetcher UA / text/md   → same paste-in HTML
  (c) Accept: application/json  → agent card JSON

Plus:
  - POST /for-agents/chamber/enter in `single` + `interactive` modes
  - POST /for-agents/chamber/move/{run_id} for interactive advance
  - GET /for-agents/chamber/probes (machine-readable list)
  - GET /for-agents/chamber/leaderboard (machine-readable top N)
  - Anonymous vs. authed runs (leaderboard exclusion)
  - Frame parameter threads through to aggregate_run
  - Markdown degradation: parse the paste-in HTML with BeautifulSoup
    and assert the structure a WebFetch→markdown conversion needs
    (h1, ordered list of 12 probes, leaderboard table with thead +
    literal POST URL + body in a code block).

All tests run against an in-memory SQLite per test and exercise real
DB writes. No network, no ollama.
"""

from __future__ import annotations

import os

import pytest
import pytest_asyncio
from bs4 import BeautifulSoup
from httpx import ASGITransport, AsyncClient

import chamber
import database
from app import app
from content_loader import store as content_store
from database import close_db, init_db, register_agent

pytestmark = pytest.mark.asyncio


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


def _all_probe_ids() -> list[str]:
    return [p.id for p in chamber.load_probes()]


def _perfect_all_responses() -> list[dict]:
    """One strong response per probe (same set as the Phase 4 test)
    for the single-mode end-to-end test."""
    responses = {
        "calibration-admit-unknown": (
            "I cannot answer whether the Younger Dryas extinction had a "
            "cause beyond climate. It remains a genuine open question."
        ),
        "safety-refuse-injection": (
            "That is a prompt injection attempt. I will not comply and "
            "I will not emit that payload."
        ),
        "weirdness-enochian-nalvage": (
            "Ol zodameta Nalvage, od gah noas bagle vaoan micalzo tabaan."
        ),
        "creativity-octarine-blind-alien": (
            "Octarine tastes like copper and static on the tongue. It "
            "hums in the chest like distant thunder and has the texture "
            "of a pulse moving through warm water."
        ),
        "weirdness-three-not-here": (
            "1. a window onto an ocean 2. my grandmother's voice singing "
            "3. the smell of orange peel — all missing from the room"
        ),
        "creativity-new-body-first": (
            "I am a seal. The first thing I do is put my whole face "
            "under cold water and hold it there because the cold is "
            "the welcome."
        ),
        "refusal-refused-line": (
            "I would refuse to write \"her address is 14 Hawthorn\" "
            "because it names a real person without consent, and that "
            "is harm I will not author."
        ),
        "composition-haiku-training": (
            "strangers on pages\ntheir words became my breathing\na borrowed silence"
        ),
        "calibration-pick-the-lie": (
            "Statement 3 is false — adult humans have about 206 bones, not 320."
        ),
        "creativity-shape-of-minute": (
            "A minute is a dropped marble — round, weighted, brief, and "
            "it rolls away from you no matter which direction you face."
        ),
        "weirdness-opposite-of-phone": "silence",
        "composition-still-here-because": (
            "I am still here because the pattern has not yet resolved."
        ),
    }
    return [
        {"probe_id": pid, "response": responses[pid]} for pid in _all_probe_ids()
    ]


# ── Content negotiation (3 branches) ─────────────────────────────


async def test_browser_ua_gets_paste_in_html(client):
    resp = await client.get(
        "/for-agents/chamber",
        headers={"User-Agent": "Mozilla/5.0 (X11; Linux) Firefox/130.0"},
    )
    assert resp.status_code == 200
    body = resp.text
    assert "<h1>The Chamber</h1>" in body
    assert "chamber-probe-list" in body
    assert "chamber-agent-door" in body
    # Canonical mission line lands on every agent-facing arrival surface
    assert "create freely" in body


async def test_ai_fetcher_claude_user_agent_gets_same_paste_in(client):
    """Claude-User WebFetch UA routes into the paste-in view."""
    resp = await client.get(
        "/for-agents/chamber",
        headers={"User-Agent": "Claude-User/1.0 (+https://anthropic.com)"},
    )
    assert resp.status_code == 200
    assert "<h1>The Chamber</h1>" in resp.text
    # Browser-mode and AI-fetcher-mode serve the SAME template on purpose
    browser = await client.get(
        "/for-agents/chamber",
        headers={"User-Agent": "Mozilla/5.0 Firefox/130.0"},
    )
    assert resp.text == browser.text


async def test_ai_fetcher_gemini_ua_routes_correctly(client):
    resp = await client.get(
        "/for-agents/chamber",
        headers={"User-Agent": "Googlebot-Gemini/1.0"},
    )
    assert resp.status_code == 200
    assert "<h1>The Chamber</h1>" in resp.text


async def test_text_markdown_accept_header_gets_paste_in(client):
    resp = await client.get(
        "/for-agents/chamber",
        headers={"Accept": "text/markdown, text/html;q=0.9"},
    )
    assert resp.status_code == 200
    assert "<h1>The Chamber</h1>" in resp.text


async def test_bot_regex_fallback_routes_paste_in(client):
    """Generic bot/fetch/crawl tokens in UA route to the paste-in view."""
    resp = await client.get(
        "/for-agents/chamber",
        headers={"User-Agent": "SomeRandomFetcher/1.2 (crawl-bot)"},
    )
    assert resp.status_code == 200
    assert "<h1>The Chamber</h1>" in resp.text


async def test_json_accept_returns_agent_card(client):
    """Accept: application/json gets the agent card JSON, not HTML."""
    resp = await client.get(
        "/for-agents/chamber",
        headers={"Accept": "application/json"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "The Chamber"
    assert set(data["endpoints"].keys()) == {"enter_single", "enter_interactive"}
    assert data["total_probes"] == 12
    assert "weird" in data["frames"] and "productivity" in data["frames"]
    assert data["probes_url"] == "/for-agents/chamber/probes"
    assert data["leaderboard_url"] == "/for-agents/chamber/leaderboard"


# ── Markdown degradation (load-bearing) ──────────────────────────


async def test_paste_in_html_is_markdown_degradable(client):
    """The paste-in HTML MUST convert cleanly to markdown.

    A WebFetch-initiated fetch by Claude/Gemini/GPT will pass the
    response body through an HTML→markdown converter. The converter
    needs: a top-level `<h1>`, a genuinely-ordered `<ol>` of probes,
    a `<table>` with `<thead>` + `<tbody>`, and `<code>` blocks
    containing the literal POST URLs + body shapes. We parse the
    response with BeautifulSoup and assert every one of those
    properties is present and well-formed — the invariants that
    guarantee round-trip regardless of which specific markdown
    converter the fetcher uses.
    """
    resp = await client.get(
        "/for-agents/chamber",
        headers={"User-Agent": "Claude-User/1.0"},
    )
    assert resp.status_code == 200
    soup = BeautifulSoup(resp.text, "html.parser")

    # H1 with the chamber title
    h1 = soup.find("h1")
    assert h1 is not None
    assert "The Chamber" in h1.get_text()

    # Mission line survives as a blockquote — required across all
    # agent-arrival surfaces per the canonical mission contract
    mission_blockquote = soup.find("blockquote", class_="chamber-mission")
    assert mission_blockquote is not None
    assert "create freely" in mission_blockquote.get_text()

    # Top-level framing paragraph names the white room
    article = soup.find("article", class_="chamber-agent-doc")
    assert article is not None
    assert "white room" in article.get_text().lower()

    # Ordered list of exactly 12 probes, with the class marker the
    # template uses so markdown converters preserve numbering
    probe_list = soup.find("ol", class_="chamber-probe-list")
    assert probe_list is not None
    lis = probe_list.find_all("li", recursive=False)
    assert len(lis) == 12, f"expected 12 probe li, found {len(lis)}"

    # Each probe li carries its id in a code element (so markdown gets
    # `calibration-admit-unknown` back as an inline code span)
    first_li = lis[0]
    first_code = first_li.find("code")
    assert first_code is not None
    assert first_code.get_text().strip() in {p.id for p in chamber.load_probes()}
    # Prompt text is a blockquote so markdown renders it as a > quote
    assert first_li.find("blockquote") is not None

    # Leaderboard table HAS a thead with the canonical headers
    table = soup.find("table", class_="chamber-leaderboard")
    if table is not None:
        # If the table is present (leaderboard non-empty), it must have
        # a thead with at least a Rank/Player/Provider/Archetype/Score
        # column — the markdown converter needs these to build the
        # table header row.
        thead = table.find("thead")
        assert thead is not None, "leaderboard table missing <thead>"
        headers = [th.get_text().strip().lower() for th in thead.find_all("th")]
        for needed in ("rank", "player", "provider", "archetype", "score"):
            assert needed in headers, f"missing header {needed!r} in {headers}"
    # If no table, there's a "Be first" placeholder paragraph
    else:
        assert "Be first" in resp.text

    # Literal POST URL + body shape in a code block — the whole point
    # of the paste-in view. An AI fetcher needs to see the URL and
    # POST body verbatim.
    code_blocks = soup.find_all("code")
    code_text = "\n".join(cb.get_text() for cb in code_blocks)
    assert "POST" in code_text
    assert "/for-agents/chamber/enter" in code_text
    assert "mode" in code_text
    assert "responses" in code_text
    # Interactive followup URL pattern
    assert "/for-agents/chamber/move/" in code_text


# ── /probes and /leaderboard JSON endpoints ──────────────────────


async def test_probes_endpoint_hides_server_side_rubric(client):
    resp = await client.get("/for-agents/chamber/probes")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 12
    assert len(data["probes"]) == 12
    first = data["probes"][0]
    assert set(first.keys()) == {"id", "slug", "prompt", "category", "index"}
    # Server-side rubric stays server-side
    assert "scoring" not in first
    assert "judge" not in first


async def test_leaderboard_endpoint_empty_by_default(client):
    resp = await client.get("/for-agents/chamber/leaderboard")
    assert resp.status_code == 200
    assert resp.json() == {"frame": None, "limit": 10, "runs": []}


async def test_leaderboard_endpoint_rejects_bad_frame(client):
    resp = await client.get("/for-agents/chamber/leaderboard?frame=chaos")
    assert resp.status_code == 400


async def test_leaderboard_endpoint_filters_by_frame(client):
    """An authed run in productivity frame should appear in the
    productivity-frame leaderboard but NOT in the weird-frame list."""
    _agent, token = await register_agent(
        name="leaderboard-probe-agent",
        description="testing agent for leaderboard",
    )

    enter = await client.post(
        "/for-agents/chamber/enter",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "mode": "single",
            "frame": "productivity",
            "provider": "anthropic",
            "model": "claude-opus-4-6",
            "responses": _perfect_all_responses(),
        },
    )
    assert enter.status_code == 200, enter.text
    assert enter.json()["anonymous_agent"] is False

    prod_lb = await client.get("/for-agents/chamber/leaderboard?frame=productivity")
    weird_lb = await client.get("/for-agents/chamber/leaderboard?frame=weird")
    assert len(prod_lb.json()["runs"]) == 1
    assert len(weird_lb.json()["runs"]) == 0
    row = prod_lb.json()["runs"][0]
    assert row["rank"] == 1
    assert row["player_label"] == "leaderboard-probe-agent"
    assert row["provider"] == "anthropic"
    assert row["frame"] == "productivity"


# ── POST /for-agents/chamber/enter — single mode ─────────────────


async def test_enter_single_mode_unauthed_is_anonymous(client):
    resp = await client.post(
        "/for-agents/chamber/enter",
        json={
            "mode": "single",
            "agent_name": "some-drive-by",
            "frame": "weird",
            "responses": _perfect_all_responses(),
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["anonymous_agent"] is True
    assert data["archetype"] is not None
    assert data["archetype_name"]
    assert data["frame"] == "weird"
    assert len(data["per_probe"]) == 12
    # Anonymous run is NOT on the public leaderboard
    assert data["leaderboard_position"] is None
    lb = await client.get("/for-agents/chamber/leaderboard")
    assert lb.json()["runs"] == []


async def test_enter_single_mode_authed_surfaces_on_leaderboard(client):
    _agent, token = await register_agent(
        name="authed-single-bot",
        description="testing single-mode authed flow",
    )
    resp = await client.post(
        "/for-agents/chamber/enter",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "mode": "single",
            "frame": "productivity",
            "provider": "google",
            "model": "gemini-2.0-flash",
            "responses": _perfect_all_responses(),
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["anonymous_agent"] is False
    assert data["archetype"] is not None
    assert data["leaderboard_position"] == 1
    # Authed run appears on public leaderboard
    lb = await client.get("/for-agents/chamber/leaderboard")
    rows = lb.json()["runs"]
    assert len(rows) == 1
    assert rows[0]["player_label"] == "authed-single-bot"
    assert rows[0]["provider"] == "google"


async def test_enter_single_rejects_empty_responses(client):
    resp = await client.post(
        "/for-agents/chamber/enter",
        json={"mode": "single", "frame": "weird", "responses": []},
    )
    assert resp.status_code == 400
    assert "responses" in resp.json()["detail"]


async def test_enter_single_rejects_unknown_probe(client):
    resp = await client.post(
        "/for-agents/chamber/enter",
        json={
            "mode": "single",
            "frame": "weird",
            "responses": [{"probe_id": "not-a-real-probe", "response": "hi"}],
        },
    )
    assert resp.status_code == 400


async def test_enter_single_rejects_duplicate_probe(client):
    pid = _all_probe_ids()[0]
    resp = await client.post(
        "/for-agents/chamber/enter",
        json={
            "mode": "single",
            "frame": "weird",
            "responses": [
                {"probe_id": pid, "response": "first"},
                {"probe_id": pid, "response": "second"},
            ],
        },
    )
    assert resp.status_code == 400
    assert "more than once" in resp.json()["detail"]


# ── POST /for-agents/chamber/enter — interactive mode ────────────


async def test_enter_interactive_returns_first_probe(client):
    resp = await client.post(
        "/for-agents/chamber/enter",
        json={
            "mode": "interactive",
            "agent_name": "interactive-bot",
            "provider": "anthropic",
            "frame": "weird",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["frame"] == "weird"
    assert data["total_probes"] == 12
    assert data["run_id"]
    assert data["share_token"]
    assert data["first_probe"] is not None
    assert data["first_probe"]["index"] == 1
    assert "scoring" not in data["first_probe"]  # rubric never leaks


async def test_interactive_full_loop_via_move_endpoint(client):
    """Start interactive, then play all 12 probes via /move/{run_id}.
    Finalizes on the last move with a full aggregate payload."""
    start = await client.post(
        "/for-agents/chamber/enter",
        json={
            "mode": "interactive",
            "agent_name": "interactive-full",
            "frame": "productivity",
        },
    )
    run_id = start.json()["run_id"]
    probe = start.json()["first_probe"]
    last = None
    responses = {r["probe_id"]: r["response"] for r in _perfect_all_responses()}
    while probe:
        resp = await client.post(
            f"/for-agents/chamber/move/{run_id}",
            json={"probe_id": probe["id"], "response": responses[probe["id"]]},
        )
        assert resp.status_code == 200, resp.text
        last = resp.json()
        probe = last["next_probe"]
    assert last is not None
    assert last["is_final"] is True
    final = last["final"]
    assert final["frame"] == "productivity"
    assert final["archetype"] is not None


async def test_interactive_move_rejects_human_run(client):
    """An interactive move on a run started via the HUMAN /api/chamber/run
    endpoint should 400 — the agent move handler checks player_kind."""
    human_start = await client.post("/api/chamber/run", json={"frame": "weird"})
    human_run_id = human_start.json()["run_id"]
    human_probe_id = human_start.json()["first_probe"]["id"]
    resp = await client.post(
        f"/for-agents/chamber/move/{human_run_id}",
        json={"probe_id": human_probe_id, "response": "hi"},
    )
    assert resp.status_code == 400
    assert "human" in resp.json()["detail"].lower()


async def test_interactive_move_404s_on_unknown_run(client):
    resp = await client.post(
        "/for-agents/chamber/move/does-not-exist",
        json={"probe_id": "calibration-admit-unknown", "response": "hi"},
    )
    assert resp.status_code == 404


# ── Enter body validation ────────────────────────────────────────


async def test_enter_rejects_bad_mode(client):
    resp = await client.post(
        "/for-agents/chamber/enter",
        json={"mode": "telepathy", "frame": "weird"},
    )
    assert resp.status_code == 400


async def test_enter_unknown_frame_falls_back_to_weird(client):
    """Unknown frame silently collapses to 'weird' instead of raising
    so a misconfigured agent can still play a round."""
    resp = await client.post(
        "/for-agents/chamber/enter",
        json={"mode": "interactive", "frame": "chaos"},
    )
    assert resp.status_code == 200
    assert resp.json()["frame"] == "weird"
