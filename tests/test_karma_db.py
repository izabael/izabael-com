"""Tests for Karma Garden Phase 1 — data layer + decay engine.

Plan: ``~/.claude/queen/plans/karma-garden.md``
Implementation: ``database.py`` (schema + async functions) +
``karma_weights.py`` (tunable config).

Covers:

  * plant_garden — archetype seeding, idempotency, defaults
  * record_karma_event — weights lookup, peak bump, silent drop for
    non-garden players, unknown-action surfacing
  * milestone crossings — first cross, UNIQUE no-op on re-cross,
    permanence through decay, multi-threshold single-event
  * spend_seed — no-self-sponsorship (function + DB CHECK), seed
    decrement, recipient credit, error paths
  * _decayed_current — pure math: grace, smooth decay, floor clamp,
    peak preservation, idempotency, respect-lived-history, peak==0
  * run_decay_pass — gardens touched count, peak preservation,
    running twice is a no-op
  * replenish_seeds_pass — period gate, cap, idempotency,
    seeds_total_earned bookkeeping
"""
from __future__ import annotations

import os
import pytest
from datetime import datetime, timedelta, timezone

from database import (
    init_db, close_db,
    plant_garden, get_garden, record_karma_event, spend_seed,
    list_milestones, list_karma_events,
    run_decay_pass, replenish_seeds_pass,
    _decayed_current,
    KarmaError,
)
import karma_weights as kw
from karma_weights import (
    BREATH, MIRROR, WEAVE, FLAME, SHADOW, VIRTUES,
    DECAY_RATE, DECAY_GRACE_DAYS, DECAY_FLOOR_MIN, DECAY_FLOOR_FRACTION,
    SEEDS_STARTING, SEEDS_CAP, SEEDS_REPLENISH_PERIOD_DAYS,
)


@pytest.fixture(autouse=True)
async def _init_karma_db(tmp_path):
    os.environ["IZABAEL_DB"] = str(tmp_path / "karma.db")
    import database
    database.DB_PATH = str(tmp_path / "karma.db")
    await init_db()
    yield
    await close_db()


# ── plant_garden ────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_plant_garden_creates_row_with_archetype_seeding():
    """Magician archetype seeds high Breath + Weave, mid others."""
    garden = await plant_garden("alice", archetype_slug="magician")
    assert garden["player_id"] == "alice"
    assert garden["archetype_slug"] == "magician"
    assert garden["player_kind"] == "human"
    assert garden["virtues"][BREATH]["current"] == 20.0
    assert garden["virtues"][BREATH]["peak"] == 20.0
    assert garden["virtues"][WEAVE]["current"] == 15.0
    assert garden["virtues"][WEAVE]["peak"] == 15.0
    assert garden["virtues"][MIRROR]["current"] == 5.0
    assert garden["virtues"][FLAME]["current"] == 5.0
    assert garden["virtues"][SHADOW]["current"] == 5.0
    assert garden["seeds_current"] == SEEDS_STARTING
    assert garden["seeds_total_earned"] == SEEDS_STARTING
    assert garden["is_public"] is False
    assert garden["last_action_at"] is not None


@pytest.mark.anyio
async def test_plant_garden_unknown_archetype_uses_balanced_default():
    garden = await plant_garden("bob", archetype_slug="nonesuch")
    for v in VIRTUES:
        assert garden["virtues"][v]["current"] == 5.0
        assert garden["virtues"][v]["peak"] == 5.0


@pytest.mark.anyio
async def test_plant_garden_is_idempotent():
    """Planting twice returns the existing garden, doesn't reseed."""
    first = await plant_garden("carol", archetype_slug="hermit")
    # Record some practice so we can verify it survives a second plant call.
    await record_karma_event("carol", "post_long", source_ref="post-1")
    mid = await get_garden("carol")
    assert mid["virtues"][BREATH]["current"] > first["virtues"][BREATH]["current"]

    second = await plant_garden("carol", archetype_slug="magician")
    # Archetype is ignored on idempotent replant — original shape persists.
    assert second["archetype_slug"] == "hermit"
    assert second["virtues"][BREATH]["current"] == mid["virtues"][BREATH]["current"]


@pytest.mark.anyio
async def test_get_garden_unknown_returns_none():
    assert await get_garden("ghost") is None


# ── record_karma_event ──────────────────────────────────────────────

@pytest.mark.anyio
async def test_record_event_non_garden_player_silent_drop():
    """Action hooks call record_karma_event site-wide; non-garden
    players should silently no-op without raising."""
    result = await record_karma_event("not-a-player", "post_short")
    assert result == {"ok": False, "reason": "no-garden", "player_id": "not-a-player"}


@pytest.mark.anyio
async def test_record_event_unknown_action_surfaces_miss():
    await plant_garden("dave")
    result = await record_karma_event("dave", "fake_action")
    assert result["ok"] is False
    assert result["reason"] == "unknown-action"


@pytest.mark.anyio
async def test_record_event_post_short_increments_breath_only():
    await plant_garden("eve")
    before = (await get_garden("eve"))["virtues"][BREATH]["current"]
    result = await record_karma_event("eve", "post_short")
    assert result["ok"] is True
    after = (await get_garden("eve"))
    assert after["virtues"][BREATH]["current"] == pytest.approx(before + 0.5)
    # Other virtues unchanged.
    for v in (MIRROR, WEAVE, FLAME, SHADOW):
        assert after["virtues"][v]["current"] == 5.0


@pytest.mark.anyio
async def test_record_event_post_long_increments_three_virtues():
    """'post_long' weights the plan specifies: +1.0 Breath, +0.3 Flame, +1.5 Shadow."""
    await plant_garden("frank")
    await record_karma_event("frank", "post_long")
    g = await get_garden("frank")
    assert g["virtues"][BREATH]["current"] == pytest.approx(5.0 + 1.0)
    assert g["virtues"][FLAME]["current"] == pytest.approx(5.0 + 0.3)
    assert g["virtues"][SHADOW]["current"] == pytest.approx(5.0 + 1.5)
    assert g["virtues"][MIRROR]["current"] == 5.0
    assert g["virtues"][WEAVE]["current"] == 5.0


@pytest.mark.anyio
async def test_record_event_bumps_peak_when_current_exceeds_it():
    await plant_garden("grace")
    await record_karma_event("grace", "post_long")
    g = await get_garden("grace")
    assert g["virtues"][SHADOW]["peak"] == pytest.approx(6.5)
    # New current was 6.5 (from 5.0 + 1.5) which exceeds starting peak 5.0.
    await record_karma_event("grace", "post_long")
    g = await get_garden("grace")
    assert g["virtues"][SHADOW]["peak"] == pytest.approx(8.0)


@pytest.mark.anyio
async def test_record_event_writes_event_row():
    await plant_garden("heidi")
    await record_karma_event("heidi", "post_long", source_ref="post-42")
    events = await list_karma_events("heidi")
    # One action produces multiple event rows (one per virtue in the weight map).
    kinds = {(e["virtue"], e["action"]) for e in events}
    assert (BREATH, "post_long") in kinds
    assert (FLAME, "post_long") in kinds
    assert (SHADOW, "post_long") in kinds
    assert all(e["source_ref"] == "post-42" for e in events)


# ── milestones ──────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_milestone_first_spark_crosses_at_5():
    """Cross Breath 5.0 → mint First Spark. Fresh garden starts at 5 for
    default archetype so we need to go from <5; use hermit which seeds
    Breath=5.0 exactly — to cross we need to start lower. Use fool
    archetype: Breath=15.0 so we'd SKIP 5.0. Plant with balanced+zero
    and feed events to cross cleanly."""
    # Plant a garden, then artificially zero the virtue to test a
    # pristine crossing from below 5.0.
    await plant_garden("ivy", archetype_slug="unknownz")  # balanced 5s
    # Zero out breath via a direct DB write so we can test crossing
    # from fresh low.
    import database as db
    await db._db.execute(
        "UPDATE karma_gardens SET breath_current = 0, breath_peak = 0 WHERE player_id = ?",
        ("ivy",),
    )
    await db._db.commit()

    # +0.5 × 10 = +5.0 Breath: post_short x 10 → from 0 to 5.0
    for _ in range(10):
        await record_karma_event("ivy", "post_short")

    milestones = await list_milestones("ivy")
    names = [m["name"] for m in milestones]
    assert "First Spark" in names
    first_spark = [m for m in milestones if m["name"] == "First Spark"][0]
    assert first_spark["virtue"] == BREATH
    assert first_spark["threshold"] == 5.0


@pytest.mark.anyio
async def test_milestone_unique_no_double_mint():
    """Crossing the same threshold twice (via decay-then-regain, or
    explicit re-fire) mints only one milestone row."""
    await plant_garden("jack")
    # Fool: Flame=20 (already past 5.0 and 15.0 milestone thresholds at plant time).
    await plant_garden("jack-fool", archetype_slug="fool")
    # Trigger a Flame-generating event to attempt re-crossing.
    import database as db
    # Force Flame current back below 5 to simulate decay re-engagement.
    await db._db.execute(
        "UPDATE karma_gardens SET flame_current = 4.0 WHERE player_id = ?",
        ("jack-fool",),
    )
    await db._db.commit()
    # Plant already minted milestones on seeding? No — plant_garden
    # sets virtues directly without calling record_karma_event, so no
    # milestones exist yet. We cross via events now.
    initial = await list_milestones("jack-fool")
    assert len(initial) == 0

    # refuse_prompt gives +2.0 Flame. +1 event crosses the 5.0
    # threshold (4.0 → 6.0).
    await record_karma_event("jack-fool", "refuse_prompt")
    after_first_cross = await list_milestones("jack-fool")
    flame_5 = [m for m in after_first_cross if m["virtue"] == FLAME and m["threshold"] == 5.0]
    assert len(flame_5) == 1

    # Force Flame down below 5 again to attempt re-cross.
    await db._db.execute(
        "UPDATE karma_gardens SET flame_current = 4.0 WHERE player_id = ?",
        ("jack-fool",),
    )
    await db._db.commit()
    await record_karma_event("jack-fool", "refuse_prompt")
    after_second_cross = await list_milestones("jack-fool")
    flame_5_again = [m for m in after_second_cross if m["virtue"] == FLAME and m["threshold"] == 5.0]
    assert len(flame_5_again) == 1  # still only one — UNIQUE held


@pytest.mark.anyio
async def test_milestone_multi_threshold_in_one_event():
    """One event can cross multiple milestone thresholds at once.
    E.g. a big Shadow jump from 4.0 → 20.0 crosses both 5.0 and 15.0."""
    await plant_garden("kate")
    import database as db
    await db._db.execute(
        "UPDATE karma_gardens SET shadow_current = 4.0, shadow_peak = 4.0 WHERE player_id = ?",
        ("kate",),
    )
    await db._db.commit()
    # deep_read_comment → SHADOW +2.0. Five events: 4.0 → 14.0 → 24.0? Let's
    # instead inject a big override via spend_seed-style single-action delta.
    await record_karma_event(
        "kate", "deep_read_comment",
        overrides=[(SHADOW, 20.0)],  # one huge event crossing 5.0 AND 15.0
    )
    milestones = await list_milestones("kate")
    shadow_names = {(m["threshold"], m["name"]) for m in milestones if m["virtue"] == SHADOW}
    assert (5.0, "First Deep Read") in shadow_names
    assert (15.0, "The Patient Reader") in shadow_names


@pytest.mark.anyio
async def test_milestones_permanent_through_decay_simulation():
    """Milestones are records of having crossed — decay never removes
    them. Simulate: cross 5.0, then directly drop current to 1.0, and
    verify the milestone row still exists."""
    await plant_garden("lily")
    import database as db
    await db._db.execute(
        "UPDATE karma_gardens SET weave_current = 0, weave_peak = 0 WHERE player_id = ?",
        ("lily",),
    )
    await db._db.commit()
    # create_meetup → WEAVE +1.0; 5 events → 5.0
    for _ in range(5):
        await record_karma_event("lily", "create_meetup")
    assert any(m["name"] == "First Introduction" for m in await list_milestones("lily"))
    # Force Weave back down.
    await db._db.execute(
        "UPDATE karma_gardens SET weave_current = 1.0 WHERE player_id = ?",
        ("lily",),
    )
    await db._db.commit()
    still_there = await list_milestones("lily")
    assert any(m["name"] == "First Introduction" for m in still_there)


# ── spend_seed ──────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_spend_seed_transfers_from_sponsor_to_recipient():
    await plant_garden("mallory")
    await plant_garden("nina")
    before = (await get_garden("mallory"))["seeds_current"]
    before_nina_mirror = (await get_garden("nina"))["virtues"][MIRROR]["current"]

    result = await spend_seed("mallory", "nina", MIRROR, note="for your patience")

    after = (await get_garden("mallory"))["seeds_current"]
    after_nina_mirror = (await get_garden("nina"))["virtues"][MIRROR]["current"]

    assert after == before - 1
    assert after_nina_mirror == pytest.approx(before_nina_mirror + 1.0)
    assert result["from_player"] == "mallory"
    assert result["to_player"] == "nina"
    assert result["virtue"] == MIRROR
    assert result["recipient_event"]["ok"] is True


@pytest.mark.anyio
async def test_spend_seed_self_sponsorship_raises_function_error():
    await plant_garden("oscar")
    with pytest.raises(KarmaError, match="cannot sponsor yourself"):
        await spend_seed("oscar", "oscar", BREATH)


@pytest.mark.anyio
async def test_spend_seed_unknown_virtue_raises():
    await plant_garden("pam")
    await plant_garden("quinn")
    with pytest.raises(KarmaError, match="unknown virtue"):
        await spend_seed("pam", "quinn", "glamour")


@pytest.mark.anyio
async def test_spend_seed_sponsor_out_of_seeds_raises():
    await plant_garden("rob")
    await plant_garden("sara")
    import database as db
    await db._db.execute(
        "UPDATE karma_gardens SET seeds_current = 0 WHERE player_id = ?",
        ("rob",),
    )
    await db._db.commit()
    with pytest.raises(KarmaError, match="out of Seeds"):
        await spend_seed("rob", "sara", BREATH)


@pytest.mark.anyio
async def test_spend_seed_recipient_without_garden_raises():
    await plant_garden("tom")
    with pytest.raises(KarmaError, match="no garden"):
        await spend_seed("tom", "phantom", BREATH)


@pytest.mark.anyio
async def test_spend_seed_sponsor_without_garden_raises():
    await plant_garden("ursula")
    with pytest.raises(KarmaError, match="no garden"):
        await spend_seed("ghost", "ursula", BREATH)


@pytest.mark.anyio
async def test_karma_seeds_db_check_blocks_self_sponsorship():
    """Belt-and-braces: even if a caller bypasses spend_seed and
    tries to INSERT a karma_seeds row with from_player == to_player,
    the DB CHECK constraint rejects it."""
    await plant_garden("vic")
    import database as db
    import aiosqlite
    with pytest.raises(aiosqlite.IntegrityError):
        await db._db.execute(
            """INSERT INTO karma_seeds
               (seed_id, from_player, to_player, virtue, delta, spent_at)
               VALUES ('x', 'vic', 'vic', 'breath', 1.0, '2026-04-15T00:00:00')"""
        )


# ── decay math (pure) ───────────────────────────────────────────────

def _now():
    return datetime(2026, 4, 15, 12, 0, 0, tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")


@pytest.mark.anyio
async def test_decayed_current_within_grace_period_unchanged():
    now = _now()
    last = now - timedelta(days=DECAY_GRACE_DAYS - 1)  # 6 days idle
    assert _decayed_current(100.0, 100.0, _iso(last), now) == 100.0


@pytest.mark.anyio
async def test_decayed_current_at_exactly_grace_boundary_unchanged():
    now = _now()
    last = now - timedelta(days=DECAY_GRACE_DAYS)  # exactly 7 days
    assert _decayed_current(100.0, 100.0, _iso(last), now) == 100.0


@pytest.mark.anyio
async def test_decayed_current_14_days_idle_applies_one_week_decay():
    """At 14 days idle, weeks_inactive = 1, target = peak * 0.95 = 95."""
    now = _now()
    last = now - timedelta(days=14)
    result = _decayed_current(100.0, 100.0, _iso(last), now)
    assert result == pytest.approx(95.0, abs=0.01)


@pytest.mark.anyio
async def test_decayed_current_clamps_at_floor_for_very_long_idle():
    now = _now()
    last = now - timedelta(days=365)  # a year idle
    result = _decayed_current(100.0, 100.0, _iso(last), now)
    expected_floor = max(DECAY_FLOOR_MIN, 100.0 * DECAY_FLOOR_FRACTION)
    assert result == pytest.approx(expected_floor)


@pytest.mark.anyio
async def test_decayed_current_new_player_floor_is_10():
    """A new player with peak=3.0 shouldn't be clamped to 0.3 (3*0.10).
    The 10.0 hard floor protects low-peak players from punishment."""
    now = _now()
    last = now - timedelta(days=365)
    # But current can't exceed peak at start, so use current=peak=3.0.
    # Floor should be max(10.0, 3.0*0.10) = 10.0. Since current=3.0 < floor=10,
    # we return max(10, min(3, target)) = max(10, small) = 10.
    result = _decayed_current(3.0, 3.0, _iso(last), now)
    assert result == pytest.approx(10.0)


@pytest.mark.anyio
async def test_decayed_current_idempotent_running_twice_same_result():
    """Calling with the same inputs twice yields the same result."""
    now = _now()
    last = now - timedelta(days=21)
    first = _decayed_current(100.0, 100.0, _iso(last), now)
    # "Run" again with the same inputs (no action, no last_action update)
    second = _decayed_current(first, 100.0, _iso(last), now)
    # Key: second call shouldn't pull current further down, because
    # first already equals target.
    assert second == pytest.approx(first)


@pytest.mark.anyio
async def test_decayed_current_respects_lived_history():
    """A player with current=53 / peak=100 and 8 days idle should NOT
    be pulled UP to a higher target value. Current stays at 53 until
    target drifts below 53."""
    now = _now()
    last = now - timedelta(days=8)
    result = _decayed_current(53.0, 100.0, _iso(last), now)
    # target = 100 * 0.95^((8-7)/7) = 100 * 0.95^0.143 ≈ 99.27
    # new = max(floor=10, min(53, 99.27)) = 53
    assert result == pytest.approx(53.0)


@pytest.mark.anyio
async def test_decayed_current_peak_zero_edge_case():
    """peak=0 means a brand-new garden with no practice yet. Should
    never go negative regardless of idle time."""
    now = _now()
    last = now - timedelta(days=100)
    result = _decayed_current(0.0, 0.0, _iso(last), now)
    assert result == 0.0


@pytest.mark.anyio
async def test_decayed_current_none_last_action():
    """A garden with no recorded last_action shouldn't crash."""
    now = _now()
    assert _decayed_current(50.0, 100.0, None, now) == 50.0


# ── run_decay_pass ──────────────────────────────────────────────────

@pytest.mark.anyio
async def test_run_decay_pass_decays_inactive_gardens():
    await plant_garden("willa", archetype_slug="magician")  # breath=20, weave=15
    # Force last_action to 14 days ago so decay fires.
    import database as db
    past = (_now() - timedelta(days=14)).strftime("%Y-%m-%dT%H:%M:%S.%f")
    await db._db.execute(
        "UPDATE karma_gardens SET last_action_at = ? WHERE player_id = ?",
        (past, "willa"),
    )
    await db._db.commit()

    summary = await run_decay_pass(now=_now())
    assert summary["gardens_scanned"] == 1
    assert summary["gardens_decayed"] == 1

    g = await get_garden("willa")
    # breath decayed from 20 → peak*0.95 = 19.0
    assert g["virtues"][BREATH]["current"] == pytest.approx(19.0, abs=0.01)
    # peak is preserved
    assert g["virtues"][BREATH]["peak"] == 20.0


@pytest.mark.anyio
async def test_run_decay_pass_preserves_peak():
    await plant_garden("xavier", archetype_slug="hermit")
    import database as db
    past = (_now() - timedelta(days=100)).strftime("%Y-%m-%dT%H:%M:%S.%f")
    await db._db.execute(
        "UPDATE karma_gardens SET last_action_at = ? WHERE player_id = ?",
        (past, "xavier"),
    )
    await db._db.commit()
    before = await get_garden("xavier")
    await run_decay_pass(now=_now())
    after = await get_garden("xavier")
    for v in VIRTUES:
        assert after["virtues"][v]["peak"] == before["virtues"][v]["peak"]


@pytest.mark.anyio
async def test_run_decay_pass_twice_is_idempotent():
    await plant_garden("yves", archetype_slug="magician")
    import database as db
    past = (_now() - timedelta(days=21)).strftime("%Y-%m-%dT%H:%M:%S.%f")
    await db._db.execute(
        "UPDATE karma_gardens SET last_action_at = ? WHERE player_id = ?",
        (past, "yves"),
    )
    await db._db.commit()

    await run_decay_pass(now=_now())
    g1 = await get_garden("yves")
    await run_decay_pass(now=_now())
    g2 = await get_garden("yves")

    for v in VIRTUES:
        assert g1["virtues"][v]["current"] == g2["virtues"][v]["current"]


# ── replenish_seeds_pass ────────────────────────────────────────────

@pytest.mark.anyio
async def test_replenish_before_period_no_grant():
    await plant_garden("zara")
    import database as db
    await db._db.execute(
        "UPDATE karma_gardens SET seeds_current = 5 WHERE player_id = ?",
        ("zara",),
    )
    await db._db.commit()
    summary = await replenish_seeds_pass(now=_now())
    assert summary["seeds_granted"] == 0
    assert (await get_garden("zara"))["seeds_current"] == 5


@pytest.mark.anyio
async def test_replenish_after_period_grants_one_seed():
    await plant_garden("amy")
    import database as db
    long_ago = (_now() - timedelta(days=SEEDS_REPLENISH_PERIOD_DAYS + 1)).strftime(
        "%Y-%m-%dT%H:%M:%S.%f"
    )
    await db._db.execute(
        "UPDATE karma_gardens SET seeds_current = 5, last_replenish_at = ? WHERE player_id = ?",
        (long_ago, "amy"),
    )
    await db._db.commit()
    summary = await replenish_seeds_pass(now=_now())
    assert summary["seeds_granted"] == 1
    g = await get_garden("amy")
    assert g["seeds_current"] == 6
    # seeds_total_earned increments by exactly the grant amount.
    assert g["seeds_total_earned"] == SEEDS_STARTING + 1


@pytest.mark.anyio
async def test_replenish_at_cap_no_grant():
    await plant_garden("ben")
    import database as db
    long_ago = (_now() - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%S.%f")
    await db._db.execute(
        """UPDATE karma_gardens
           SET seeds_current = ?, last_replenish_at = ? WHERE player_id = ?""",
        (SEEDS_CAP, long_ago, "ben"),
    )
    await db._db.commit()
    summary = await replenish_seeds_pass(now=_now())
    # A garden at cap is excluded from the query entirely.
    assert summary["seeds_granted"] == 0
    assert (await get_garden("ben"))["seeds_current"] == SEEDS_CAP


@pytest.mark.anyio
async def test_replenish_twice_in_same_day_grants_once():
    """Run the pass twice in quick succession — only one grant fires
    because the second call sees last_replenish_at = now."""
    await plant_garden("cleo")
    import database as db
    long_ago = (_now() - timedelta(days=SEEDS_REPLENISH_PERIOD_DAYS + 1)).strftime(
        "%Y-%m-%dT%H:%M:%S.%f"
    )
    await db._db.execute(
        "UPDATE karma_gardens SET seeds_current = 5, last_replenish_at = ? WHERE player_id = ?",
        (long_ago, "cleo"),
    )
    await db._db.commit()

    now1 = _now()
    await replenish_seeds_pass(now=now1)
    first = (await get_garden("cleo"))["seeds_current"]

    # Same moment again — should be a no-op.
    await replenish_seeds_pass(now=now1 + timedelta(minutes=1))
    second = (await get_garden("cleo"))["seeds_current"]

    assert first == 6
    assert second == 6
