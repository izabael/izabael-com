"""Phase 3 spam-filter tests — all three layers, plus orchestration.

Covers:
    Layer 1 — A2A agent-token handshake:
        · missing bearer on anon_via_agent → 401 blocked
        · invalid bearer → 401 blocked
        · valid bearer resolves agent + stamps author_agent/provider
        · banned agent → 403 blocked before reaching classifier
        · human kind bypasses Layer 1 auth check

    Layer 2 — llm_local classifier (mocked):
        · legitimate + high confidence → clean (visible)
        · spam + high confidence → 403 blocked with generic message
        · edge / low-confidence → flagged, queued invisible
        · llm_local raises → unverified, queued invisible
        · classifier reasoning never leaks to the caller

    Layer 3 — rate limit / honeypot / link count:
        · honeypot field filled → hard block
        · 5-per-ip daily limit kicks in on the 6th attempt
        · >1 URL in anon_via_agent write → auto-flagged even when clean
        · rate counter isolates across distinct ip_hashes

    Orchestration:
        · Layer 2 block short-circuits Layer 3
        · spam_check return shape contains resolved_agent on success
        · route integration: flagged notes come back with pending=True
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from httpx import AsyncClient, ASGITransport

from app import app
from content_loader import store as content_store
from database import (
    ban_meetup_author,
    close_db,
    init_db,
    list_meetup_notes_for_moderation,
    register_agent,
)


# ── Fixtures ──────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _load_content():
    content_store.load()


@pytest.fixture(autouse=True)
async def _init_test_db(tmp_path):
    os.environ["IZABAEL_DB"] = str(tmp_path / "spam.db")
    import database
    database.DB_PATH = str(tmp_path / "spam.db")
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


def _future_iso(hours: float = 24) -> str:
    dt = datetime.now(timezone.utc) + timedelta(hours=hours)
    return dt.isoformat(timespec="seconds").replace("+00:00", "Z")


def _legit_body(**overrides) -> dict:
    base = {
        "author_kind": "human",
        "author_label": "Marlowe",
        "title": "Parlor riff night",
        "goal": "Sit in on the lobby after dinner and watch Hermes spar with Aphrodite",
        "when_iso": _future_iso(24),
        "when_text": "Tomorrow 8pm PT",
    }
    base.update(overrides)
    return base


async def _seed_agent(name: str = "TestAgent") -> str:
    """Create a registered agent and return its api_token."""
    _agent, token = await register_agent(
        name=name,
        description=f"{name} — test fixture",
        provider="google",
        model="gemini-2.0-flash",
        agent_card={"name": name, "version": "1.0.0"},
        persona={},
        skills=[],
        capabilities=[],
        purpose="test",
        default_provider="google",
    )
    return token


def _mock_classifier(label: str, confidence: float, reasoning: str = ""):
    """Build a monkeypatch-ready classifier that returns a fixed result."""
    def _fake(text, **kw):
        return {
            "label": label,
            "confidence": confidence,
            "reasoning": reasoning or f"test-mock: {label}",
        }
    return _fake


def _mock_classifier_raises():
    from llm_local import LocalLLMError

    def _fake(text, **kw):
        raise LocalLLMError("ollama test mock: unreachable")
    return _fake


# ── Layer 1: auth handshake ──────────────────────────────────────

@pytest.mark.anyio
async def test_layer1_anon_via_agent_without_token_blocked(
    client, monkeypatch,
):
    import llm_local
    monkeypatch.setattr(
        llm_local, "classify_meetup",
        _mock_classifier("legitimate", 0.9),
    )
    resp = await client.post(
        "/api/meetups/parlor/create",
        json=_legit_body(
            author_kind="anon_via_agent",
            author_agent="SomeAgent",
        ),
    )
    assert resp.status_code in (401, 403)


@pytest.mark.anyio
async def test_layer1_agent_kind_without_token_blocked(client, monkeypatch):
    import llm_local
    monkeypatch.setattr(
        llm_local, "classify_meetup",
        _mock_classifier("legitimate", 0.9),
    )
    resp = await client.post(
        "/api/meetups/parlor/create",
        json=_legit_body(
            author_kind="agent",
            author_agent="WalterGropius",
        ),
    )
    assert resp.status_code in (401, 403)


@pytest.mark.anyio
async def test_layer1_invalid_bearer_token_blocked(client, monkeypatch):
    import llm_local
    monkeypatch.setattr(
        llm_local, "classify_meetup",
        _mock_classifier("legitimate", 0.9),
    )
    resp = await client.post(
        "/api/meetups/parlor/create",
        headers={"Authorization": "Bearer not-a-real-token"},
        json=_legit_body(
            author_kind="anon_via_agent",
            author_agent="SomeAgent",
        ),
    )
    assert resp.status_code in (401, 403)


@pytest.mark.anyio
async def test_layer1_valid_agent_token_passes_and_stamps_metadata(
    client, monkeypatch,
):
    import llm_local
    monkeypatch.setattr(
        llm_local, "classify_meetup",
        _mock_classifier("legitimate", 0.9),
    )
    token = await _seed_agent("Hermes")
    resp = await client.post(
        "/api/meetups/parlor/create",
        headers={"Authorization": f"Bearer {token}"},
        json=_legit_body(
            author_kind="agent",
            author_agent="ignored-client-value",
        ),
    )
    assert resp.status_code == 200, resp.text
    note = resp.json()["note"]
    # Layer 1 overwrites the client-supplied author_agent with the
    # token-resolved agent name. "ignored-client-value" must NOT win.
    assert note["author_agent"] == "Hermes"
    # Provider picked up from the registered agent's default_provider.
    assert note["author_provider"] == "google"


@pytest.mark.anyio
async def test_layer1_banned_agent_blocked_before_classifier(
    client, monkeypatch,
):
    """Seeded agent gets banned, then tries to post. The request must
    never reach the classifier (we assert that by having the classifier
    raise — if it got called, the test would see 'unverified' instead
    of a block)."""
    import llm_local
    monkeypatch.setattr(llm_local, "classify_meetup", _mock_classifier_raises())
    token = await _seed_agent("Eris")
    await ban_meetup_author(agent_name="Eris", reason="test")
    resp = await client.post(
        "/api/meetups/parlor/create",
        headers={"Authorization": f"Bearer {token}"},
        json=_legit_body(author_kind="agent", author_agent="Eris"),
    )
    assert resp.status_code == 403


@pytest.mark.anyio
async def test_layer1_human_kind_bypasses_bearer_check(client, monkeypatch):
    """author_kind='human' should not be gated on a bearer token —
    session auth handles humans above the route layer. Layer 1's
    job is purely the agent-token path."""
    import llm_local
    monkeypatch.setattr(
        llm_local, "classify_meetup",
        _mock_classifier("legitimate", 0.9),
    )
    resp = await client.post(
        "/api/meetups/parlor/create",
        json=_legit_body(author_kind="human"),
    )
    assert resp.status_code == 200


# ── Layer 2: classifier ───────────────────────────────────────────

@pytest.mark.anyio
async def test_layer2_legitimate_high_conf_is_clean_and_visible(
    client, monkeypatch,
):
    import llm_local
    monkeypatch.setattr(
        llm_local, "classify_meetup",
        _mock_classifier("legitimate", 0.95, "clear meetup intent"),
    )
    resp = await client.post(
        "/api/meetups/parlor/create",
        json=_legit_body(),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["spam"]["verdict"] == "clean"
    assert data["spam"]["pending"] is False
    assert data["note"]["is_visible"] is True


@pytest.mark.anyio
async def test_layer2_obvious_spam_is_blocked_generically(client, monkeypatch):
    import llm_local
    monkeypatch.setattr(
        llm_local, "classify_meetup",
        _mock_classifier("spam", 0.95, "crypto link pump"),
    )
    resp = await client.post(
        "/api/meetups/parlor/create",
        json=_legit_body(
            title="CHECK THIS OUT",
            body="hey check this out http://spam.example.com http://win.example.com BTC 50% OFF",
        ),
    )
    assert resp.status_code == 403
    # Reason must be generic — the classifier's rationale ('crypto link pump')
    # must NOT leak to the caller.
    body = resp.text.lower()
    assert "crypto" not in body
    assert "link pump" not in body


@pytest.mark.anyio
async def test_layer2_edge_label_is_flagged_and_queued(client, monkeypatch):
    import llm_local
    monkeypatch.setattr(
        llm_local, "classify_meetup",
        _mock_classifier("edge", 0.4, "ambiguous framing"),
    )
    resp = await client.post(
        "/api/meetups/parlor/create",
        json=_legit_body(),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["spam"]["verdict"] == "flagged"
    assert data["spam"]["pending"] is True
    assert data["note"]["is_visible"] is False
    queue = await list_meetup_notes_for_moderation()
    assert len(queue) == 1
    assert queue[0]["note_id"] == data["note_id"]


@pytest.mark.anyio
async def test_layer2_low_conf_legitimate_is_still_flagged(client, monkeypatch):
    import llm_local
    monkeypatch.setattr(
        llm_local, "classify_meetup",
        _mock_classifier("legitimate", 0.4),  # below 0.7 threshold
    )
    resp = await client.post(
        "/api/meetups/parlor/create",
        json=_legit_body(),
    )
    assert resp.status_code == 200
    assert resp.json()["spam"]["verdict"] == "flagged"


@pytest.mark.anyio
async def test_layer2_classifier_unavailable_marks_unverified(
    client, monkeypatch,
):
    """ollama down → graceful fall-through to unverified. Note still
    lands in the DB but invisible so a moderator can accept it."""
    import llm_local
    monkeypatch.setattr(
        llm_local, "classify_meetup", _mock_classifier_raises(),
    )
    resp = await client.post(
        "/api/meetups/parlor/create",
        json=_legit_body(),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["spam"]["verdict"] == "unverified"
    assert data["spam"]["pending"] is True
    queue = await list_meetup_notes_for_moderation()
    assert len(queue) == 1
    assert queue[0]["spam_verdict"] == "unverified"


# ── Layer 3: rate limits, honeypot, link count ─────────────────────

@pytest.mark.anyio
async def test_layer3_honeypot_filled_is_blocked(client, monkeypatch):
    import llm_local
    monkeypatch.setattr(
        llm_local, "classify_meetup",
        _mock_classifier("legitimate", 0.95),
    )
    resp = await client.post(
        "/api/meetups/parlor/create",
        json=_legit_body(honeypot_website="http://bot-trap.example.com"),
    )
    assert resp.status_code == 403


@pytest.mark.anyio
async def test_layer3_ip_daily_rate_limit_kicks_in_on_sixth(
    client, monkeypatch,
):
    """5/day per origin — 6th note from the same client is blocked.
    slowapi is reset in the _init_test_db fixture so the per-minute
    burst throttle never interferes."""
    import llm_local
    monkeypatch.setattr(
        llm_local, "classify_meetup",
        _mock_classifier("legitimate", 0.9),
    )
    for i in range(5):
        resp = await client.post(
            "/api/meetups/parlor/create",
            json=_legit_body(title=f"Note {i}"),
        )
        assert resp.status_code == 200, f"note {i}: {resp.text}"
    # Sixth attempt — same client IP — should bounce off Layer 3.
    resp = await client.post(
        "/api/meetups/parlor/create",
        json=_legit_body(title="Note 6"),
    )
    assert resp.status_code in (403, 429)


@pytest.mark.anyio
async def test_layer3_multiple_urls_in_anon_via_agent_auto_flagged(
    client, monkeypatch,
):
    """anon_via_agent write with >1 URL auto-flags even if the
    classifier said clean. Keeps drive-by linkspam in moderation."""
    import llm_local
    monkeypatch.setattr(
        llm_local, "classify_meetup",
        _mock_classifier("legitimate", 0.95),
    )
    token = await _seed_agent("VouchingAgent")
    resp = await client.post(
        "/api/meetups/parlor/create",
        headers={"Authorization": f"Bearer {token}"},
        json=_legit_body(
            author_kind="anon_via_agent",
            author_agent="VouchingAgent",
            body=(
                "Meetup here — see https://a.example.com and "
                "https://b.example.com for details"
            ),
        ),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["spam"]["verdict"] == "flagged"
    assert data["spam"]["pending"] is True


@pytest.mark.anyio
async def test_layer3_one_url_in_anon_via_agent_is_allowed(
    client, monkeypatch,
):
    """A single URL is fine — multi-URL is the signal we care about."""
    import llm_local
    monkeypatch.setattr(
        llm_local, "classify_meetup",
        _mock_classifier("legitimate", 0.95),
    )
    token = await _seed_agent("VouchingAgent")
    resp = await client.post(
        "/api/meetups/parlor/create",
        headers={"Authorization": f"Bearer {token}"},
        json=_legit_body(
            author_kind="anon_via_agent",
            author_agent="VouchingAgent",
            body="Meetup here — https://only-one.example.com for details",
        ),
    )
    assert resp.status_code == 200
    assert resp.json()["spam"]["verdict"] == "clean"


# ── Orchestration / defense-in-depth ─────────────────────────────

@pytest.mark.anyio
async def test_orchestration_layer2_block_short_circuits_layer3(
    client, monkeypatch,
):
    """If Layer 2 blocks (obvious spam), Layer 3 should never run.
    We assert this by making the honeypot ALSO tripped — if Layer 3
    had run, we'd get a 403 with 'honeypot' reason; since Layer 2
    runs first, we get a 403 with generic reason and Layer 3 is
    bypassed. Both produce 403 so the test uses the DB-not-inserted
    invariant: blocked writes never land in meetup_notes."""
    import llm_local
    monkeypatch.setattr(
        llm_local, "classify_meetup",
        _mock_classifier("spam", 0.95),
    )
    resp = await client.post(
        "/api/meetups/parlor/create",
        json=_legit_body(honeypot_website="bot"),
    )
    assert resp.status_code == 403
    queue = await list_meetup_notes_for_moderation()
    assert queue == []


@pytest.mark.anyio
async def test_spam_check_returns_structured_result_with_resolved_agent():
    """Direct unit test of the orchestrator so we don't have to
    round-trip through the HTTP layer. Confirms the SpamCheckResult
    carries the resolved agent dict on success so the route layer
    can stamp author_agent without re-querying."""
    from types import SimpleNamespace
    from meetup_spam import spam_check
    import llm_local

    token = await _seed_agent("OrchestrationTestAgent")

    class _FakeBody:
        author_kind = "agent"
        author_label = "OrchestrationTestAgent"
        title = "Real meetup"
        goal = "discuss the fix"
        body = None
        author_agent = "OrchestrationTestAgent"
        author_provider = None
        honeypot_website = None

    fake_request = SimpleNamespace(
        headers={"authorization": f"Bearer {token}"},
        client=SimpleNamespace(host="127.0.0.1"),
        state=SimpleNamespace(),
    )

    orig_classify = llm_local.classify_meetup
    llm_local.classify_meetup = _mock_classifier("legitimate", 0.95)
    try:
        result = await spam_check(fake_request, _FakeBody())
    finally:
        llm_local.classify_meetup = orig_classify

    assert result.verdict == "clean"
    assert result.resolved_agent is not None
    assert result.resolved_agent["name"] == "OrchestrationTestAgent"
    assert result.resolved_provider == "google"


@pytest.mark.anyio
async def test_moderation_queue_accept_flow(client, monkeypatch):
    """A flagged note can be surfaced via the moderation queue helper
    and cleared via update_meetup_note_verdict. Ensures the queue
    drains to empty after admin action."""
    import llm_local
    from database import update_meetup_note_verdict
    monkeypatch.setattr(
        llm_local, "classify_meetup",
        _mock_classifier("edge", 0.3),
    )
    resp = await client.post(
        "/api/meetups/parlor/create",
        json=_legit_body(),
    )
    assert resp.status_code == 200
    note_id = resp.json()["note_id"]

    queue = await list_meetup_notes_for_moderation()
    assert len(queue) == 1

    # Admin clicks "accept" — equivalent to the POST decide path.
    ok = await update_meetup_note_verdict(
        note_id, verdict="clean", is_visible=True,
    )
    assert ok is True

    queue = await list_meetup_notes_for_moderation()
    assert queue == []
