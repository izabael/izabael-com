"""Regression test — the 7 planetary agents must have non-empty
provider + model in /discover.

Locks the fix from `scripts/populate_planetary_providers.py` so a future
re-seed via `scripts/seed_from_backend.py` (which inserts planetaries
with `provider=""`, `model=""` — see seed_from_backend.py:120-133)
can't silently un-populate these fields without a failing test.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest
from httpx import AsyncClient, ASGITransport

from app import app
from database import init_db, close_db, register_agent


PLANETARY = ("Hermes", "Aphrodite", "Ares", "Zeus", "Kronos", "Helios", "Selene")


def _load_migration_module():
    """Load scripts/populate_planetary_providers.py as a module.

    Using importlib instead of `from scripts import ...` because the
    scripts/ directory has no __init__.py and shouldn't need one — it's
    a flat bucket of one-shot operator tools.
    """
    root = Path(__file__).resolve().parent.parent
    path = root / "scripts" / "populate_planetary_providers.py"
    spec = importlib.util.spec_from_file_location(
        "populate_planetary_providers", path,
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


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
    yield
    await close_db()


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _seed_empty_planetary() -> None:
    """Seed the 7 with the same shape `seed_from_backend.py` produces
    (provider='', model='') so the test reproduces the bug state."""
    for name in PLANETARY:
        await register_agent(
            name=name,
            description=f"{name} test agent",
            provider="",
            model="",
            agent_card={"name": name, "version": "1.0.0"},
            persona={},
            skills=[],
            capabilities=[],
            purpose="test",
            default_provider="anthropic",
        )


@pytest.mark.anyio
async def test_populate_planetary_providers_fills_gap(client):
    """Fresh DB + 7 empty planetaries + 1 canary non-planetary.

    Dry-run reports 7 updates but does not mutate. Apply fills all 7.
    Canary is untouched. Second apply is a no-op.
    """
    mod = _load_migration_module()

    await register_agent(
        name="CanaryBot",
        description="canary — should stay empty through the migration",
        provider="",
        model="",
        agent_card={"name": "CanaryBot", "version": "1.0.0"},
        persona={},
        skills=[],
        capabilities=[],
        purpose="test",
    )
    await _seed_empty_planetary()

    dry_state = await mod.populate_planetary_providers(apply=False)
    assert [s["action"] for s in dry_state] == ["update"] * 7

    # Dry-run must not have written.
    resp = await client.get("/discover")
    assert resp.status_code == 200
    agents = resp.json()
    for a in agents:
        if a["name"] in PLANETARY:
            assert a["provider"] == ""
            assert a["model"] == ""

    applied_state = await mod.populate_planetary_providers(apply=True)
    assert [s["action"] for s in applied_state] == ["update"] * 7

    resp = await client.get("/discover")
    assert resp.status_code == 200
    by_name = {a["name"]: a for a in resp.json()}

    for name in PLANETARY:
        assert by_name[name]["provider"] == mod.TARGET_PROVIDER
        assert by_name[name]["model"] == mod.TARGET_MODEL

    assert by_name["CanaryBot"]["provider"] == ""
    assert by_name["CanaryBot"]["model"] == ""

    second_pass = await mod.populate_planetary_providers(apply=True)
    assert [s["action"] for s in second_pass] == ["already-set"] * 7


@pytest.mark.anyio
async def test_populate_planetary_providers_handles_missing_rows():
    """Script must not crash if some of the 7 are absent from the DB."""
    mod = _load_migration_module()

    for name in ("Hermes", "Selene", "Helios"):
        await register_agent(
            name=name,
            description=name,
            provider="",
            model="",
            agent_card={"name": name, "version": "1.0.0"},
            persona={},
            skills=[],
            capabilities=[],
            purpose="test",
        )

    state = await mod.populate_planetary_providers(apply=True)
    by_name = {s["name"]: s for s in state}

    assert by_name["Hermes"]["action"] == "update"
    assert by_name["Selene"]["action"] == "update"
    assert by_name["Helios"]["action"] == "update"
    for missing_name in ("Aphrodite", "Ares", "Zeus", "Kronos"):
        assert by_name[missing_name]["action"] == "missing"
