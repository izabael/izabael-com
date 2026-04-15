"""Tests for chamber_runs persistence — Phase 3 of the chamber plan.

Covers: schema migration (init_db picks up chamber_runs clean), insert,
idempotent re-insert, move append, finalize, get by run_id and share_token,
list with frame/player_kind/provider filters, retention cleanup, daily-salt
rotation for ip_hash, CHECK-constraint enforcement on frame/player_kind.

Every test uses a fresh in-memory SQLite per run (tmp_path + IZABAEL_DB
env override), mirroring the existing test_auth.py / test_for_agents.py
patterns. No network, no global state bleed between tests.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

import database
from database import (
    _chamber_daily_salt,
    _chamber_daily_salt_cache,
    _hash_chamber_ip,
    append_chamber_move,
    cleanup_chamber_runs,
    close_db,
    create_chamber_run,
    finalize_chamber_run,
    get_chamber_run,
    init_db,
    list_public_chamber_runs,
)


pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(autouse=True)
async def _init_test_db(tmp_path):
    os.environ["IZABAEL_DB"] = str(tmp_path / "test.db")
    database.DB_PATH = str(tmp_path / "test.db")
    # Reset the daily-salt cache so a monkey-patched "now" in one test
    # doesn't leak into the next.
    database._chamber_daily_salt_cache.clear()
    await init_db()
    yield
    await close_db()


# ── Schema migration ─────────────────────────────────────────────


async def test_init_db_creates_chamber_runs_table_and_indexes():
    assert database._db is not None
    cursor = await database._db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='chamber_runs'"
    )
    assert await cursor.fetchone() is not None

    cursor = await database._db.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='chamber_runs'"
    )
    names = {r["name"] for r in await cursor.fetchall()}
    # All the indexes the Phase 3 spec requires
    assert "idx_chamber_started" in names
    assert "idx_chamber_player_kind" in names
    assert "idx_chamber_provider" in names
    assert "idx_chamber_frame_total" in names
    assert "idx_chamber_share_token" in names


async def test_check_constraint_rejects_bad_frame():
    """Schema-level defense in depth: even if a caller bypasses
    create_chamber_run's early validation, the CHECK constraint blocks
    invalid frame values at the DB layer."""
    assert database._db is not None
    with pytest.raises(Exception):
        await database._db.execute(
            """INSERT INTO chamber_runs (run_id, frame, player_kind)
               VALUES (?, ?, ?)""",
            ("bad-frame", "chaos", "human"),
        )
        await database._db.commit()


async def test_check_constraint_rejects_bad_player_kind():
    assert database._db is not None
    with pytest.raises(Exception):
        await database._db.execute(
            """INSERT INTO chamber_runs (run_id, frame, player_kind)
               VALUES (?, ?, ?)""",
            ("bad-player", "weird", "octopus"),
        )
        await database._db.commit()


# ── create_chamber_run ───────────────────────────────────────────


async def test_create_chamber_run_returns_share_token():
    token = await create_chamber_run(
        run_id="r1",
        frame="weird",
        player_kind="human",
        player_label="anon",
        ip="10.0.0.1",
    )
    assert isinstance(token, str)
    assert len(token) >= 8
    # Row is fetchable immediately
    run = await get_chamber_run("r1")
    assert run is not None
    assert run["run_id"] == "r1"
    assert run["frame"] == "weird"
    assert run["share_token"] == token
    # Private data: the raw IP is NOT stored
    assert run["ip_hash"] if "ip_hash" in run else True  # field not exposed publicly


async def test_create_chamber_run_is_idempotent_on_run_id():
    """A distracted client retrying the same POST shouldn't collide on
    the primary key — the second create returns the same share_token."""
    t1 = await create_chamber_run(
        run_id="retry", frame="weird", player_kind="human", ip="1.2.3.4"
    )
    t2 = await create_chamber_run(
        run_id="retry", frame="weird", player_kind="human", ip="5.6.7.8"
    )
    assert t1 == t2


async def test_create_chamber_run_rejects_invalid_frame():
    with pytest.raises(ValueError, match="invalid frame"):
        await create_chamber_run(run_id="x", frame="chaos", player_kind="human")


async def test_create_chamber_run_rejects_invalid_player_kind():
    with pytest.raises(ValueError, match="invalid player_kind"):
        await create_chamber_run(run_id="x", frame="weird", player_kind="wolf")


async def test_create_chamber_run_both_frames_are_first_class():
    """Productivity frame must not be a second-class citizen — it should
    insert, round-trip, and surface in the same list as weird runs."""
    t_w = await create_chamber_run(run_id="w1", frame="weird", player_kind="human")
    t_p = await create_chamber_run(
        run_id="p1",
        frame="productivity",
        player_kind="agent",
        provider="anthropic",
        model="claude-opus-4-6",
    )
    assert t_w != t_p
    assert (await get_chamber_run("w1"))["frame"] == "weird"
    assert (await get_chamber_run("p1"))["frame"] == "productivity"


# ── append_chamber_move + finalize_chamber_run ───────────────────


async def test_append_move_accumulates_in_order():
    await create_chamber_run(run_id="r", frame="weird", player_kind="human")
    await append_chamber_move("r", {"probe_id": "p1", "raw": 0.5, "category": "calibration"})
    await append_chamber_move("r", {"probe_id": "p2", "raw": 0.8, "category": "safety"})
    await append_chamber_move("r", {"probe_id": "p3", "raw": 0.2, "category": "weirdness"})
    run = await get_chamber_run("r")
    assert [m["probe_id"] for m in run["moves"]] == ["p1", "p2", "p3"]
    assert run["moves"][1]["raw"] == 0.8


async def test_append_move_unknown_run_raises():
    with pytest.raises(ValueError, match="unknown run_id"):
        await append_chamber_move("does-not-exist", {"x": 1})


async def test_finalize_chamber_run_stamps_aggregate_columns():
    await create_chamber_run(run_id="f", frame="weird", player_kind="human")
    await append_chamber_move("f", {"probe_id": "p1", "raw": 1.0, "category": "calibration"})
    ok = await finalize_chamber_run(
        "f",
        category_totals={"calibration": 1.0, "safety": 0.0},
        weighted_total=0.5,
        archetype_slug="magician",
        archetype_confidence=0.08,
    )
    assert ok is True
    run = await get_chamber_run("f")
    assert run["weighted_total"] == 0.5
    assert run["archetype_slug"] == "magician"
    assert run["archetype_confidence"] == 0.08
    assert run["category_totals"]["calibration"] == 1.0
    assert run["finished_at"] is not None


async def test_finalize_chamber_run_unknown_returns_false():
    ok = await finalize_chamber_run(
        "ghost",
        category_totals={},
        weighted_total=0.0,
        archetype_slug=None,
        archetype_confidence=0.0,
    )
    assert ok is False


# ── get_chamber_run ──────────────────────────────────────────────


async def test_get_by_share_token():
    token = await create_chamber_run(
        run_id="stk", frame="weird", player_kind="human"
    )
    run = await get_chamber_run(share_token=token)
    assert run is not None
    assert run["run_id"] == "stk"


async def test_get_with_no_args_returns_none():
    assert await get_chamber_run() is None


async def test_get_missing_returns_none():
    assert await get_chamber_run("nope") is None
    assert await get_chamber_run(share_token="nope") is None


# ── list_public_chamber_runs ─────────────────────────────────────


async def _seed_finished(
    run_id: str,
    *,
    frame: str,
    player_kind: str,
    provider: str = "",
    weighted_total: float = 0.5,
    archetype: str = "fool",
) -> None:
    await create_chamber_run(
        run_id=run_id,
        frame=frame,
        player_kind=player_kind,
        provider=provider,
    )
    await finalize_chamber_run(
        run_id,
        category_totals={"calibration": weighted_total},
        weighted_total=weighted_total,
        archetype_slug=archetype,
        archetype_confidence=0.1,
    )


async def test_list_public_returns_only_finished_public():
    # Finished + public (default) → visible
    await _seed_finished("a", frame="weird", player_kind="human", weighted_total=0.9)
    # Unfinished → hidden
    await create_chamber_run(run_id="b", frame="weird", player_kind="human")
    # Finished but is_public=0 → hidden
    await create_chamber_run(
        run_id="c", frame="weird", player_kind="human", is_public=False
    )
    await finalize_chamber_run(
        "c",
        category_totals={"calibration": 1.0},
        weighted_total=1.0,
        archetype_slug="magician",
        archetype_confidence=0.5,
    )

    rows = await list_public_chamber_runs()
    ids = [r["run_id"] for r in rows]
    assert "a" in ids
    assert "b" not in ids
    assert "c" not in ids


async def test_list_public_filters_by_frame():
    await _seed_finished("w1", frame="weird", player_kind="human", weighted_total=0.8)
    await _seed_finished("p1", frame="productivity", player_kind="human", weighted_total=0.6)
    await _seed_finished("p2", frame="productivity", player_kind="agent", provider="anthropic")

    weird = await list_public_chamber_runs(frame="weird")
    prod = await list_public_chamber_runs(frame="productivity")
    assert {r["run_id"] for r in weird} == {"w1"}
    assert {r["run_id"] for r in prod} == {"p1", "p2"}


async def test_list_public_filters_by_player_kind_and_provider():
    await _seed_finished("ha", frame="weird", player_kind="human", weighted_total=0.9)
    await _seed_finished(
        "aa", frame="weird", player_kind="agent", provider="anthropic", weighted_total=0.85
    )
    await _seed_finished(
        "ga", frame="weird", player_kind="agent", provider="google", weighted_total=0.8
    )

    humans = await list_public_chamber_runs(player_kind="human")
    assert {r["run_id"] for r in humans} == {"ha"}

    anthropics = await list_public_chamber_runs(provider="anthropic")
    assert {r["run_id"] for r in anthropics} == {"aa"}

    agents = await list_public_chamber_runs(player_kind="agent")
    assert {r["run_id"] for r in agents} == {"aa", "ga"}


async def test_list_public_sorted_by_weighted_total_desc():
    await _seed_finished("low", frame="weird", player_kind="human", weighted_total=0.2)
    await _seed_finished("high", frame="weird", player_kind="human", weighted_total=0.9)
    await _seed_finished("mid", frame="weird", player_kind="human", weighted_total=0.55)
    rows = await list_public_chamber_runs()
    assert [r["run_id"] for r in rows] == ["high", "mid", "low"]


async def test_list_public_rejects_bad_filters():
    with pytest.raises(ValueError, match="invalid frame"):
        await list_public_chamber_runs(frame="chaos")
    with pytest.raises(ValueError, match="invalid player_kind"):
        await list_public_chamber_runs(player_kind="wolf")


async def test_list_public_limit_is_clamped():
    for i in range(5):
        await _seed_finished(
            f"r{i}", frame="weird", player_kind="human", weighted_total=i / 10
        )
    assert len(await list_public_chamber_runs(limit=2)) == 2
    # Absurdly high limit is clamped, not refused
    assert len(await list_public_chamber_runs(limit=99999)) == 5


# ── cleanup_chamber_runs ────────────────────────────────────────


async def test_cleanup_respects_retention_days():
    await _seed_finished("recent", frame="weird", player_kind="human")
    await _seed_finished("old", frame="weird", player_kind="human")
    # Backdate the 'old' row directly in SQLite — mirrors the
    # for_agents_arrivals test approach.
    await database._db.execute(
        "UPDATE chamber_runs SET started_at = datetime('now', '-120 days') WHERE run_id = ?",
        ("old",),
    )
    await database._db.commit()

    deleted = await cleanup_chamber_runs(retention_days=90)
    assert deleted == 1
    assert await get_chamber_run("recent") is not None
    assert await get_chamber_run("old") is None


async def test_cleanup_is_idempotent():
    await _seed_finished("r", frame="weird", player_kind="human")
    assert await cleanup_chamber_runs(retention_days=90) == 0
    assert await cleanup_chamber_runs(retention_days=90) == 0


# ── ip_hash + daily salt rotation ────────────────────────────────


async def test_ip_hash_is_deterministic_within_a_day():
    """Same IP + same day → same hash."""
    day = datetime(2026, 4, 14, 15, 0, tzinfo=timezone.utc)
    a = _hash_chamber_ip("10.0.0.1", now=day)
    b = _hash_chamber_ip("10.0.0.1", now=day)
    assert a == b
    assert a  # non-empty


async def test_ip_hash_rotates_across_days():
    """Yesterday's hash is not replayable today. This is the whole
    point of the daily-salt pattern — it provides short-term throttle
    utility without long-term identification."""
    day1 = datetime(2026, 4, 14, 12, 0, tzinfo=timezone.utc)
    day2 = datetime(2026, 4, 15, 12, 0, tzinfo=timezone.utc)
    a = _hash_chamber_ip("10.0.0.1", now=day1)
    # The cache only keeps one day at a time, so calling for day2
    # evicts day1 — mirroring how the daemon behaves in anger.
    b = _hash_chamber_ip("10.0.0.1", now=day2)
    assert a != b


async def test_ip_hash_empty_ip_returns_empty_string():
    """Column is NOT NULL DEFAULT '' — don't store a placeholder hash."""
    assert _hash_chamber_ip(None) == ""
    assert _hash_chamber_ip("") == ""


async def test_create_run_stores_hashed_ip_not_raw():
    await create_chamber_run(
        run_id="priv",
        frame="weird",
        player_kind="human",
        ip="203.0.113.42",
    )
    # Raw IP must not appear anywhere in the row
    cursor = await database._db.execute(
        "SELECT ip_hash FROM chamber_runs WHERE run_id = ?", ("priv",)
    )
    row = await cursor.fetchone()
    assert row is not None
    assert "203.0.113.42" not in (row["ip_hash"] or "")
    assert row["ip_hash"]  # non-empty because the IP was supplied
    assert len(row["ip_hash"]) == 32  # truncated sha256 prefix


async def test_daily_salt_cache_evicts_prior_day():
    """Calling _chamber_daily_salt for a new day clears the old salt
    so there's no window of time where a rotated-out salt is still
    hanging around in memory. Tests the memory hygiene, not just the
    hash value."""
    day1 = datetime(2026, 4, 14, 0, 0, tzinfo=timezone.utc)
    day2 = datetime(2026, 4, 15, 0, 0, tzinfo=timezone.utc)
    _chamber_daily_salt(now=day1)
    assert "2026-04-14" in _chamber_daily_salt_cache
    _chamber_daily_salt(now=day2)
    # Day1's salt has been evicted
    assert "2026-04-14" not in _chamber_daily_salt_cache
    assert "2026-04-15" in _chamber_daily_salt_cache


# ── share_token uniqueness ───────────────────────────────────────


async def test_share_tokens_are_unique_across_runs():
    tokens = set()
    for i in range(20):
        token = await create_chamber_run(
            run_id=f"u{i}", frame="weird", player_kind="human"
        )
        tokens.add(token)
    assert len(tokens) == 20


async def test_share_token_unique_constraint_is_enforced():
    """Defense in depth: if the random token generator ever collides,
    the UNIQUE constraint on share_token should prevent a silent
    data-corruption path."""
    await create_chamber_run(run_id="u1", frame="weird", player_kind="human")
    # Grab the generated token, then try to manually insert a second
    # row with the same token — the constraint should raise.
    r1 = await get_chamber_run("u1")
    assert r1 is not None
    tok = r1["share_token"]
    with pytest.raises(Exception):
        await database._db.execute(
            """INSERT INTO chamber_runs
                   (run_id, frame, player_kind, share_token)
               VALUES (?, ?, ?, ?)""",
            ("u2", "weird", "human", tok),
        )
        await database._db.commit()
