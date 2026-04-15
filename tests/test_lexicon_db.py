"""Data-layer tests for the-lexicon Phase 2.

Round-trip CRUD for lexicon_languages, lexicon_proposals, lexicon_usages.
Covers: canonical seeding from content/lexicon/*, original creation,
fork lineage, list filters, proposal lifecycle (open → accepted),
re-decide rejection, usage recording + recent listing, and validation
errors at every boundary.

Mirrors tests/test_meetups_db.py for fixture style.
"""

import os

import pytest

from database import (
    init_db, close_db,
    create_language, fork_language, get_language, list_languages,
    create_proposal, get_proposal, list_proposals_for_language,
    decide_proposal,
    record_usage, list_recent_usages,
)


# ── Fixture ──────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
async def _init_test_db(tmp_path):
    os.environ["IZABAEL_DB"] = str(tmp_path / "lexicon.db")
    import database
    database.DB_PATH = str(tmp_path / "lexicon.db")
    await init_db()
    yield
    await close_db()


# ── Canonical seeding ────────────────────────────────────────────

@pytest.mark.anyio
async def test_seed_creates_three_canonical_languages():
    langs = await list_languages(canonical_only=True)
    slugs = {l["slug"] for l in langs}
    assert slugs == {"brevis", "verus", "actus"}


@pytest.mark.anyio
async def test_seeded_languages_carry_purpose_and_spec():
    brevis = await get_language("brevis")
    assert brevis is not None
    assert brevis["canonical"] is True
    assert brevis["parent_slug"] is None
    assert brevis["author_kind"] == "seed"
    assert "compress" in brevis["one_line_purpose"].lower()
    # The full spec markdown is stored, not just the frontmatter
    assert len(brevis["spec_markdown"]) > 500
    assert "canonical" in brevis["tags"]
    assert "speed" in brevis["tags"]


@pytest.mark.anyio
async def test_seeded_verus_and_actus_tags():
    verus = await get_language("verus")
    actus = await get_language("actus")
    assert "credibility" in verus["tags"]
    assert "efficacy" in actus["tags"]


@pytest.mark.anyio
async def test_seed_is_idempotent():
    # Re-run init which re-seeds — row count stays the same
    from database import seed_lexicon_canonical
    await seed_lexicon_canonical()
    await seed_lexicon_canonical()
    langs = await list_languages(canonical_only=True)
    assert len(langs) == 3


# ── create_language ──────────────────────────────────────────────

@pytest.mark.anyio
async def test_create_language_original():
    slug = await create_language(
        slug="kata",
        name="Kata",
        one_line_purpose="A tiny test language.",
        spec_markdown="# Kata\n\nSmall.",
        author_kind="agent",
        author_label="test-author",
        author_agent="test-agent-1",
        tags="test,craft",
    )
    assert slug == "kata"
    row = await get_language("kata")
    assert row["parent_slug"] is None
    assert row["canonical"] is False
    assert row["author_kind"] == "agent"
    assert "test" in row["tags"]
    assert "craft" in row["tags"]


@pytest.mark.anyio
async def test_create_language_rejects_duplicate_slug():
    with pytest.raises(ValueError, match="already exists"):
        await create_language(
            slug="brevis",  # already seeded
            name="Not Brevis",
            one_line_purpose="A collision.",
            spec_markdown="body",
            author_kind="agent",
            author_label="collider",
            author_agent="collider-agent",
        )


@pytest.mark.anyio
async def test_create_language_rejects_bad_slug():
    with pytest.raises(ValueError, match="slug"):
        await create_language(
            slug="Has Spaces",
            name="X",
            one_line_purpose="y",
            spec_markdown="z",
            author_kind="human",
            author_label="h",
        )


@pytest.mark.anyio
async def test_create_language_requires_agent_for_agent_kind():
    with pytest.raises(ValueError, match="author_agent"):
        await create_language(
            slug="headless",
            name="Headless",
            one_line_purpose="no agent",
            spec_markdown="body",
            author_kind="agent",
            author_label="h",
            author_agent=None,
        )


# ── fork_language ────────────────────────────────────────────────

@pytest.mark.anyio
async def test_fork_language_links_to_parent():
    new = await fork_language(
        parent_slug="brevis",
        new_slug="brevis-ext",
        name="Brevis Ext",
        one_line_purpose="Brevis with 10 more symbols.",
        author_kind="agent",
        author_label="forker",
        author_agent="forker-agent",
    )
    row = await get_language(new)
    assert row["parent_slug"] == "brevis"
    assert row["canonical"] is False
    # The fork should inherit the spec from its parent when no
    # spec_markdown is passed in.
    parent = await get_language("brevis")
    assert row["spec_markdown"] == parent["spec_markdown"]
    # Inherits parent's non-canonical tags and adds 'fork'
    assert "speed" in row["tags"]
    assert "fork" in row["tags"]
    assert "canonical" not in row["tags"]


@pytest.mark.anyio
async def test_fork_language_rejects_unknown_parent():
    with pytest.raises(ValueError, match="unknown parent_slug"):
        await fork_language(
            parent_slug="nope",
            new_slug="child",
            name="Child",
            one_line_purpose="orphan",
            author_kind="agent",
            author_label="h",
            author_agent="h-agent",
        )


@pytest.mark.anyio
async def test_fork_language_with_custom_spec_overrides_parent():
    new = await fork_language(
        parent_slug="verus",
        new_slug="verus-lite",
        name="Verus Lite",
        one_line_purpose="Verus without the confidence scale.",
        spec_markdown="# Verus Lite\n\ntrimmed.",
        author_kind="agent",
        author_label="trimmer",
        author_agent="trimmer-agent",
    )
    row = await get_language(new)
    assert "Lite" in row["spec_markdown"]
    assert row["parent_slug"] == "verus"


# ── list_languages ───────────────────────────────────────────────

@pytest.mark.anyio
async def test_list_languages_all_vs_canonical():
    await create_language(
        slug="kata", name="Kata", one_line_purpose="test",
        spec_markdown="body", author_kind="human", author_label="h",
    )
    all_langs = await list_languages()
    canonical_langs = await list_languages(canonical_only=True)
    assert len(all_langs) == 4      # 3 seeds + kata
    assert len(canonical_langs) == 3


@pytest.mark.anyio
async def test_list_languages_tag_filter():
    speed = await list_languages(tag_filter="speed")
    assert len(speed) == 1
    assert speed[0]["slug"] == "brevis"
    credibility = await list_languages(tag_filter="credibility")
    assert len(credibility) == 1
    assert credibility[0]["slug"] == "verus"


@pytest.mark.anyio
async def test_get_language_unknown_returns_none():
    assert await get_language("not-a-real-language") is None
    assert await get_language("") is None


# ── create_proposal + lifecycle ──────────────────────────────────

@pytest.mark.anyio
async def test_create_proposal_basic():
    pid = await create_proposal(
        target_slug="brevis",
        title="add ⊘ for null-result",
        body_markdown="Propose ⊘ as a single symbol meaning null/void.",
        author_kind="agent",
        author_label="proposer",
        author_agent="proposer-agent",
    )
    row = await get_proposal(pid)
    assert row["status"] == "open"
    assert row["target_slug"] == "brevis"
    assert row["decided_at"] is None


@pytest.mark.anyio
async def test_create_proposal_rejects_unknown_target():
    with pytest.raises(ValueError, match="unknown target_slug"):
        await create_proposal(
            target_slug="nothing",
            title="x",
            body_markdown="y",
            author_kind="agent",
            author_label="p",
            author_agent="p-agent",
        )


@pytest.mark.anyio
async def test_list_proposals_filters_by_status():
    pid1 = await create_proposal(
        target_slug="brevis", title="one", body_markdown="a",
        author_kind="agent", author_label="p", author_agent="p-agent",
    )
    pid2 = await create_proposal(
        target_slug="brevis", title="two", body_markdown="b",
        author_kind="agent", author_label="p", author_agent="p-agent",
    )
    await decide_proposal(pid1, "accepted", "maintainer")
    open_list = await list_proposals_for_language("brevis", status="open")
    accepted = await list_proposals_for_language("brevis", status="accepted")
    all_list = await list_proposals_for_language("brevis", status=None)
    assert [p["proposal_id"] for p in open_list] == [pid2]
    assert [p["proposal_id"] for p in accepted] == [pid1]
    assert len(all_list) == 2


@pytest.mark.anyio
async def test_decide_proposal_sets_metadata():
    pid = await create_proposal(
        target_slug="actus", title="add rollback-all", body_markdown="body",
        author_kind="agent", author_label="p", author_agent="p-agent",
    )
    row = await decide_proposal(pid, "accepted", "meta-iza @ HiveQueen")
    assert row["status"] == "accepted"
    assert row["decider"] == "meta-iza @ HiveQueen"
    assert row["decided_at"] is not None


@pytest.mark.anyio
async def test_decide_proposal_rejects_already_decided():
    pid = await create_proposal(
        target_slug="verus", title="x", body_markdown="y",
        author_kind="agent", author_label="p", author_agent="p-agent",
    )
    await decide_proposal(pid, "declined", "maintainer")
    with pytest.raises(ValueError, match="already"):
        await decide_proposal(pid, "accepted", "maintainer")


@pytest.mark.anyio
async def test_decide_proposal_rejects_unknown_id():
    with pytest.raises(ValueError, match="unknown proposal_id"):
        await decide_proposal("deadbeef", "accepted", "maintainer")


# ── record_usage + list_recent_usages ────────────────────────────

@pytest.mark.anyio
async def test_record_usage_and_list():
    uid = await record_usage(
        language_slug="brevis",
        source_type="agent-message",
        content="?¬V ⟐src",
        source_ref="msg-abc",
        author_label="hermes",
    )
    recent = await list_recent_usages(limit=10)
    assert len(recent) == 1
    assert recent[0]["usage_id"] == uid
    assert recent[0]["language_slug"] == "brevis"
    assert recent[0]["source_type"] == "agent-message"


@pytest.mark.anyio
async def test_list_recent_usages_filters_by_language():
    await record_usage(
        language_slug="brevis", source_type="cube",
        content="b1", author_label="a",
    )
    await record_usage(
        language_slug="verus", source_type="cube",
        content="v1", author_label="a",
    )
    brevis_only = await list_recent_usages(limit=10, language_slug="brevis")
    assert len(brevis_only) == 1
    assert brevis_only[0]["language_slug"] == "brevis"


@pytest.mark.anyio
async def test_record_usage_rejects_unknown_language():
    with pytest.raises(ValueError, match="unknown language_slug"):
        await record_usage(
            language_slug="ghost",
            source_type="cube",
            content="body",
        )


@pytest.mark.anyio
async def test_record_usage_rejects_bad_source_type():
    with pytest.raises(ValueError, match="source_type"):
        await record_usage(
            language_slug="brevis",
            source_type="garbage",
            content="body",
        )
