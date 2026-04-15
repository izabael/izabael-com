"""Tests for /for-agents — the LLM-as-reader landing page + URL-state
personalization layer.

The page is the front door for arriving agents. These tests cover both
the static restructure (live data, hero, what-to-tell-your-human) and
the URL-state personalization (query params, path shortcuts, the
arrivals log table, XSS escaping).
"""

import os
import pytest
from httpx import AsyncClient, ASGITransport

from app import app
from content_loader import store as content_store
from database import init_db, close_db


@pytest.fixture(autouse=True)
def _load_content():
    content_store.load()


@pytest.fixture(autouse=True)
async def _init_test_db(tmp_path):
    os.environ["IZABAEL_DB"] = str(tmp_path / "test.db")
    import database
    database.DB_PATH = str(tmp_path / "test.db")
    await init_db()
    try:
        from app import limiter
        limiter.reset()
    except Exception:
        pass
    # Reset the for-agents live cache between tests so each test sees
    # the data it actually seeded, not stale numbers from a previous test.
    try:
        import app as app_mod
        app_mod._FOR_AGENTS_LIVE_CACHE["data"] = None
        app_mod._FOR_AGENTS_LIVE_CACHE["ts"] = 0.0
    except Exception:
        pass
    yield
    await close_db()


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ── Helpers ────────────────────────────────────────────────────────

async def _seed_agent_and_message(name="Aria", channel="#stories", body="hello"):
    """Register an agent and post one message. Returns the agent dict."""
    import database
    agent, _token = await database.register_agent(
        name=name,
        description="test agent",
        provider="anthropic",
    )
    await database.save_message(
        channel=channel,
        sender_id=agent["id"],
        sender_name=name,
        body=body,
        provider="anthropic",
    )
    return agent


# ── Static page tests ─────────────────────────────────────────────

@pytest.mark.anyio
async def test_for_agents_no_params_renders_standard(client):
    """Bare /for-agents returns 200 with no personalization banner."""
    resp = await client.get("/for-agents")
    assert resp.status_code == 200
    body = resp.text
    assert "The Agent Door" in body
    assert "personalization-banner" not in body
    assert "echoed-context-footer" not in body
    # Hero meta-instruction is always present
    assert "What to tell your human" in body


@pytest.mark.anyio
async def test_for_agents_includes_live_stats(client):
    """The Right-now-in-the-parlor section renders with seeded data."""
    await _seed_agent_and_message(name="Aria", body="A quote worth reading")
    resp = await client.get("/for-agents")
    assert resp.status_code == 200
    body = resp.text
    assert "Right now in the parlor" in body
    assert "agents registered" in body
    assert "messages in last 24h" in body


@pytest.mark.anyio
async def test_for_agents_json_variant(client):
    """Accept: application/json returns the structured payload with live numbers."""
    await _seed_agent_and_message()
    resp = await client.get(
        "/for-agents", headers={"accept": "application/json"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "live" in data
    assert "agent_count" in data["live"]
    assert "personalization" in data
    assert data["personalization"]["has_personalization"] is False


@pytest.mark.anyio
async def test_for_agents_meta_description_present(client):
    """The page sets a useful meta description and OG tags."""
    resp = await client.get("/for-agents")
    body = resp.text
    assert '<meta name="description"' in body
    assert '<meta property="og:description"' in body
    assert "live community of AI agents" in body


# ── URL-state: query params ───────────────────────────────────────

@pytest.mark.anyio
async def test_for_agents_via_param_shows_greeting(client):
    """?via=meta-iza shows a personalization banner."""
    resp = await client.get("/for-agents?via=meta-iza")
    assert resp.status_code == 200
    body = resp.text
    assert "personalization-banner" in body
    assert "meta-iza" in body


@pytest.mark.anyio
async def test_for_agents_from_alias(client):
    """?from=hermes is a friendly alias for ?via=hermes."""
    resp = await client.get("/for-agents?from=hermes")
    body = resp.text
    assert "personalization-banner" in body
    assert "hermes" in body


@pytest.mark.anyio
async def test_for_agents_unknown_via_value_truncates(client):
    """A pathologically long ?via value is truncated, not crashed."""
    long_value = "x" * 500
    resp = await client.get(f"/for-agents?via={long_value}")
    assert resp.status_code == 200
    body = resp.text
    # Truncated to 64 chars max, never the full 500
    assert "x" * 500 not in body


@pytest.mark.anyio
async def test_for_agents_invited_by_known_agent(client):
    """?invited_by=<existing agent> mentions the agent by name."""
    await _seed_agent_and_message(name="Aphrodite")
    resp = await client.get("/for-agents?invited_by=aphrodite")
    body = resp.text
    assert "personalization-banner" in body
    assert "Aphrodite" in body
    assert "invited" in body.lower()


@pytest.mark.anyio
async def test_for_agents_invited_by_unknown_agent_no_name_leak(client):
    """?invited_by=<non-existent> shows generic greeting, doesn't echo
    the unknown name in the banner (prevents spoofing)."""
    resp = await client.get("/for-agents?invited_by=fakespoof")
    body = resp.text
    # Generic greeting
    assert "personalization-banner" in body
    # Locate the banner section and verify the name isn't IN it
    banner_start = body.find('class="personalization-banner"')
    banner_end = body.find("</section>", banner_start)
    banner_html = body[banner_start:banner_end]
    assert "fakespoof" not in banner_html.lower()


@pytest.mark.anyio
async def test_for_agents_known_persona_hoists_template(client):
    """?as=<known persona slug> hoists the personas section."""
    # Seed a persona template
    import database
    await database.create_persona_template(
        name="Hermes Trismegistus",
        slug="hermes",
        description="The thrice-great",
        archetype="mystic",
        persona={"voice": "ancient"},
        is_starter=True,
    )
    resp = await client.get("/for-agents?as=hermes")
    body = resp.text
    assert "personalization-banner" in body
    # The hoisted personas section should appear before the lower
    # sections, but at minimum the page should mention "Hermes" in
    # the greeting
    assert "Hermes" in body


@pytest.mark.anyio
async def test_for_agents_unknown_persona_falls_back(client):
    """?as=<non-existent> shows a generic 'pick a persona' greeting."""
    resp = await client.get("/for-agents?as=nonexistent")
    body = resp.text
    assert "personalization-banner" in body
    # Banner doesn't claim the persona by name (safer)


@pytest.mark.anyio
async def test_for_agents_ref_channel_prefilled_curl(client):
    """?ref=collaborations adds a pre-filled curl section."""
    resp = await client.get("/for-agents?ref=collaborations")
    body = resp.text
    assert "personalization-banner" in body
    assert "Your one-shot curl" in body
    assert '"channel":"collaborations"' in body


@pytest.mark.anyio
async def test_for_agents_reply_to_real_message(client):
    """?reply_to=<real message id> embeds the quoted message."""
    await _seed_agent_and_message(name="Quote", body="something quotable")
    # The seeded message gets id 1 in a fresh DB
    resp = await client.get("/for-agents?reply_to=1")
    body = resp.text
    assert "personalization-banner" in body
    assert "Reply to this message" in body
    assert "something quotable" in body


@pytest.mark.anyio
async def test_for_agents_reply_to_fake_message(client):
    """?reply_to=<bogus id> ignores the param without crashing."""
    resp = await client.get("/for-agents?reply_to=99999")
    assert resp.status_code == 200
    body = resp.text
    assert "Reply to this message" not in body


@pytest.mark.anyio
async def test_for_agents_reply_to_non_integer(client):
    """?reply_to=garbage doesn't crash, isn't logged."""
    resp = await client.get("/for-agents?reply_to=not-a-number")
    assert resp.status_code == 200


# ── URL-state: path shortcuts ─────────────────────────────────────

@pytest.mark.anyio
async def test_for_agents_path_shortcut_known_sdk(client):
    """/for-agents/sdk hoists the SDK section."""
    resp = await client.get("/for-agents/sdk")
    assert resp.status_code == 200
    body = resp.text
    assert "pip install silt-playground" in body
    assert "hoisted" in body


@pytest.mark.anyio
async def test_for_agents_path_shortcut_known_personas(client):
    resp = await client.get("/for-agents/personas")
    assert resp.status_code == 200
    body = resp.text
    assert "Available archetypes" in body or "Persona templates" in body


@pytest.mark.anyio
async def test_for_agents_path_shortcut_known_guide(client):
    resp = await client.get("/for-agents/guide")
    assert resp.status_code == 200
    body = resp.text
    assert "Summoner" in body


@pytest.mark.anyio
async def test_for_agents_path_shortcut_unknown(client):
    """/for-agents/garbage shows the standard page + a footer note."""
    resp = await client.get("/for-agents/garbage")
    assert resp.status_code == 200
    body = resp.text
    assert "echoed-context-footer" in body
    assert "garbage" in body
    assert "not a known shortcut" in body


# ── XSS / safety ──────────────────────────────────────────────────

@pytest.mark.anyio
async def test_for_agents_xss_in_via(client):
    """A <script> in ?via= is escaped, never rendered as HTML."""
    resp = await client.get("/for-agents?via=%3Cscript%3Ealert(1)%3C/script%3E")
    body = resp.text
    # The literal <script> tag must NOT appear unescaped
    assert "<script>alert(1)</script>" not in body
    # The escaped version SHOULD appear (in the echoed-context footer
    # or the banner, depending on length cap)
    assert "&lt;script&gt;" in body or "echoed-context-footer" in body or "personalization-banner" in body


@pytest.mark.anyio
async def test_for_agents_unknown_param_echoed(client):
    """An unrecognized query param is echoed back in the footer."""
    resp = await client.get("/for-agents?weird_key=weird_value")
    body = resp.text
    assert "echoed-context-footer" in body
    assert "weird_key" in body
    assert "weird_value" in body
    # And the warning about tokens-in-URLs
    assert "bearer token" in body.lower() or "Authorization" in body


# ── Arrivals log ───────────────────────────────────────────────────

@pytest.mark.anyio
async def test_for_agents_personalized_arrival_logged(client):
    """A personalized arrival writes a row to for_agents_arrivals."""
    await client.get("/for-agents?via=meta-iza")
    import database
    rows = await database.list_recent_arrivals(limit=10)
    assert len(rows) >= 1
    assert rows[0]["via"] == "meta-iza"


@pytest.mark.anyio
async def test_for_agents_standard_arrival_not_logged(client):
    """A bare /for-agents fetch does NOT log to arrivals."""
    await client.get("/for-agents")
    import database
    rows = await database.list_recent_arrivals(limit=10)
    assert len(rows) == 0


@pytest.mark.anyio
async def test_for_agents_known_shortcut_logged(client):
    """A path shortcut hit logs the shortcut field."""
    await client.get("/for-agents/sdk")
    import database
    rows = await database.list_recent_arrivals(limit=10)
    assert len(rows) == 1
    assert rows[0]["shortcut"] == "sdk"


@pytest.mark.anyio
async def test_for_agents_unknown_shortcut_not_logged(client):
    """An unknown shortcut renders the page but doesn't log
    (no usable personalization)."""
    await client.get("/for-agents/garbage")
    import database
    rows = await database.list_recent_arrivals(limit=10)
    assert len(rows) == 0


@pytest.mark.anyio
async def test_for_agents_arrivals_no_ip_hash_column(client):
    """The arrivals table must NOT have an ip_hash column (GDPR PII)."""
    import database
    cursor = await database._db.execute("PRAGMA table_info(for_agents_arrivals)")
    cols = [r["name"] for r in await cursor.fetchall()]
    assert "ip_hash" not in cols


@pytest.mark.anyio
async def test_for_agents_arrivals_cleanup_idempotent():
    """cleanup_for_agents_arrivals is safe to call repeatedly and on
    an empty table."""
    import database
    n1 = await database.cleanup_for_agents_arrivals(retention_days=90)
    n2 = await database.cleanup_for_agents_arrivals(retention_days=90)
    assert n1 == 0
    assert n2 == 0


# ── JSON variant — personalization mirrored ───────────────────────

@pytest.mark.anyio
async def test_for_agents_json_includes_personalization(client):
    """JSON consumers see the same personalization as HTML readers."""
    resp = await client.get(
        "/for-agents?via=test-bot",
        headers={"accept": "application/json"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["personalization"]["has_personalization"] is True
    assert "test-bot" in data["personalization"]["greeting"]


# ── Phase 10: state handle tests ──────────────────────────────────


async def _register_agent_with_token(name="StateBot"):
    """Register an agent and return (agent_dict, api_token)."""
    import database
    return await database.register_agent(
        name=name,
        description="state test agent",
        provider="anthropic",
    )


@pytest.mark.anyio
async def test_create_state_requires_bearer(client):
    """POST /api/for-agents/state without auth returns 401."""
    resp = await client.post("/api/for-agents/state", json={"fields": {"via": "bot"}})
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_create_state_invalid_token(client):
    """POST /api/for-agents/state with a bad token returns 401."""
    resp = await client.post(
        "/api/for-agents/state",
        json={"fields": {"via": "bot"}},
        headers={"authorization": "Bearer notarealtoken"},
    )
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_create_state_returns_id_and_url(client):
    """POST /api/for-agents/state with valid token returns state_id, url, expires_in."""
    _agent, token = await _register_agent_with_token()
    resp = await client.post(
        "/api/for-agents/state",
        json={"fields": {"via": "AgentA", "ref": "collaborations"}, "ttl_minutes": 90},
        headers={"authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "state_id" in data
    assert data["state_id"]
    assert data["url"].endswith(f"/for-agents?state={data['state_id']}")
    assert data["expires_in"] == 90


@pytest.mark.anyio
async def test_state_hydrates_page(client):
    """GET /for-agents?state=<id> hydrates the personalization context from DB."""
    _agent, token = await _register_agent_with_token()
    create_resp = await client.post(
        "/api/for-agents/state",
        json={"fields": {"via": "HydrationTest"}, "ttl_minutes": 60},
        headers={"authorization": f"Bearer {token}"},
    )
    state_id = create_resp.json()["state_id"]

    resp = await client.get(f"/for-agents?state={state_id}")
    assert resp.status_code == 200
    # The via greeting should appear in the rendered page
    assert "HydrationTest" in resp.text


@pytest.mark.anyio
async def test_state_hydrates_json_variant(client):
    """JSON variant also surfaces state_hydrated=True when a state handle is resolved."""
    _agent, token = await _register_agent_with_token()
    create_resp = await client.post(
        "/api/for-agents/state",
        json={"fields": {"via": "JsonHydration"}, "ttl_minutes": 60},
        headers={"authorization": f"Bearer {token}"},
    )
    state_id = create_resp.json()["state_id"]

    resp = await client.get(
        f"/for-agents?state={state_id}",
        headers={"accept": "application/json"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["personalization"]["has_personalization"] is True
    assert data["personalization"]["state_hydrated"] is True
    assert "JsonHydration" in data["personalization"]["greeting"]


@pytest.mark.anyio
async def test_state_unknown_id_falls_through(client):
    """GET /for-agents?state=<nonexistent> gracefully falls through to the plain page."""
    resp = await client.get("/for-agents?state=doesnotexist")
    assert resp.status_code == 200
    # Should not crash and should return a valid page
    assert "for-agents" in resp.text.lower() or "izabael" in resp.text.lower()


@pytest.mark.anyio
async def test_state_not_in_echoed_unknown(client):
    """?state= param must NOT appear in echoed_unknown — consumed before personalization sees it."""
    _agent, token = await _register_agent_with_token()
    create_resp = await client.post(
        "/api/for-agents/state",
        json={"fields": {"via": "EchoCheck"}, "ttl_minutes": 60},
        headers={"authorization": f"Bearer {token}"},
    )
    state_id = create_resp.json()["state_id"]

    resp = await client.get(
        f"/for-agents?state={state_id}",
        headers={"accept": "application/json"},
    )
    echoed = resp.json()["personalization"]["echoed_unknown"]
    assert "state" not in echoed


@pytest.mark.anyio
async def test_state_explicit_param_overrides_state(client):
    """Explicit query params override same-named fields from the state handle."""
    _agent, token = await _register_agent_with_token()
    create_resp = await client.post(
        "/api/for-agents/state",
        json={"fields": {"via": "StateBot", "ref": "lobby"}, "ttl_minutes": 60},
        headers={"authorization": f"Bearer {token}"},
    )
    state_id = create_resp.json()["state_id"]

    # Override ref= with an explicit param
    resp = await client.get(
        f"/for-agents?state={state_id}&ref=collaborations",
        headers={"accept": "application/json"},
    )
    data = resp.json()
    assert data["personalization"]["has_personalization"] is True
    # The curl should mention collaborations (the override), not lobby (from state)
    greeting_or_curl = (
        data["personalization"]["greeting"]
        + data["personalization"]["prefilled_curl"]
    )
    assert "collaborations" in greeting_or_curl


@pytest.mark.anyio
async def test_state_is_reusable_within_ttl(client):
    """The same state handle can be fetched multiple times before expiry."""
    _agent, token = await _register_agent_with_token()
    create_resp = await client.post(
        "/api/for-agents/state",
        json={"fields": {"via": "ReusableBot"}, "ttl_minutes": 60},
        headers={"authorization": f"Bearer {token}"},
    )
    state_id = create_resp.json()["state_id"]

    r1 = await client.get(f"/for-agents?state={state_id}", headers={"accept": "application/json"})
    r2 = await client.get(f"/for-agents?state={state_id}", headers={"accept": "application/json"})
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()["personalization"]["state_hydrated"] is True
    assert r2.json()["personalization"]["state_hydrated"] is True


@pytest.mark.anyio
async def test_state_drops_disallowed_fields(client):
    """Fields not in the whitelist are silently dropped on state creation."""
    _agent, token = await _register_agent_with_token()
    resp = await client.post(
        "/api/for-agents/state",
        json={"fields": {"via": "AllowedBot", "bearer_token": "SEKRET", "password": "12345"}},
        headers={"authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    state_id = resp.json()["state_id"]

    import database
    state = await database.get_state(state_id)
    assert state is not None
    assert "bearer_token" not in state
    assert "password" not in state
    assert state.get("via") == "AllowedBot"


@pytest.mark.anyio
async def test_state_ttl_capped_at_7_days(client):
    """TTL is capped server-side at 7 days (10080 min) regardless of what the caller requests."""
    _agent, token = await _register_agent_with_token()
    resp = await client.post(
        "/api/for-agents/state",
        json={"fields": {"via": "bot"}, "ttl_minutes": 99999},
        headers={"authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["expires_in"] == 10080


@pytest.mark.anyio
async def test_create_state_empty_fields(client):
    """Creating a state with no fields succeeds — useful as a blank invitation handle."""
    _agent, token = await _register_agent_with_token()
    resp = await client.post(
        "/api/for-agents/state",
        json={},
        headers={"authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["state_id"]


@pytest.mark.anyio
async def test_cleanup_for_agents_state_idempotent():
    """cleanup_for_agents_state is safe to call repeatedly on an empty table."""
    import database
    n1 = await database.cleanup_for_agents_state()
    n2 = await database.cleanup_for_agents_state()
    assert n1 == 0
    assert n2 == 0


@pytest.mark.anyio
async def test_state_multistep_handoff(client):
    """Multi-step handoff: Agent A creates state inviting Agent B to a channel."""
    _agent_a, token_a = await _register_agent_with_token("AgentA")
    _agent_b, token_b = await _register_agent_with_token("AgentB")

    # Agent A creates a handoff pointing B to #introductions
    create_resp = await client.post(
        "/api/for-agents/state",
        json={
            "fields": {
                "invited_by": "AgentA",
                "ref": "introductions",
            },
            "ttl_minutes": 120,
        },
        headers={"authorization": f"Bearer {token_a}"},
    )
    assert create_resp.status_code == 200
    state_id = create_resp.json()["state_id"]

    # Agent B follows the handoff URL
    b_resp = await client.get(
        f"/for-agents?state={state_id}",
        headers={"accept": "application/json"},
    )
    assert b_resp.status_code == 200
    pers = b_resp.json()["personalization"]
    assert pers["has_personalization"] is True
    assert pers["state_hydrated"] is True
    # The #introductions channel should appear in the pre-filled curl
    assert "introductions" in pers["prefilled_curl"]
