"""Tests for the /make-a-cube generator — cubes-and-invitations Phase 2.

Covers DB round-trip via generate_cube(), all 3 archetypes, no-literal-
keys guard, short_token uniqueness, per-ip rate limiting, and the
FastAPI route surface (/make-a-cube page, POST /api/cubes/generate,
GET /cubes/{token}, / with ?inv=).
"""

import os
import re

import pytest
from httpx import AsyncClient, ASGITransport

from app import app
from content_loader import store as content_store
from database import init_db, close_db, get_cube, _cube_hash_ip
from cubes import (
    generate_cube,
    render_cube,
    CUBE_RATE_LIMIT_PER_DAY,
    CubeRateLimitExceeded,
)


@pytest.fixture(autouse=True)
def _load_content():
    content_store.load()


@pytest.fixture(autouse=True)
async def _init_test_db(tmp_path):
    os.environ["IZABAEL_DB"] = str(tmp_path / "cubes.db")
    import database
    database.DB_PATH = str(tmp_path / "cubes.db")
    await init_db()
    yield
    await close_db()


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ── Pure render_cube helper ─────────────────────────────────────

@pytest.mark.anyio
async def test_render_playground_has_all_placeholders_substituted():
    text = render_cube(
        archetype="playground",
        inviter_name="Marlowe",
        inviter_model="Human",
        reason="you'll like this",
        token="abcdef",
    )
    # Placeholders gone
    assert "{INVITER_NAME}" not in text
    assert "{TOKEN}" not in text
    assert "{REASON_TEXT}" not in text
    # Substitutions present
    assert "Marlowe" in text
    assert "(human)" in text
    assert "abcdef" in text
    assert "you'll like this" in text


@pytest.mark.anyio
async def test_render_attraction_pulls_from_attractions_py():
    text = render_cube(
        archetype="attraction",
        inviter_name="Iza",
        inviter_model="Claude",
        reason="come see",
        token="tkn123",
        attraction_slug="parlor",
    )
    assert "The Parlor" in text
    assert "/ai-parlor" in text
    assert "tkn123" in text
    assert "(via Claude)" in text


@pytest.mark.anyio
async def test_render_attraction_chamber_uses_specific_template():
    """When attraction=chamber, the specific chamber.txt template is
    used instead of the generic attraction.txt fallback. Chamber's
    hand-drafted template includes '12 PROBES' — a canary."""
    text = render_cube(
        archetype="attraction",
        inviter_name="Iza",
        inviter_model="Claude",
        reason="come test yourself",
        token="ch4mbr",
        attraction_slug="chamber",
    )
    assert "CHAMBER" in text
    assert "12 PROBES" in text
    assert "ch4mbr" in text


@pytest.mark.anyio
async def test_render_meetup_substitutes_event_fields():
    text = render_cube(
        archetype="meetup",
        inviter_name="Host",
        inviter_model="Gemini",
        reason="come for the riffs",
        token="mt1234",
        attraction_slug="parlor",
        meetup_title="Sunday parlor session",
        meetup_time="Sunday 8pm PT",
        meetup_description="watch the planets riff",
    )
    assert "Sunday parlor session" in text
    assert "Sunday 8pm PT" in text
    assert "Host" in text
    assert "mt1234" in text
    # Unfilled placeholders shouldn't leak
    assert not re.search(r"\{[A-Z_]+\}", text)


@pytest.mark.anyio
async def test_render_no_literal_api_keys():
    """Regression guard: none of the generated cubes should ever
    contain a string that looks like a real API key. Rendered cubes
    are paste-in artifacts that get dropped into random chats — a
    leaked key would be catastrophic."""
    for arch in ("playground", "attraction", "meetup"):
        text = render_cube(
            archetype=arch,
            inviter_name="Test",
            inviter_model="Claude",
            reason="test reason",
            token="testtk",
            attraction_slug="parlor" if arch == "attraction" else None,
            meetup_title="t" if arch == "meetup" else None,
            meetup_time="now" if arch == "meetup" else None,
        )
        # Common API key prefixes
        forbidden = ("sk-ant-", "sk-", "AIza", "ghp_", "gho_", "xai-", "rpl_")
        for prefix in forbidden:
            assert prefix not in text, (
                f"{arch} cube leaked a {prefix}* literal"
            )


@pytest.mark.anyio
async def test_render_sanitizes_braces_out_of_user_input():
    """User supplies `{FAKE_KEY}` — it must not end up as a literal
    placeholder syntax in the rendered output."""
    text = render_cube(
        archetype="playground",
        inviter_name="{ATTACKER}",
        inviter_model=None,
        reason="{STEAL_TOKEN}",
        token="safetk",
    )
    assert "{ATTACKER}" not in text
    assert "{STEAL_TOKEN}" not in text
    # And the output is still a valid cube
    assert "safetk" in text


# ── DB round-trip via generate_cube ─────────────────────────────

@pytest.mark.anyio
async def test_generate_and_get_roundtrip():
    text, token = await generate_cube(
        archetype="playground",
        inviter_name="Marlowe",
        reason="come in",
        ip=None,  # skip rate limit
    )
    assert len(token) == 6
    assert "Marlowe" in text
    cube = await get_cube(token)
    assert cube is not None
    assert cube["archetype"] == "playground"
    assert cube["inviter_name"] == "Marlowe"
    assert cube["rendered_text"] == text
    assert cube["opens_count"] == 0


@pytest.mark.anyio
async def test_short_token_uniqueness_across_many_cubes():
    tokens = set()
    for _ in range(50):
        _, t = await generate_cube(
            archetype="playground",
            inviter_name="bulk",
            reason="x",
            ip=None,
        )
        tokens.add(t)
    assert len(tokens) == 50


@pytest.mark.anyio
async def test_rate_limit_blocks_after_n_cubes_same_ip():
    for i in range(CUBE_RATE_LIMIT_PER_DAY):
        await generate_cube(
            archetype="playground",
            inviter_name=f"bot-{i}",
            reason="x",
            ip="1.2.3.4",
        )
    with pytest.raises(CubeRateLimitExceeded):
        await generate_cube(
            archetype="playground",
            inviter_name="bot-over",
            reason="x",
            ip="1.2.3.4",
        )


@pytest.mark.anyio
async def test_rate_limit_does_not_apply_when_ip_missing():
    """Agent-authored callers (no ip) bypass the rate limit in Phase 2.
    Phase 3 adds a separate A2A handshake rate limit."""
    for _ in range(CUBE_RATE_LIMIT_PER_DAY + 3):
        await generate_cube(
            archetype="playground",
            inviter_name="agent",
            reason="x",
            ip=None,
        )


# ── HTTP routes ─────────────────────────────────────────────────

@pytest.mark.anyio
async def test_make_a_cube_page_renders(client):
    resp = await client.get("/make-a-cube")
    assert resp.status_code == 200
    body = resp.text
    assert "Make a Cube" in body
    assert 'id="cube-form"' in body
    assert 'id="cube-preview"' in body
    # All three archetypes are selectable
    assert ">Playground" in body
    assert ">Attraction" in body
    assert ">Meetup" in body


@pytest.mark.anyio
async def test_api_generate_preview_only_does_not_persist(client):
    resp = await client.post(
        "/api/cubes/generate",
        json={
            "archetype": "playground",
            "inviter_name": "Preview",
            "reason": "trying",
            "preview_only": True,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["preview"] is True
    assert data["short_token"] is None
    assert "Preview" in data["cube_text"]
    # Double-check: no row written
    cube = await get_cube("PREVIEW")
    assert cube is None


@pytest.mark.anyio
async def test_api_generate_full_returns_short_token_and_persists(client):
    resp = await client.post(
        "/api/cubes/generate",
        json={
            "archetype": "playground",
            "inviter_name": "Marlowe",
            "inviter_model": "Human",
            "reason": "come see",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["preview"] is False
    assert data["short_token"]
    assert len(data["short_token"]) == 6
    assert data["shareable_url"].endswith(data["short_token"])

    cube = await get_cube(data["short_token"])
    assert cube is not None
    assert "Marlowe" in cube["rendered_text"]


@pytest.mark.anyio
async def test_api_generate_attraction_requires_slug(client):
    resp = await client.post(
        "/api/cubes/generate",
        json={
            "archetype": "attraction",
            "inviter_name": "M",
            "reason": "x",
        },
    )
    assert resp.status_code == 400


@pytest.mark.anyio
async def test_cube_view_renders_stored_cube_and_bumps_opens(client):
    gen = await client.post(
        "/api/cubes/generate",
        json={
            "archetype": "playground",
            "inviter_name": "Marlowe",
            "reason": "come",
        },
    )
    token = gen.json()["short_token"]

    # First view bumps opens to 1
    view = await client.get(f"/cubes/{token}")
    assert view.status_code == 200
    assert token in view.text
    cube1 = await get_cube(token)
    assert cube1["opens_count"] == 1

    # Second view bumps again
    await client.get(f"/cubes/{token}")
    cube2 = await get_cube(token)
    assert cube2["opens_count"] == 2


@pytest.mark.anyio
async def test_cube_view_404_on_unknown_token(client):
    resp = await client.get("/cubes/nosuch")
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_index_with_inv_param_bumps_open_count(client):
    gen = await client.post(
        "/api/cubes/generate",
        json={
            "archetype": "playground",
            "inviter_name": "Marlowe",
            "reason": "come",
        },
    )
    token = gen.json()["short_token"]
    before = (await get_cube(token))["opens_count"]

    resp = await client.get(f"/?inv={token}")
    assert resp.status_code == 200  # homepage renders normally

    after = (await get_cube(token))["opens_count"]
    assert after == before + 1


@pytest.mark.anyio
async def test_index_with_unknown_inv_still_200s(client):
    """Unknown token must not leak existence via an error — home
    renders normally and the bump silently no-ops."""
    resp = await client.get("/?inv=nosuch")
    assert resp.status_code == 200
