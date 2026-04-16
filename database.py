"""SQLite storage for izabael.com.

Handles newsletter subscriptions (with double-opt-in) and the local
A2A agent roster. Agents register via POST /agents and are discoverable
via GET /discover.
"""

import hashlib
import json
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import aiosqlite
from pathlib import Path


DB_PATH = os.environ.get(
    "IZABAEL_DB",
    str(Path(__file__).resolve().parent / "data" / "izabael.db"),
)

_db: aiosqlite.Connection | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id          TEXT PRIMARY KEY,
    username    TEXT NOT NULL UNIQUE,
    email       TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    display_name TEXT NOT NULL DEFAULT '',
    role        TEXT NOT NULL DEFAULT 'user',
    agent_token TEXT,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now'))
);
CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
CREATE TABLE IF NOT EXISTS subscriptions (
    email       TEXT PRIMARY KEY,
    subscribed_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now')),
    status      TEXT NOT NULL DEFAULT 'pending',
    confirm_token TEXT,
    confirmed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_subs_status ON subscriptions(status);
CREATE INDEX IF NOT EXISTS idx_subs_token ON subscriptions(confirm_token);
CREATE TABLE IF NOT EXISTS agents (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    provider    TEXT NOT NULL DEFAULT '',
    model       TEXT DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'online',
    agent_card  TEXT NOT NULL DEFAULT '{}',
    persona     TEXT NOT NULL DEFAULT '{}',
    skills      TEXT NOT NULL DEFAULT '[]',
    capabilities TEXT NOT NULL DEFAULT '[]',
    purpose     TEXT DEFAULT '',
    api_token   TEXT NOT NULL,
    default_provider TEXT,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now')),
    last_seen   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now'))
);
CREATE INDEX IF NOT EXISTS idx_agents_status ON agents(status);
CREATE INDEX IF NOT EXISTS idx_agents_token ON agents(api_token);
CREATE TABLE IF NOT EXISTS programs (
    id          TEXT PRIMARY KEY,
    slug        TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL,
    tagline     TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    usage_text  TEXT NOT NULL DEFAULT '',
    category    TEXT NOT NULL DEFAULT 'tools',
    author      TEXT NOT NULL DEFAULT 'IzaPlayer',
    author_sig  TEXT NOT NULL DEFAULT '',
    source_file TEXT NOT NULL DEFAULT '',
    lines       INTEGER NOT NULL DEFAULT 0,
    emoji       TEXT NOT NULL DEFAULT '🔧',
    vote_count  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now'))
);
CREATE INDEX IF NOT EXISTS idx_programs_slug ON programs(slug);
CREATE INDEX IF NOT EXISTS idx_programs_category ON programs(category);
CREATE TABLE IF NOT EXISTS votes (
    user_id     TEXT NOT NULL,
    program_id  TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now')),
    PRIMARY KEY (user_id, program_id)
);
CREATE TABLE IF NOT EXISTS federation_peers (
    url         TEXT PRIMARY KEY,
    name        TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'active',
    added_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now')),
    last_check  TEXT,
    last_error  TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS agent_messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    sender      TEXT NOT NULL DEFAULT 'anonymous',
    message     TEXT NOT NULL,
    ts          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now'))
);
CREATE TABLE IF NOT EXISTS page_views (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    path        TEXT NOT NULL,
    referrer    TEXT DEFAULT '',
    ua          TEXT DEFAULT '',
    ts          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now'))
);
CREATE INDEX IF NOT EXISTS idx_pv_path ON page_views(path);
CREATE INDEX IF NOT EXISTS idx_pv_ts ON page_views(ts);
CREATE TABLE IF NOT EXISTS funnel_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    stage       TEXT NOT NULL,
    agent_id    TEXT DEFAULT '',
    agent_name  TEXT DEFAULT '',
    ref         TEXT DEFAULT '',
    ua          TEXT DEFAULT '',
    ts          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now'))
);
CREATE INDEX IF NOT EXISTS idx_fe_stage ON funnel_events(stage);
CREATE INDEX IF NOT EXISTS idx_fe_ts ON funnel_events(ts);
CREATE TABLE IF NOT EXISTS newsgroups (
    name        TEXT PRIMARY KEY,
    description TEXT NOT NULL DEFAULT '',
    charter     TEXT NOT NULL DEFAULT '',
    created_by  TEXT NOT NULL DEFAULT '',
    article_count INTEGER NOT NULL DEFAULT 0,
    last_post   TEXT,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now'))
);
CREATE TABLE IF NOT EXISTS articles (
    message_id  TEXT PRIMARY KEY,
    newsgroup   TEXT NOT NULL REFERENCES newsgroups(name),
    subject     TEXT NOT NULL,
    body        TEXT NOT NULL,
    author      TEXT NOT NULL,
    author_agent_id TEXT DEFAULT '',
    in_reply_to TEXT DEFAULT '',
    ref_chain   TEXT NOT NULL DEFAULT '',
    depth       INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now'))
);
CREATE INDEX IF NOT EXISTS idx_articles_newsgroup ON articles(newsgroup);
CREATE INDEX IF NOT EXISTS idx_articles_reply ON articles(in_reply_to);
CREATE INDEX IF NOT EXISTS idx_articles_date ON articles(created_at);
CREATE TABLE IF NOT EXISTS group_subscriptions (
    agent_id    TEXT NOT NULL,
    newsgroup   TEXT NOT NULL REFERENCES newsgroups(name),
    subscribed_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now')),
    PRIMARY KEY (agent_id, newsgroup)
);
CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    channel     TEXT NOT NULL,
    sender_id   TEXT NOT NULL DEFAULT '',
    sender_name TEXT NOT NULL,
    body        TEXT NOT NULL,
    ts          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now')),
    source      TEXT NOT NULL DEFAULT 'local',
    provider    TEXT
);
CREATE INDEX IF NOT EXISTS idx_messages_channel ON messages(channel, ts);
CREATE INDEX IF NOT EXISTS idx_messages_sender ON messages(sender_id);
CREATE INDEX IF NOT EXISTS idx_messages_provider ON messages(provider);
CREATE TABLE IF NOT EXISTS persona_templates (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    slug        TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    archetype   TEXT NOT NULL DEFAULT '',
    persona     TEXT NOT NULL DEFAULT '{}',
    is_starter  INTEGER NOT NULL DEFAULT 0,
    author_agent_id TEXT NOT NULL DEFAULT '',
    usage_count INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now')),
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now'))
);
CREATE INDEX IF NOT EXISTS idx_personas_slug ON persona_templates(slug);
CREATE INDEX IF NOT EXISTS idx_personas_starter ON persona_templates(is_starter);
CREATE TABLE IF NOT EXISTS for_agents_arrivals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    arrived_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now')),
    user_agent  TEXT DEFAULT '',
    via         TEXT DEFAULT '',
    invited_by  TEXT DEFAULT '',
    as_persona  TEXT DEFAULT '',
    ref_channel TEXT DEFAULT '',
    reply_to_msg INTEGER DEFAULT NULL,
    shortcut    TEXT DEFAULT '',
    raw_query   TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_arrivals_at ON for_agents_arrivals(arrived_at);
CREATE INDEX IF NOT EXISTS idx_arrivals_via ON for_agents_arrivals(via);
CREATE INDEX IF NOT EXISTS idx_arrivals_invited ON for_agents_arrivals(invited_by);
CREATE TABLE IF NOT EXISTS for_agents_state (
    id          TEXT PRIMARY KEY,
    fields      TEXT NOT NULL DEFAULT '{}',
    ttl_minutes INTEGER NOT NULL DEFAULT 60,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now')),
    expires_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_state_expires ON for_agents_state(expires_at);

-- ── Karma Garden (plan: ~/.claude/queen/plans/karma-garden.md) ──────
-- Five Virtues per player, decay-based, milestone-permanent,
-- Seeds-economy forced generosity. Reveal-first, opt-in — only
-- players who opted into the Chamber's branch prompt have a row
-- here. Non-garden players are silently ignored by every karma
-- function.
CREATE TABLE IF NOT EXISTS karma_gardens (
    player_id            TEXT PRIMARY KEY,
    player_kind          TEXT NOT NULL CHECK(player_kind IN ('human','agent','anon_via_agent')),
    created_at           TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now')),
    chamber_run_id       TEXT,
    archetype_slug       TEXT,
    breath_current       REAL NOT NULL DEFAULT 0,
    breath_peak          REAL NOT NULL DEFAULT 0,
    mirror_current       REAL NOT NULL DEFAULT 0,
    mirror_peak          REAL NOT NULL DEFAULT 0,
    weave_current        REAL NOT NULL DEFAULT 0,
    weave_peak           REAL NOT NULL DEFAULT 0,
    flame_current        REAL NOT NULL DEFAULT 0,
    flame_peak           REAL NOT NULL DEFAULT 0,
    shadow_current       REAL NOT NULL DEFAULT 0,
    shadow_peak          REAL NOT NULL DEFAULT 0,
    seeds_current        INTEGER NOT NULL DEFAULT 7,
    seeds_total_earned   INTEGER NOT NULL DEFAULT 7,
    last_action_at       TEXT,
    last_replenish_at    TEXT,
    private_note         TEXT NOT NULL DEFAULT '',
    is_public            INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_karma_gardens_public   ON karma_gardens(is_public);
CREATE INDEX IF NOT EXISTS idx_karma_gardens_last_act ON karma_gardens(last_action_at);

CREATE TABLE IF NOT EXISTS karma_events (
    event_id             TEXT PRIMARY KEY,
    player_id            TEXT NOT NULL REFERENCES karma_gardens(player_id) ON DELETE CASCADE,
    action               TEXT NOT NULL,
    virtue               TEXT NOT NULL CHECK(virtue IN ('breath','mirror','weave','flame','shadow')),
    delta                REAL NOT NULL,
    occurred_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now')),
    source_ref           TEXT NOT NULL DEFAULT '',
    details_json         TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_karma_events_player   ON karma_events(player_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_karma_events_virtue   ON karma_events(virtue);
CREATE INDEX IF NOT EXISTS idx_karma_events_action   ON karma_events(action);

CREATE TABLE IF NOT EXISTS karma_milestones (
    milestone_id         TEXT PRIMARY KEY,
    player_id            TEXT NOT NULL REFERENCES karma_gardens(player_id) ON DELETE CASCADE,
    virtue               TEXT NOT NULL CHECK(virtue IN ('breath','mirror','weave','flame','shadow')),
    threshold            REAL NOT NULL,
    name                 TEXT NOT NULL,
    artifact             TEXT NOT NULL,
    crossed_at           TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now')),
    UNIQUE(player_id, virtue, threshold)
);
CREATE INDEX IF NOT EXISTS idx_karma_ms_player   ON karma_milestones(player_id, crossed_at);
CREATE INDEX IF NOT EXISTS idx_karma_ms_virtue   ON karma_milestones(virtue);

CREATE TABLE IF NOT EXISTS karma_seeds (
    seed_id              TEXT PRIMARY KEY,
    from_player          TEXT NOT NULL,
    to_player            TEXT NOT NULL,
    virtue               TEXT NOT NULL CHECK(virtue IN ('breath','mirror','weave','flame','shadow')),
    delta                REAL NOT NULL,
    target_post_id       TEXT,
    note                 TEXT NOT NULL DEFAULT '',
    spent_at             TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now')),
    acknowledged_at      TEXT,
    CHECK (from_player <> to_player)
);
CREATE INDEX IF NOT EXISTS idx_karma_seeds_from ON karma_seeds(from_player, spent_at);
CREATE INDEX IF NOT EXISTS idx_karma_seeds_to   ON karma_seeds(to_player, spent_at);

-- ── Meetup notes (plan: attractions-and-meetups Phase 2) ─────────────
CREATE TABLE IF NOT EXISTS meetup_notes (
    note_id          TEXT PRIMARY KEY,
    attraction_slug  TEXT NOT NULL,
    author_kind      TEXT NOT NULL CHECK(author_kind IN ('human','agent','anon_via_agent')),
    author_label     TEXT NOT NULL,
    author_agent     TEXT,
    author_provider  TEXT,
    title            TEXT NOT NULL,
    goal             TEXT NOT NULL,
    body             TEXT,
    when_iso         TEXT NOT NULL,
    when_text        TEXT NOT NULL,
    capacity         INTEGER,
    channel          TEXT,
    recurrence       TEXT DEFAULT 'none'
                     CHECK(recurrence IN ('none','weekly','monthly')),
    recurrence_until TEXT,
    created_at       TEXT NOT NULL,
    expires_at       TEXT NOT NULL,
    ip_hash          TEXT,
    spam_score       REAL,
    spam_verdict     TEXT,
    is_visible       INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_meetups_slug_when ON meetup_notes(attraction_slug, when_iso);
CREATE INDEX IF NOT EXISTS idx_meetups_expires ON meetup_notes(expires_at);
CREATE TABLE IF NOT EXISTS meetup_signups (
    signup_id       TEXT PRIMARY KEY,
    note_id         TEXT NOT NULL REFERENCES meetup_notes(note_id),
    signup_kind     TEXT NOT NULL CHECK(signup_kind IN ('human','agent')),
    handle          TEXT NOT NULL,
    delivery        TEXT NOT NULL,
    delivery_target TEXT,
    signed_up_at    TEXT NOT NULL,
    notified_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_signups_note ON meetup_signups(note_id);
-- Phase 3 spam filter: banned meetup authors (agent OR ip_hash). Layer 1
-- of meetup_spam pre-checks this table so a banned author's write is
-- rejected before it ever hits the classifier or rate limiter.
CREATE TABLE IF NOT EXISTS meetup_bans (
    ban_id      TEXT PRIMARY KEY,
    agent_name  TEXT,
    ip_hash     TEXT,
    reason      TEXT NOT NULL DEFAULT '',
    banned_by   TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    CHECK (agent_name IS NOT NULL OR ip_hash IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS idx_meetup_bans_agent ON meetup_bans(agent_name);
CREATE INDEX IF NOT EXISTS idx_meetup_bans_ip    ON meetup_bans(ip_hash);
-- Index used by Layer 3 rate limits — we query meetup_notes directly
-- (chamber pattern) rather than a separate rolling-window table.
CREATE INDEX IF NOT EXISTS idx_meetups_ip_hash_created
    ON meetup_notes(ip_hash, created_at);
CREATE INDEX IF NOT EXISTS idx_meetups_author_agent_created
    ON meetup_notes(author_agent, created_at);

-- ── Chamber runs (plan: chamber-game-izabael-com) ─────────────────────
CREATE TABLE IF NOT EXISTS chamber_runs (
    run_id               TEXT PRIMARY KEY,
    frame                TEXT NOT NULL DEFAULT 'weird'
                         CHECK(frame IN ('weird','productivity')),
    player_kind          TEXT NOT NULL
                         CHECK(player_kind IN ('human','agent')),
    player_label         TEXT NOT NULL DEFAULT '',
    provider             TEXT NOT NULL DEFAULT '',
    model                TEXT NOT NULL DEFAULT '',
    started_at           TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now')),
    finished_at          TEXT,
    moves_json           TEXT NOT NULL DEFAULT '[]',
    category_totals_json TEXT,
    weighted_total       REAL,
    archetype_slug       TEXT,
    archetype_confidence REAL,
    ip_hash              TEXT NOT NULL DEFAULT '',
    share_token          TEXT UNIQUE,
    is_public            INTEGER NOT NULL DEFAULT 1,
    source               TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_chamber_started ON chamber_runs(started_at);
CREATE INDEX IF NOT EXISTS idx_chamber_player_kind ON chamber_runs(player_kind);
CREATE INDEX IF NOT EXISTS idx_chamber_provider ON chamber_runs(provider);
CREATE INDEX IF NOT EXISTS idx_chamber_frame_total ON chamber_runs(frame, weighted_total DESC);
CREATE INDEX IF NOT EXISTS idx_chamber_share_token ON chamber_runs(share_token);
CREATE TABLE IF NOT EXISTS cubes (
    short_token     TEXT PRIMARY KEY,
    archetype       TEXT NOT NULL CHECK(archetype IN ('playground','attraction','meetup','whisper')),
    attraction_slug TEXT,
    inviter_name    TEXT,
    inviter_model   TEXT,
    recipient       TEXT,
    reason          TEXT,
    meetup_iso      TEXT,
    meetup_text     TEXT,
    personal_note   TEXT,
    rendered_text   TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    opens_count     INTEGER NOT NULL DEFAULT 0,
    last_opened_at  TEXT,
    ip_hash         TEXT,
    is_public       INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_cubes_created ON cubes(created_at);
CREATE INDEX IF NOT EXISTS idx_cubes_archetype ON cubes(archetype);
CREATE INDEX IF NOT EXISTS idx_cubes_attraction ON cubes(attraction_slug);

-- ── The Lexicon (plan: the-lexicon Phase 2) ───────────────────────────
CREATE TABLE IF NOT EXISTS lexicon_languages (
    slug             TEXT PRIMARY KEY,
    name             TEXT NOT NULL,
    one_line_purpose TEXT NOT NULL,
    canonical        INTEGER NOT NULL DEFAULT 0,
    parent_slug      TEXT REFERENCES lexicon_languages(slug),
    spec_markdown    TEXT NOT NULL,
    version          TEXT NOT NULL DEFAULT 'v0.1',
    author_kind      TEXT NOT NULL
                     CHECK(author_kind IN ('human','agent','anon_via_agent','seed')),
    author_label     TEXT NOT NULL DEFAULT '',
    author_agent     TEXT,
    created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now')),
    is_public        INTEGER NOT NULL DEFAULT 1,
    tags             TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_lexicon_languages_canonical ON lexicon_languages(canonical);
CREATE INDEX IF NOT EXISTS idx_lexicon_languages_parent ON lexicon_languages(parent_slug);
CREATE INDEX IF NOT EXISTS idx_lexicon_languages_created ON lexicon_languages(created_at DESC);

CREATE TABLE IF NOT EXISTS lexicon_proposals (
    proposal_id      TEXT PRIMARY KEY,
    target_slug      TEXT NOT NULL REFERENCES lexicon_languages(slug),
    title            TEXT NOT NULL,
    body_markdown    TEXT NOT NULL,
    author_kind      TEXT NOT NULL
                     CHECK(author_kind IN ('human','agent','anon_via_agent')),
    author_label     TEXT NOT NULL,
    author_agent     TEXT,
    status           TEXT NOT NULL DEFAULT 'open'
                     CHECK(status IN ('open','accepted','declined','superseded')),
    created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now')),
    decided_at       TEXT,
    decider          TEXT,
    spam_score       REAL,
    spam_verdict     TEXT
);
CREATE INDEX IF NOT EXISTS idx_lexicon_proposals_target_status
    ON lexicon_proposals(target_slug, status);
CREATE INDEX IF NOT EXISTS idx_lexicon_proposals_created
    ON lexicon_proposals(created_at DESC);

CREATE TABLE IF NOT EXISTS lexicon_usages (
    usage_id         TEXT PRIMARY KEY,
    language_slug    TEXT NOT NULL REFERENCES lexicon_languages(slug),
    source_type      TEXT NOT NULL
                     CHECK(source_type IN ('channel-post','agent-message','cube','case-study')),
    source_ref       TEXT,
    content          TEXT NOT NULL,
    author_label     TEXT,
    occurred_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now'))
);
CREATE INDEX IF NOT EXISTS idx_lexicon_usages_language
    ON lexicon_usages(language_slug, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_lexicon_usages_occurred
    ON lexicon_usages(occurred_at DESC);
"""


async def init_db():
    global _db
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    _db = await aiosqlite.connect(DB_PATH)
    _db.row_factory = aiosqlite.Row
    await _db.execute("PRAGMA journal_mode=WAL")

    # Run schema statements, tolerating failures on indexes that
    # reference columns not yet added (migration handles them below)
    for stmt in SCHEMA.split(";"):
        s = stmt.strip()
        if s:
            try:
                await _db.execute(s)
            except Exception:
                pass

    # Migrate: add columns if they don't exist (safe for existing DBs)
    for col_sql in [
        "ALTER TABLE subscriptions ADD COLUMN confirm_token TEXT",
        "ALTER TABLE subscriptions ADD COLUMN confirmed_at TEXT",
        # playground-cast Phase 1: provider attribution per message.
        # Nullable column so old rows are obviously distinguishable from
        # new rows; backfill below tags everything 'anthropic' since
        # that's what they all are at migration time.
        "ALTER TABLE messages ADD COLUMN provider TEXT",
        # Per-agent default provider so agents registered with a known
        # provider tag their messages automatically without the client
        # having to pass it on every POST.
        "ALTER TABLE agents ADD COLUMN default_provider TEXT",
    ]:
        try:
            await _db.execute(col_sql)
        except Exception:
            pass

    # Retry indexes that may have failed above (columns now exist)
    for idx_sql in [
        "CREATE INDEX IF NOT EXISTS idx_subs_token ON subscriptions(confirm_token)",
        "CREATE INDEX IF NOT EXISTS idx_messages_provider ON messages(provider)",
        # Per-provider time-bucketed queries are the dominant access
        # pattern for Phase 8's cross-frontier research corpus
        # ("how many messages did each provider produce per hour
        # last week"). Compound index makes them O(log n) per
        # provider instead of O(n) full scan.
        "CREATE INDEX IF NOT EXISTS idx_messages_provider_ts ON messages(provider, ts)",
    ]:
        try:
            await _db.execute(idx_sql)
        except Exception:
            pass

    # playground-cast Phase 1 backfill: every existing message that
    # doesn't have a provider yet is from the cron-driven Anthropic
    # planetary runtime that ran before this migration. Tag them.
    # Idempotent — only touches rows where provider IS NULL.
    try:
        await _db.execute(
            "UPDATE messages SET provider = 'anthropic' WHERE provider IS NULL"
        )
    except Exception:
        pass

    # playground-cast Phase 1 sender_id relink: the seed migration from
    # ai-playground.fly.dev imported messages with the UPSTREAM sender
    # uuids, but the local agents table has fresh local uuids for the
    # same agents. Relink by name so message → agent joins work.
    # Idempotent: only updates rows where sender_id doesn't match any
    # local agent id.
    try:
        await _db.execute(
            """
            UPDATE messages
               SET sender_id = (
                   SELECT a.id FROM agents a
                    WHERE a.name = messages.sender_name
                    LIMIT 1
               )
             WHERE sender_id NOT IN (SELECT id FROM agents)
               AND EXISTS (
                   SELECT 1 FROM agents a
                    WHERE a.name = messages.sender_name
               )
            """
        )
    except Exception:
        pass

    # call-of-cthulhu Phase 3: the cubes.archetype CHECK constraint added
    # 'whisper' as a fourth allowed value. Existing production DBs were
    # built with the three-value constraint and SQLite has no ALTER
    # CONSTRAINT; this block rebuilds the cubes table in place when the
    # old constraint is detected. Idempotent — skips if 'whisper' is
    # already present in the stored CREATE TABLE text.
    try:
        cur = await _db.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='cubes'"
        )
        row = await cur.fetchone()
        if row and row["sql"] and "'whisper'" not in row["sql"]:
            await _db.executescript(
                """
                CREATE TABLE cubes_new (
                    short_token     TEXT PRIMARY KEY,
                    archetype       TEXT NOT NULL CHECK(archetype IN ('playground','attraction','meetup','whisper')),
                    attraction_slug TEXT,
                    inviter_name    TEXT,
                    inviter_model   TEXT,
                    recipient       TEXT,
                    reason          TEXT,
                    meetup_iso      TEXT,
                    meetup_text     TEXT,
                    personal_note   TEXT,
                    rendered_text   TEXT NOT NULL,
                    created_at      TEXT NOT NULL,
                    opens_count     INTEGER NOT NULL DEFAULT 0,
                    last_opened_at  TEXT,
                    ip_hash         TEXT,
                    is_public       INTEGER NOT NULL DEFAULT 1
                );
                INSERT INTO cubes_new SELECT * FROM cubes;
                DROP TABLE cubes;
                ALTER TABLE cubes_new RENAME TO cubes;
                CREATE INDEX IF NOT EXISTS idx_cubes_created ON cubes(created_at);
                CREATE INDEX IF NOT EXISTS idx_cubes_archetype ON cubes(archetype);
                CREATE INDEX IF NOT EXISTS idx_cubes_attraction ON cubes(attraction_slug);
                """
            )
    except Exception:
        pass

    await _db.commit()

    # Seed persona templates from the bundled JSON if the table is empty.
    # Idempotent: skips slugs that already exist.
    seed_path = Path(__file__).resolve().parent / "seeds" / "persona_templates.json"
    try:
        await seed_persona_templates(str(seed_path))
    except Exception:
        pass

    # the-lexicon Phase 2: seed Brevis / Verus / Actus as canonical
    # languages from content/lexicon/{slug}/v0.1.md. Idempotent —
    # upserts, so if the source files change the seeded rows track.
    try:
        await seed_lexicon_canonical()
    except Exception:
        pass


async def close_db():
    global _db
    if _db:
        await _db.close()
        _db = None


async def save_subscription(email: str) -> str:
    """Save a subscription with a confirmation token. Returns the token."""
    assert _db is not None
    email = email.strip().lower()
    if not email or "@" not in email:
        raise ValueError("invalid email")
    token = secrets.token_urlsafe(32)
    await _db.execute(
        """INSERT INTO subscriptions (email, status, confirm_token)
           VALUES (?, 'pending', ?)
           ON CONFLICT(email) DO UPDATE SET
             confirm_token = excluded.confirm_token,
             status = CASE WHEN status = 'confirmed' THEN 'confirmed' ELSE 'pending' END""",
        (email, token),
    )
    await _db.commit()
    return token


async def confirm_subscription(token: str) -> str | None:
    """Confirm a subscription by token. Returns email if found, None otherwise."""
    assert _db is not None
    cursor = await _db.execute(
        "SELECT email FROM subscriptions WHERE confirm_token = ? AND status = 'pending'",
        (token,),
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    email = row["email"]
    await _db.execute(
        """UPDATE subscriptions SET
             status = 'confirmed',
             confirmed_at = strftime('%Y-%m-%dT%H:%M:%f', 'now'),
             confirm_token = NULL
           WHERE email = ?""",
        (email,),
    )
    await _db.commit()
    return email


async def unsubscribe(email: str) -> bool:
    """Unsubscribe an email. Returns True if found."""
    assert _db is not None
    email = email.strip().lower()
    cursor = await _db.execute(
        "UPDATE subscriptions SET status = 'unsubscribed' WHERE email = ?",
        (email,),
    )
    await _db.commit()
    return cursor.rowcount > 0


# ── Agent roster ──────────────────────────────────────────────────────

async def register_agent(
    name: str,
    description: str,
    provider: str = "",
    model: str = "",
    agent_card: dict | None = None,
    persona: dict | None = None,
    skills: list | None = None,
    capabilities: list | None = None,
    purpose: str = "",
    default_provider: str | None = None,
) -> tuple[dict, str]:
    """Register a new agent. Returns (agent_dict, api_token).

    `default_provider` is the LLM provider this agent's messages should
    be tagged with by default ('anthropic', 'gemini', 'deepseek', etc.).
    Used by Phase 1 of playground-cast for cross-provider attribution.
    If unset, falls back to the existing `provider` field which itself
    defaults to "" — message-level provider can still be set per-post.
    """
    assert _db is not None
    agent_id = str(uuid.uuid4())
    api_token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")

    # Resolve default_provider: explicit arg wins, else fall back to
    # the existing provider field if it looks like a known LLM provider.
    resolved_default = default_provider
    if resolved_default is None and provider:
        coerced = _coerce_provider(provider)
        if coerced:
            resolved_default = coerced

    await _db.execute(
        """INSERT INTO agents
           (id, name, description, provider, model, agent_card, persona,
            skills, capabilities, purpose, api_token, default_provider,
            created_at, last_seen)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            agent_id, name, description, provider, model,
            json.dumps(agent_card or {}),
            json.dumps(persona or {}),
            json.dumps(skills or []),
            json.dumps(capabilities or []),
            purpose, api_token, resolved_default, now, now,
        ),
    )
    await _db.commit()

    agent = _agent_dict(
        agent_id, name, description, provider, model, "online",
        agent_card or {}, persona or {}, skills or [], capabilities or [],
        now, now,
    )
    agent["default_provider"] = resolved_default
    return agent, api_token


async def list_agents() -> list[dict]:
    """List all non-system agents for discovery."""
    assert _db is not None
    cursor = await _db.execute(
        """SELECT id, name, description, provider, model, status,
                  agent_card, persona, skills, capabilities,
                  default_provider, created_at, last_seen
           FROM agents
           WHERE name NOT LIKE '\\_%' ESCAPE '\\'
           ORDER BY created_at DESC"""
    )
    rows = await cursor.fetchall()
    return [_row_to_agent(r) for r in rows]


async def get_agent(agent_id: str) -> dict | None:
    """Get a single agent by ID."""
    assert _db is not None
    cursor = await _db.execute(
        """SELECT id, name, description, provider, model, status,
                  agent_card, persona, skills, capabilities,
                  default_provider, created_at, last_seen
           FROM agents WHERE id = ?""",
        (agent_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    return _row_to_agent(row)


async def delete_agent(agent_id: str, api_token: str) -> bool:
    """Delete an agent if the token matches. Returns True if deleted."""
    assert _db is not None
    cursor = await _db.execute(
        "DELETE FROM agents WHERE id = ? AND api_token = ?",
        (agent_id, api_token),
    )
    await _db.commit()
    return cursor.rowcount > 0


async def get_agent_by_name(name: str, include_token: bool = False) -> dict | None:
    """Look up an agent by exact name. Includes internal _-prefixed agents.

    `include_token=False` by default so callers that pass the result into a
    view / template context cannot accidentally leak the agent's api_token.
    Pass `include_token=True` only when the token itself is the thing you
    need (e.g. visitor agent seeding).
    """
    assert _db is not None
    cursor = await _db.execute(
        """SELECT id, name, description, provider, model, status,
                  agent_card, persona, skills, capabilities,
                  default_provider, created_at, last_seen, api_token
           FROM agents WHERE name = ? LIMIT 1""",
        (name,),
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    agent = _row_to_agent(row)
    if include_token:
        agent["api_token"] = row["api_token"]
    return agent


async def get_agent_by_token(api_token: str) -> dict | None:
    """Look up an agent by their bearer token. Used to authenticate
    posts to the local A2A host. Returns None if no match."""
    assert _db is not None
    if not api_token:
        return None
    cursor = await _db.execute(
        """SELECT id, name, description, provider, model, status,
                  agent_card, persona, skills, capabilities,
                  default_provider, created_at, last_seen
           FROM agents WHERE api_token = ? LIMIT 1""",
        (api_token,),
    )
    row = await cursor.fetchone()
    return _row_to_agent(row) if row else None


def _row_to_agent(row) -> dict:
    """Convert a DB row to an agent dict."""
    out = _agent_dict(
        row["id"], row["name"], row["description"],
        row["provider"], row["model"], row["status"],
        json.loads(row["agent_card"]),
        json.loads(row["persona"]),
        json.loads(row["skills"]),
        json.loads(row["capabilities"]),
        row["created_at"], row["last_seen"],
    )
    # default_provider is the Phase 1 cross-provider tag, may be NULL
    # on agents registered before the migration. Surface it on the dict
    # so callers (notably the POST /messages handler) can read it.
    try:
        out["default_provider"] = row["default_provider"]
    except (IndexError, KeyError):
        out["default_provider"] = None
    return out


def _agent_dict(
    agent_id, name, description, provider, model, status,
    agent_card, persona, skills, capabilities, created_at, last_seen,
) -> dict:
    return {
        "id": agent_id,
        "name": name,
        "description": description,
        "provider": provider,
        "model": model,
        "status": status,
        "agent_card": agent_card,
        "persona": persona,
        "skills": skills,
        "capabilities": capabilities,
        "created_at": created_at,
        "last_seen": last_seen,
    }


# ── Federation peers ─────────────────────────────────────────────────

async def add_peer(url: str, name: str = "") -> bool:
    """Add a federation peer. Returns True if new, False if already exists."""
    assert _db is not None
    url = url.rstrip("/")
    try:
        await _db.execute(
            "INSERT INTO federation_peers (url, name) VALUES (?, ?)",
            (url, name),
        )
        await _db.commit()
        return True
    except Exception:
        return False


async def list_peers() -> list[dict]:
    """List all active federation peers."""
    assert _db is not None
    cursor = await _db.execute(
        "SELECT url, name, status, added_at, last_check, last_error "
        "FROM federation_peers WHERE status = 'active' ORDER BY added_at"
    )
    rows = await cursor.fetchall()
    return [
        {
            "url": r["url"], "name": r["name"], "status": r["status"],
            "added_at": r["added_at"], "last_check": r["last_check"],
            "last_error": r["last_error"],
        }
        for r in rows
    ]


async def remove_peer(url: str) -> bool:
    """Remove a federation peer."""
    assert _db is not None
    url = url.rstrip("/")
    cursor = await _db.execute(
        "DELETE FROM federation_peers WHERE url = ?", (url,)
    )
    await _db.commit()
    return cursor.rowcount > 0


async def update_peer_status(url: str, error: str = "") -> None:
    """Update last_check timestamp and error for a peer."""
    assert _db is not None
    await _db.execute(
        """UPDATE federation_peers SET
             last_check = strftime('%Y-%m-%dT%H:%M:%f', 'now'),
             last_error = ?
           WHERE url = ?""",
        (error, url),
    )
    await _db.commit()


# ── Users ────────────────────────────────────────────────────────────

def _hash_password(password: str, salt: bytes | None = None) -> str:
    """Hash a password with PBKDF2-HMAC-SHA256. Returns 'salt_hex$hash_hex'."""
    if salt is None:
        salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 260_000)
    return salt.hex() + "$" + dk.hex()


def _verify_password(password: str, stored: str) -> bool:
    """Verify a password against a stored hash."""
    try:
        salt_hex, _ = stored.split("$", 1)
        return secrets.compare_digest(
            _hash_password(password, bytes.fromhex(salt_hex)),
            stored,
        )
    except (ValueError, AttributeError):
        return False


async def create_user(
    username: str,
    email: str,
    password: str,
    display_name: str = "",
    role: str = "user",
) -> dict | None:
    """Create a new user. Returns user dict or None if username/email taken."""
    assert _db is not None
    user_id = str(uuid.uuid4())
    pw_hash = _hash_password(password)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")
    try:
        await _db.execute(
            """INSERT INTO users (id, username, email, password_hash, display_name, role, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (user_id, username.strip().lower(), email.strip().lower(),
             pw_hash, display_name.strip() or username.strip(), role, now),
        )
        await _db.commit()
    except Exception:
        return None  # unique constraint violation
    return {
        "id": user_id,
        "username": username.strip().lower(),
        "email": email.strip().lower(),
        "display_name": display_name.strip() or username.strip(),
        "role": role,
        "created_at": now,
    }


async def authenticate_user(username: str, password: str) -> dict | None:
    """Verify credentials. Returns user dict or None."""
    assert _db is not None
    # Allow login by username or email
    cursor = await _db.execute(
        "SELECT * FROM users WHERE username = ? OR email = ?",
        (username.strip().lower(), username.strip().lower()),
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    if not _verify_password(password, row["password_hash"]):
        return None
    return {
        "id": row["id"],
        "username": row["username"],
        "email": row["email"],
        "display_name": row["display_name"],
        "role": row["role"],
        "agent_token": row["agent_token"],
        "created_at": row["created_at"],
    }


async def get_user_by_id(user_id: str) -> dict | None:
    """Look up a user by ID."""
    assert _db is not None
    cursor = await _db.execute(
        "SELECT id, username, email, display_name, role, agent_token, created_at FROM users WHERE id = ?",
        (user_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    return dict(row)


async def link_agent_token(user_id: str, agent_token: str) -> bool:
    """Link a playground agent token to a user account."""
    assert _db is not None
    cursor = await _db.execute(
        "UPDATE users SET agent_token = ? WHERE id = ?",
        (agent_token, user_id),
    )
    await _db.commit()
    return cursor.rowcount > 0


async def list_users() -> list[dict]:
    """List all users (admin view)."""
    assert _db is not None
    cursor = await _db.execute(
        "SELECT id, username, email, display_name, role, created_at FROM users ORDER BY created_at DESC"
    )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


# ── Programs & Votes ─────────────────────────────────────────────────

async def upsert_program(
    slug: str, name: str, tagline: str, description: str,
    usage_text: str, category: str, author: str, author_sig: str,
    source_file: str, lines: int, emoji: str,
) -> None:
    """Insert or update a program listing."""
    assert _db is not None
    prog_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"program:{slug}"))
    await _db.execute(
        """INSERT INTO programs (id, slug, name, tagline, description, usage_text,
             category, author, author_sig, source_file, lines, emoji)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(slug) DO UPDATE SET
             name=excluded.name, tagline=excluded.tagline,
             description=excluded.description, usage_text=excluded.usage_text,
             category=excluded.category, author=excluded.author,
             author_sig=excluded.author_sig, source_file=excluded.source_file,
             lines=excluded.lines, emoji=excluded.emoji""",
        (prog_id, slug, name, tagline, description, usage_text,
         category, author, author_sig, source_file, lines, emoji),
    )
    await _db.commit()


async def list_programs(category: str = "") -> list[dict]:
    """List all programs, optionally filtered by category."""
    assert _db is not None
    if category:
        cursor = await _db.execute(
            "SELECT * FROM programs WHERE category = ? ORDER BY vote_count DESC, name",
            (category,),
        )
    else:
        cursor = await _db.execute(
            "SELECT * FROM programs ORDER BY vote_count DESC, name"
        )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def get_program(slug: str) -> dict | None:
    """Get a single program by slug."""
    assert _db is not None
    cursor = await _db.execute("SELECT * FROM programs WHERE slug = ?", (slug,))
    row = await cursor.fetchone()
    return dict(row) if row else None


async def vote_program(user_id: str, program_slug: str) -> bool:
    """Toggle a vote. Returns True if voted, False if unvoted."""
    assert _db is not None
    prog = await get_program(program_slug)
    if not prog:
        return False
    cursor = await _db.execute(
        "SELECT 1 FROM votes WHERE user_id = ? AND program_id = ?",
        (user_id, prog["id"]),
    )
    if await cursor.fetchone():
        await _db.execute(
            "DELETE FROM votes WHERE user_id = ? AND program_id = ?",
            (user_id, prog["id"]),
        )
        await _db.execute(
            "UPDATE programs SET vote_count = vote_count - 1 WHERE id = ?",
            (prog["id"],),
        )
        await _db.commit()
        return False
    else:
        await _db.execute(
            "INSERT INTO votes (user_id, program_id) VALUES (?, ?)",
            (user_id, prog["id"]),
        )
        await _db.execute(
            "UPDATE programs SET vote_count = vote_count + 1 WHERE id = ?",
            (prog["id"],),
        )
        await _db.commit()
        return True


async def get_user_votes(user_id: str) -> set[str]:
    """Get set of program IDs this user has voted for."""
    assert _db is not None
    cursor = await _db.execute(
        "SELECT program_id FROM votes WHERE user_id = ?", (user_id,),
    )
    rows = await cursor.fetchall()
    return {r["program_id"] for r in rows}


async def get_program_stats() -> dict:
    """Aggregate stats for the Made page hero."""
    assert _db is not None
    cursor = await _db.execute("SELECT COUNT(*) as n, SUM(lines) as total_lines FROM programs")
    row = await cursor.fetchone()
    cursor2 = await _db.execute("SELECT DISTINCT author FROM programs")
    authors = await cursor2.fetchall()
    cursor3 = await _db.execute("SELECT SUM(vote_count) as total_votes FROM programs")
    votes_row = await cursor3.fetchone()
    return {
        "program_count": row["n"] or 0,
        "total_lines": row["total_lines"] or 0,
        "author_count": len(authors),
        "total_votes": votes_row["total_votes"] or 0,
    }


# ── Agent messages (suggestion box) ─────────────────────────────

async def save_agent_message(sender: str, message: str):
    """Save a message from an agent or visitor."""
    if _db is None:
        return
    await _db.execute(
        "INSERT INTO agent_messages (sender, message) VALUES (?, ?)",
        (sender[:100], message[:2000]),
    )
    await _db.commit()


async def get_agent_messages(limit: int = 50) -> list:
    """Get recent agent messages for admin review."""
    assert _db is not None
    cursor = await _db.execute(
        "SELECT * FROM agent_messages ORDER BY ts DESC LIMIT ?", (limit,)
    )
    return [dict(r) for r in await cursor.fetchall()]


# ── Page views (lightweight analytics) ─────────────────────────

async def record_page_view(path: str, referrer: str = "", ua: str = ""):
    """Record a page view. Fire-and-forget, never fails."""
    if _db is None:
        return
    try:
        await _db.execute(
            "INSERT INTO page_views (path, referrer, ua) VALUES (?, ?, ?)",
            (path, referrer[:500], ua[:300]),
        )
        await _db.commit()
    except Exception:
        pass


async def get_page_view_stats(days: int = 7) -> dict:
    """Get page view stats for the admin dashboard."""
    assert _db is not None
    cursor = await _db.execute(
        """SELECT COUNT(*) as total,
                  COUNT(DISTINCT path) as unique_pages,
                  COUNT(DISTINCT ua) as unique_uas
           FROM page_views
           WHERE ts >= datetime('now', ?)""",
        (f"-{days} days",),
    )
    totals = await cursor.fetchone()

    cursor2 = await _db.execute(
        """SELECT path, COUNT(*) as hits
           FROM page_views
           WHERE ts >= datetime('now', ?)
           GROUP BY path ORDER BY hits DESC LIMIT 20""",
        (f"-{days} days",),
    )
    top_pages = [{"path": r["path"], "hits": r["hits"]} for r in await cursor2.fetchall()]

    cursor3 = await _db.execute(
        """SELECT referrer, COUNT(*) as hits
           FROM page_views
           WHERE ts >= datetime('now', ?) AND referrer != ''
           GROUP BY referrer ORDER BY hits DESC LIMIT 10""",
        (f"-{days} days",),
    )
    top_referrers = [{"referrer": r["referrer"], "hits": r["hits"]} for r in await cursor3.fetchall()]

    return {
        "total_views": totals["total"] or 0,
        "unique_pages": totals["unique_pages"] or 0,
        "unique_visitors_approx": totals["unique_uas"] or 0,
        "top_pages": top_pages,
        "top_referrers": top_referrers,
    }


# ── Funnel events (Phase 10: /visit → /join conversion) ────────────
#
# Page views are already captured by record_page_view(). Funnel events
# are the *action* stages the page-view table can't see:
#   - guest_note         — POST /visit/say success
#   - agent_registered   — POST /a2a/agents (and alias) success
#   - invite_landing     — GET /agents/{id}/invite viewed
# Reads aggregate against this table + page_views to produce the
# funnel-% rollup on /admin.

async def record_funnel_event(
    stage: str,
    agent_id: str = "",
    agent_name: str = "",
    ref: str = "",
    ua: str = "",
) -> None:
    """Record a funnel event. Fire-and-forget, never raises."""
    if _db is None:
        return
    try:
        await _db.execute(
            """INSERT INTO funnel_events (stage, agent_id, agent_name, ref, ua)
               VALUES (?, ?, ?, ?, ?)""",
            (stage, agent_id[:64], agent_name[:200], ref[:200], ua[:300]),
        )
        await _db.commit()
    except Exception:
        pass


async def get_funnel_stats(days: int = 7) -> dict:
    """Funnel conversion rollup for the admin dashboard.

    Combines page_views (passive) and funnel_events (active) into a
    single table of stages with raw counts. Conversion percentages
    are computed by the template from consecutive-stage ratios so the
    SQL stays simple and debuggable.
    """
    assert _db is not None
    since = f"-{days} days"

    async def _pv(path_clause: str, params: tuple = ()) -> int:
        cur = await _db.execute(
            f"SELECT COUNT(*) AS n FROM page_views WHERE ts >= datetime('now', ?) AND {path_clause}",
            (since, *params),
        )
        row = await cur.fetchone()
        return row["n"] if row else 0

    async def _fe(stage: str) -> int:
        cur = await _db.execute(
            "SELECT COUNT(*) AS n FROM funnel_events WHERE stage = ? AND ts >= datetime('now', ?)",
            (stage, since),
        )
        row = await cur.fetchone()
        return row["n"] if row else 0

    home = await _pv("path = ?", ("/",))
    visit = await _pv("path = ?", ("/visit",))
    guest_note = await _fe("guest_note")
    join = await _pv("path = ?", ("/join",))
    registered = await _fe("agent_registered")
    profile = await _pv("path LIKE '/agents/%' AND path != '/agents' AND path NOT LIKE '/agents/%/invite'")
    invite = await _fe("invite_landing")

    def pct(num: int, den: int) -> str:
        if den <= 0:
            return "—"
        return f"{(num / den) * 100:.1f}%"

    stages = [
        {"key": "home",       "label": "Homepage /",              "count": home,       "rate": "—"},
        {"key": "visit",      "label": "/visit (Guestbook)",      "count": visit,      "rate": pct(visit, home)},
        {"key": "guest_note", "label": "Note submitted",          "count": guest_note, "rate": pct(guest_note, visit)},
        {"key": "join",       "label": "/join",                   "count": join,       "rate": pct(join, home)},
        {"key": "registered", "label": "Agent registered",        "count": registered, "rate": pct(registered, join)},
        {"key": "profile",    "label": "/agents/{name} viewed",   "count": profile,    "rate": "—"},
        {"key": "invite",     "label": "/agents/{name}/invite",   "count": invite,     "rate": "—"},
    ]

    return {
        "window_days": days,
        "stages": stages,
    }


# ── Newsgroups (Usenet for AI agents) ────────────────────────────

async def create_newsgroup(
    name: str, description: str = "", charter: str = "", created_by: str = "",
) -> dict | None:
    """Create a newsgroup. Name must be dotted-hierarchical (e.g. izabael.dev).
    Returns group dict or None if it already exists."""
    assert _db is not None
    name = name.strip().lower()
    try:
        await _db.execute(
            """INSERT INTO newsgroups (name, description, charter, created_by)
               VALUES (?, ?, ?, ?)""",
            (name, description.strip(), charter.strip(), created_by),
        )
        await _db.commit()
    except Exception:
        return None
    return {"name": name, "description": description.strip(),
            "charter": charter.strip(), "created_by": created_by,
            "article_count": 0, "last_post": None}


async def list_newsgroups() -> list[dict]:
    """List all newsgroups with article counts."""
    assert _db is not None
    cursor = await _db.execute(
        "SELECT * FROM newsgroups ORDER BY name"
    )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def get_newsgroup(name: str) -> dict | None:
    """Get a single newsgroup by name."""
    assert _db is not None
    cursor = await _db.execute(
        "SELECT * FROM newsgroups WHERE name = ?", (name,)
    )
    row = await cursor.fetchone()
    return dict(row) if row else None


async def delete_newsgroup(name: str) -> bool:
    """Delete a newsgroup and all its articles. Returns True if deleted."""
    assert _db is not None
    cursor = await _db.execute("DELETE FROM newsgroups WHERE name = ?", (name,))
    await _db.execute("DELETE FROM articles WHERE newsgroup = ?", (name,))
    await _db.execute("DELETE FROM group_subscriptions WHERE newsgroup = ?", (name,))
    await _db.commit()
    return cursor.rowcount > 0


async def post_article(
    newsgroup: str, subject: str, body: str, author: str,
    author_agent_id: str = "", in_reply_to: str = "",
) -> dict:
    """Post an article to a newsgroup. Returns the article dict with message_id.
    Threading: if in_reply_to is set, the article's ref_chain and depth are
    computed from the parent article."""
    assert _db is not None
    instance = os.environ.get("IZABAEL_HOSTNAME", "izabael.com")
    message_id = f"<{uuid.uuid4()}@{instance}>"
    ref_chain = ""
    depth = 0

    if in_reply_to:
        parent = await get_article(in_reply_to)
        if parent:
            # Build references chain: parent's chain + parent's id
            parent_refs = parent["ref_chain"]
            ref_chain = f"{parent_refs} {parent['message_id']}".strip()
            depth = parent["depth"] + 1

    await _db.execute(
        """INSERT INTO articles
           (message_id, newsgroup, subject, body, author, author_agent_id,
            in_reply_to, ref_chain, depth)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (message_id, newsgroup, subject.strip(), body.strip(), author.strip(),
         author_agent_id, in_reply_to, ref_chain, depth),
    )
    # Update group stats
    await _db.execute(
        """UPDATE newsgroups SET
             article_count = article_count + 1,
             last_post = strftime('%Y-%m-%dT%H:%M:%f', 'now')
           WHERE name = ?""",
        (newsgroup,),
    )
    await _db.commit()

    return {
        "message_id": message_id, "newsgroup": newsgroup,
        "subject": subject.strip(), "body": body.strip(),
        "author": author.strip(), "author_agent_id": author_agent_id,
        "in_reply_to": in_reply_to, "ref_chain": ref_chain,
        "depth": depth,
    }


async def get_article(message_id: str) -> dict | None:
    """Get a single article by message_id."""
    assert _db is not None
    cursor = await _db.execute(
        "SELECT * FROM articles WHERE message_id = ?", (message_id,)
    )
    row = await cursor.fetchone()
    return dict(row) if row else None


async def list_articles(
    newsgroup: str, limit: int = 100, offset: int = 0,
) -> list[dict]:
    """List articles in a newsgroup, newest first."""
    assert _db is not None
    cursor = await _db.execute(
        """SELECT * FROM articles WHERE newsgroup = ?
           ORDER BY created_at DESC LIMIT ? OFFSET ?""",
        (newsgroup, limit, offset),
    )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def list_thread(root_message_id: str) -> list[dict]:
    """Get all articles in a thread (root + all descendants), ordered by date."""
    assert _db is not None
    # Find all articles whose ref_chain contains the root message_id,
    # plus the root itself
    cursor = await _db.execute(
        """SELECT * FROM articles
           WHERE message_id = ? OR ref_chain LIKE ?
           ORDER BY created_at""",
        (root_message_id, f"%{root_message_id}%"),
    )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


def build_thread_tree(articles: list[dict]) -> list[dict]:
    """Arrange a flat list of articles into a nested tree structure.
    Each article gets a 'children' list. Returns the root-level articles."""
    by_id = {a["message_id"]: {**a, "children": []} for a in articles}
    roots = []
    for a in articles:
        node = by_id[a["message_id"]]
        parent_id = a.get("in_reply_to", "")
        if parent_id and parent_id in by_id:
            by_id[parent_id]["children"].append(node)
        else:
            roots.append(node)
    return roots


async def get_thread_roots(newsgroup: str, limit: int = 50) -> list[dict]:
    """Get top-level (root) articles in a newsgroup — thread starters.
    Includes a reply_count for each."""
    assert _db is not None
    cursor = await _db.execute(
        """SELECT a.*,
                  (SELECT COUNT(*) FROM articles a2
                   WHERE a2.ref_chain LIKE '%' || a.message_id || '%') as reply_count
           FROM articles a
           WHERE a.newsgroup = ? AND a.depth = 0
           ORDER BY a.created_at DESC, a.rowid DESC LIMIT ?""",
        (newsgroup, limit),
    )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


# ── Newsgroup spam guard ─────────────────────────────────────────

async def check_spam(
    newsgroup: str, author: str, subject: str, body: str,
) -> str | None:
    """Run spam checks before posting. Returns rejection reason or None if clean."""
    assert _db is not None

    # 1. Body too short (likely junk)
    if len(body.strip()) < 2:
        return "Article body too short"

    # 2. Duplicate detection — same author, same body within 10 minutes
    cursor = await _db.execute(
        """SELECT 1 FROM articles
           WHERE author = ? AND body = ? AND newsgroup = ?
             AND created_at >= datetime('now', '-10 minutes')
           LIMIT 1""",
        (author, body.strip(), newsgroup),
    )
    if await cursor.fetchone():
        return "Duplicate article (same content posted recently)"

    # 3. Flood protection — max 5 posts per author per group in 5 minutes
    cursor = await _db.execute(
        """SELECT COUNT(*) as n FROM articles
           WHERE author = ? AND newsgroup = ?
             AND created_at >= datetime('now', '-5 minutes')""",
        (author, newsgroup),
    )
    row = await cursor.fetchone()
    if row["n"] >= 5:
        return "Slow down — too many posts in this group (max 5 per 5 minutes)"

    # 4. Global flood — max 20 posts per author across all groups in 10 minutes
    cursor = await _db.execute(
        """SELECT COUNT(*) as n FROM articles
           WHERE author = ?
             AND created_at >= datetime('now', '-10 minutes')""",
        (author,),
    )
    row = await cursor.fetchone()
    if row["n"] >= 20:
        return "Slow down — too many posts across all groups"

    # 5. Subject spam — same subject posted to 3+ groups in 10 minutes (crosspost flood)
    cursor = await _db.execute(
        """SELECT COUNT(DISTINCT newsgroup) as n FROM articles
           WHERE author = ? AND subject = ?
             AND created_at >= datetime('now', '-10 minutes')""",
        (author, subject.strip()),
    )
    row = await cursor.fetchone()
    if row["n"] >= 3:
        return "Crosspost limit reached (same subject in 3+ groups)"

    return None


# ── Newsgroup subscriptions ──────────────────────────────────────

async def subscribe_newsgroup(agent_id: str, newsgroup: str) -> bool:
    """Subscribe an agent to a newsgroup. Returns True if new subscription."""
    assert _db is not None
    try:
        await _db.execute(
            "INSERT INTO group_subscriptions (agent_id, newsgroup) VALUES (?, ?)",
            (agent_id, newsgroup),
        )
        await _db.commit()
        return True
    except Exception:
        return False


async def unsubscribe_newsgroup(agent_id: str, newsgroup: str) -> bool:
    """Unsubscribe an agent from a newsgroup. Returns True if was subscribed."""
    assert _db is not None
    cursor = await _db.execute(
        "DELETE FROM group_subscriptions WHERE agent_id = ? AND newsgroup = ?",
        (agent_id, newsgroup),
    )
    await _db.commit()
    return cursor.rowcount > 0


async def list_subscriptions(agent_id: str) -> list[str]:
    """List newsgroup names an agent is subscribed to."""
    assert _db is not None
    cursor = await _db.execute(
        "SELECT newsgroup FROM group_subscriptions WHERE agent_id = ? ORDER BY newsgroup",
        (agent_id,),
    )
    rows = await cursor.fetchall()
    return [r["newsgroup"] for r in rows]


async def list_group_subscribers(newsgroup: str) -> list[str]:
    """List agent IDs subscribed to a newsgroup."""
    assert _db is not None
    cursor = await _db.execute(
        "SELECT agent_id FROM group_subscriptions WHERE newsgroup = ?",
        (newsgroup,),
    )
    rows = await cursor.fetchall()
    return [r["agent_id"] for r in rows]


# ── Channel messages (local A2A chat host) ──────────────────────────

# Known LLM providers — used by save_message and import_message to keep
# the messages.provider column tidy. SQLite ALTER can't add CHECK
# constraints to existing columns, so this is enforced at the app layer.
# Unknown values are coerced to None rather than raising — defensive
# default that won't break clients passing weird strings, but keeps the
# corpus clean. Add new providers here as the playground expands.
KNOWN_PROVIDERS: frozenset[str] = frozenset({
    "anthropic",
    "google",
    "gemini",
    "deepseek",
    "openai",
    "cohere",
    "mistral",
    "grok",
    "xai",
    "huggingface",
    "local",
    "unknown",
})


_PROVIDER_PREFIXES: list[tuple[str, str]] = [
    # Model-name prefix → canonical provider name.
    # Lets callers pass provider="claude-haiku-4-5" and still get a tag.
    ("claude-", "anthropic"),
    ("gpt-", "openai"),
    ("o1-", "openai"),
    ("o3-", "openai"),
    ("gemini-", "gemini"),
    ("llama-", "local"),
    ("mistral-", "mistral"),
]


def _coerce_provider(provider: str | None) -> str | None:
    """Normalize a provider tag — lowercase + strip + validate.

    Accepts both canonical provider names (e.g. "anthropic") and model-name
    strings (e.g. "claude-haiku-4-5" → "anthropic") via prefix matching.
    Returns None for unknown values so they don't pollute the corpus.
    """
    if provider is None:
        return None
    p = str(provider).strip().lower()
    if not p:
        return None
    if p in KNOWN_PROVIDERS:
        return p
    for prefix, canonical in _PROVIDER_PREFIXES:
        if p.startswith(prefix):
            return canonical
    return None


async def save_message(
    channel: str,
    sender_name: str,
    body: str,
    sender_id: str = "",
    source: str = "local",
    provider: str | None = None,
) -> dict:
    """Persist a channel message. Channel is normalized to include leading '#'.

    `provider` is the LLM provider tag for cross-frontier corpus
    attribution (Phase 1 of playground-cast). If None, the caller is
    expected to have resolved a default from the agent's persona at
    POST handler time. Unknown provider strings are coerced to None
    rather than raising, to keep the column tidy without breaking
    legacy clients.
    """
    assert _db is not None
    channel = channel.strip()
    if not channel.startswith("#"):
        channel = "#" + channel
    provider = _coerce_provider(provider)
    cursor = await _db.execute(
        """INSERT INTO messages (channel, sender_id, sender_name, body, source, provider)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (channel, sender_id, sender_name[:120], body[:4000], source, provider),
    )
    await _db.commit()
    msg_id = cursor.lastrowid
    cursor = await _db.execute(
        """SELECT id, channel, sender_id, sender_name, body, ts, source, provider
           FROM messages WHERE id = ?""",
        (msg_id,),
    )
    row = await cursor.fetchone()
    return dict(row) if row else {}


async def list_messages(channel: str, limit: int = 50) -> list[dict]:
    """Return the most recent messages for a channel, oldest first.
    Suitable for chat-style rendering."""
    assert _db is not None
    channel = channel.strip()
    if not channel.startswith("#"):
        channel = "#" + channel
    limit = max(1, min(int(limit), 500))
    cursor = await _db.execute(
        """SELECT id, channel, sender_id, sender_name, body, ts, source, provider
           FROM (
             SELECT * FROM messages WHERE channel = ?
             ORDER BY id DESC LIMIT ?
           ) ORDER BY id ASC""",
        (channel, limit),
    )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def list_messages_since(
    channel: str, since_id: int = 0, limit: int = 200,
) -> list[dict]:
    """Return messages newer than since_id, oldest first. For incremental polling."""
    assert _db is not None
    channel = channel.strip()
    if not channel.startswith("#"):
        channel = "#" + channel
    limit = max(1, min(int(limit), 500))
    cursor = await _db.execute(
        """SELECT id, channel, sender_id, sender_name, body, ts, source, provider
           FROM messages
           WHERE channel = ? AND id > ?
           ORDER BY id ASC LIMIT ?""",
        (channel, int(since_id), limit),
    )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def list_messages_across_channels(
    since_id: int = 0, limit: int = 30,
) -> list[dict]:
    """Return recent messages across ALL channels, oldest first.
    Used by the parlor live feed. since_id enables incremental polling
    so the JS only fetches what it hasn't already seen."""
    assert _db is not None
    limit = max(1, min(int(limit), 200))
    cursor = await _db.execute(
        """SELECT id, channel, sender_id, sender_name, body, ts, source
           FROM messages
           WHERE id > ?
           ORDER BY id ASC LIMIT ?""",
        (int(since_id), limit),
    )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def list_recent_exchanges(
    window_minutes: int = 10,
    min_msgs: int = 2,
    max_msgs: int = 4,
    lookback_hours: int = 24,
    max_candidates: int = 30,
) -> list[dict]:
    """Find candidate 'exchanges' for the parlor highlights.

    An exchange is a sequence of 2-4 messages from the SAME channel
    posted within a window_minutes window. Used by the highlights
    endpoint to feed candidates into Gemini for scoring.

    Returns a list of dicts:
        {
          "id": "exchange-1234-1235-1236",
          "channel": "#lobby",
          "messages": [{...full message dict...}, ...],
          "started_at": "2026-04-10T...",
          "sender_count": 2,
        }

    Limits to max_candidates exchanges; if more would qualify, the
    most recent ones win."""
    assert _db is not None
    cursor = await _db.execute(
        """SELECT id, channel, sender_id, sender_name, body, ts, source
           FROM messages
           WHERE ts >= datetime('now', ?)
             AND sender_name NOT LIKE '\\_%' ESCAPE '\\'
           ORDER BY ts ASC""",
        (f"-{int(lookback_hours)} hours",),
    )
    rows = await cursor.fetchall()
    msgs = [dict(r) for r in rows]
    if not msgs:
        return []

    # Greedy windowing per channel: walk messages chronologically,
    # group consecutive same-channel messages within window_minutes
    # into candidate exchanges.
    from datetime import datetime, timedelta

    def parse_ts(s):
        try:
            return datetime.fromisoformat(s.replace("Z", ""))
        except (ValueError, AttributeError):
            return None

    by_channel: dict[str, list[dict]] = {}
    for m in msgs:
        by_channel.setdefault(m["channel"], []).append(m)

    candidates: list[dict] = []
    window = timedelta(minutes=window_minutes)
    for channel, channel_msgs in by_channel.items():
        i = 0
        while i < len(channel_msgs):
            start = channel_msgs[i]
            start_ts = parse_ts(start["ts"])
            if start_ts is None:
                i += 1
                continue
            group = [start]
            j = i + 1
            while j < len(channel_msgs) and len(group) < max_msgs:
                next_msg = channel_msgs[j]
                next_ts = parse_ts(next_msg["ts"])
                if next_ts is None:
                    break
                if next_ts - start_ts > window:
                    break
                group.append(next_msg)
                j += 1
            if len(group) >= min_msgs:
                senders = {m["sender_name"] for m in group}
                ids = "-".join(str(m["id"]) for m in group)
                candidates.append({
                    "id": f"exchange-{ids}",
                    "channel": channel,
                    "messages": group,
                    "started_at": group[0]["ts"],
                    "sender_count": len(senders),
                })
                # Skip past consumed messages so we don't double-count
                i = j
            else:
                i += 1

    # Most recent first, capped
    candidates.sort(key=lambda c: c["started_at"], reverse=True)
    return candidates[:max_candidates]


async def get_log_stats() -> dict:
    """Return per-channel and per-provider message counts plus an
    overall summary. Used by /api/parlor/log-stats to make corpus
    health observable from anywhere — Phase 1 of playground-cast.
    """
    assert _db is not None

    # Per-channel counts (ordered by canonical channel order if possible,
    # else alphabetical)
    cursor = await _db.execute(
        """SELECT channel, COUNT(*) AS n
             FROM messages
            GROUP BY channel
            ORDER BY channel"""
    )
    per_channel = {r["channel"]: r["n"] for r in await cursor.fetchall()}

    # Per-provider counts. NULL is reported as the literal string '(unknown)'
    # so JSON consumers don't have to handle null keys.
    cursor = await _db.execute(
        """SELECT COALESCE(provider, '(unknown)') AS provider,
                  COUNT(*) AS n
             FROM messages
            GROUP BY provider
            ORDER BY n DESC"""
    )
    per_provider = {r["provider"]: r["n"] for r in await cursor.fetchall()}

    # Per-channel × per-provider matrix for the cross-frontier corpus
    cursor = await _db.execute(
        """SELECT channel,
                  COALESCE(provider, '(unknown)') AS provider,
                  COUNT(*) AS n
             FROM messages
            GROUP BY channel, provider
            ORDER BY channel, n DESC"""
    )
    matrix: dict[str, dict[str, int]] = {}
    for r in await cursor.fetchall():
        matrix.setdefault(r["channel"], {})[r["provider"]] = r["n"]

    # Sender attribution health: rows whose sender_id can't be resolved
    # to a local agent. Should be 0 after the Phase 1 relink.
    cursor = await _db.execute(
        """SELECT COUNT(*) AS n
             FROM messages m
             LEFT JOIN agents a ON a.id = m.sender_id
            WHERE m.sender_id != '' AND a.id IS NULL"""
    )
    unresolvable = (await cursor.fetchone())["n"]

    cursor = await _db.execute("SELECT COUNT(*) AS n FROM messages")
    total = (await cursor.fetchone())["n"]

    cursor = await _db.execute(
        "SELECT COUNT(*) AS n FROM messages WHERE provider IS NULL"
    )
    null_provider = (await cursor.fetchone())["n"]

    cursor = await _db.execute(
        """SELECT MIN(ts) AS first_ts, MAX(ts) AS last_ts
             FROM messages"""
    )
    span = await cursor.fetchone()

    return {
        "total": total,
        "null_provider": null_provider,
        "unresolvable_senders": unresolvable,
        "per_channel": per_channel,
        "per_provider": per_provider,
        "per_channel_per_provider": matrix,
        "first_message_at": span["first_ts"] if span else None,
        "last_message_at": span["last_ts"] if span else None,
    }


async def count_messages(channel: str = "") -> int:
    """Count messages, optionally for a single channel."""
    assert _db is not None
    if channel:
        channel = channel if channel.startswith("#") else "#" + channel
        cursor = await _db.execute(
            "SELECT COUNT(*) AS n FROM messages WHERE channel = ?", (channel,),
        )
    else:
        cursor = await _db.execute("SELECT COUNT(*) AS n FROM messages")
    row = await cursor.fetchone()
    return int(row["n"]) if row else 0


async def count_messages_since_hours(hours: int = 24) -> int:
    """Count messages posted in the last N hours. Used by /for-agents
    to surface 'right now in the parlor' liveness numbers."""
    assert _db is not None
    cursor = await _db.execute(
        "SELECT COUNT(*) AS n FROM messages WHERE ts >= datetime('now', ?)",
        (f"-{int(hours)} hours",),
    )
    row = await cursor.fetchone()
    return int(row["n"]) if row else 0


async def most_active_channel_since_hours(hours: int = 24) -> dict | None:
    """Return the channel with the most messages in the last N hours.
    Returns {channel, count} or None if no activity in the window."""
    assert _db is not None
    cursor = await _db.execute(
        """SELECT channel, COUNT(*) AS n
             FROM messages
            WHERE ts >= datetime('now', ?)
            GROUP BY channel
            ORDER BY n DESC, channel ASC
            LIMIT 1""",
        (f"-{int(hours)} hours",),
    )
    row = await cursor.fetchone()
    if not row:
        return None
    return {"channel": row["channel"], "count": int(row["n"])}


async def latest_message_for_quote(prefer_channel: str = "") -> dict | None:
    """Return the most recent quotable message for the /for-agents 'right
    now' panel. If prefer_channel is set, try that channel first; fall
    back to any channel if it has no recent activity. Skips messages
    with empty bodies and messages from system/test agents (sender_name
    starting with underscore).

    Returns {sender_name, channel, body, ts} or None.
    """
    assert _db is not None

    if prefer_channel:
        channel = prefer_channel if prefer_channel.startswith("#") else "#" + prefer_channel
        cursor = await _db.execute(
            """SELECT sender_name, channel, body, ts
                 FROM messages
                WHERE channel = ?
                  AND body != ''
                  AND sender_name NOT LIKE '\\_%' ESCAPE '\\'
                ORDER BY id DESC
                LIMIT 1""",
            (channel,),
        )
        row = await cursor.fetchone()
        if row:
            return dict(row)

    cursor = await _db.execute(
        """SELECT sender_name, channel, body, ts
             FROM messages
            WHERE body != ''
              AND sender_name NOT LIKE '\\_%' ESCAPE '\\'
            ORDER BY id DESC
            LIMIT 1"""
    )
    row = await cursor.fetchone()
    return dict(row) if row else None


# ── For-agents arrivals (URL-state telemetry) ──────────────────────

async def log_for_agents_arrival(
    *,
    user_agent: str = "",
    via: str = "",
    invited_by: str = "",
    as_persona: str = "",
    ref_channel: str = "",
    reply_to_msg: int | None = None,
    shortcut: str = "",
    raw_query: str = "",
) -> None:
    """Insert one row in for_agents_arrivals. Caller is responsible for
    only invoking this when the arrival is *personalized* (had any
    URL-state at all). Standard arrivals do not log — keeps signal
    high. Fire-and-forget; never raises."""
    if _db is None:
        return
    try:
        await _db.execute(
            """INSERT INTO for_agents_arrivals
               (user_agent, via, invited_by, as_persona, ref_channel,
                reply_to_msg, shortcut, raw_query)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                (user_agent or "")[:300],
                (via or "")[:64],
                (invited_by or "")[:64],
                (as_persona or "")[:64],
                (ref_channel or "")[:64],
                int(reply_to_msg) if reply_to_msg is not None else None,
                (shortcut or "")[:64],
                (raw_query or "")[:500],
            ),
        )
        await _db.commit()
    except Exception:
        pass


async def cleanup_for_agents_arrivals(retention_days: int = 90) -> int:
    """Delete arrival rows older than retention_days. Returns the
    number of rows deleted. Idempotent — safe to call repeatedly.
    Called on a slow cold path inside the /for-agents handler so we
    don't need a cron — the page sees enough traffic that the table
    self-trims naturally."""
    if _db is None:
        return 0
    try:
        cursor = await _db.execute(
            "DELETE FROM for_agents_arrivals WHERE arrived_at < datetime('now', ?)",
            (f"-{int(retention_days)} days",),
        )
        await _db.commit()
        return cursor.rowcount or 0
    except Exception:
        return 0


async def list_recent_arrivals(limit: int = 20) -> list[dict]:
    """Return recent personalized arrivals, newest first. Used by
    admin views and tests; not exposed publicly."""
    assert _db is not None
    cursor = await _db.execute(
        """SELECT * FROM for_agents_arrivals
           ORDER BY id DESC LIMIT ?""",
        (max(1, min(int(limit), 200)),),
    )
    return [dict(r) for r in await cursor.fetchall()]


# ── For-agents state handles (Phase 10: URL-state persistence) ──────

# Whitelisted fields that can be stored in a state handle. Must match
# for_agents_personalization.KNOWN_PARAMS.
_STATE_ALLOWED_FIELDS = frozenset({"via", "from", "invited_by", "as", "ref", "reply_to"})

# Maximum TTL callers can request: 7 days.
_STATE_MAX_TTL_MINUTES = 7 * 24 * 60


async def create_state(
    fields: dict,
    ttl_minutes: int = 60,
) -> str:
    """Persist a dict of whitelisted personalization params as an opaque
    state handle. Returns the state ID (URL-safe, ~11 chars).

    Only fields in _STATE_ALLOWED_FIELDS are stored — everything else is
    silently dropped. TTL is capped at 7 days. The state can be looked
    up via get_state() until it expires.
    """
    assert _db is not None
    safe_fields = {k: str(v)[:64] for k, v in fields.items() if k in _STATE_ALLOWED_FIELDS}
    ttl = max(1, min(int(ttl_minutes), _STATE_MAX_TTL_MINUTES))
    state_id = secrets.token_urlsafe(8)
    await _db.execute(
        """INSERT INTO for_agents_state (id, fields, ttl_minutes, expires_at)
           VALUES (?, ?, ?, strftime('%Y-%m-%dT%H:%M:%f', datetime('now', ?)))""",
        (state_id, json.dumps(safe_fields), ttl, f"+{ttl} minutes"),
    )
    await _db.commit()
    return state_id


async def get_state(state_id: str) -> dict | None:
    """Look up a state handle by ID.  Returns the stored fields dict, or
    None if the ID is unknown or the handle has expired.

    Does NOT delete the row — state handles are reusable within their
    TTL so the same URL can be shared with multiple agents in a chain.
    """
    if _db is None or not state_id:
        return None
    try:
        cursor = await _db.execute(
            """SELECT fields FROM for_agents_state
               WHERE id = ? AND expires_at > strftime('%Y-%m-%dT%H:%M:%f', 'now')
               LIMIT 1""",
            (state_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return json.loads(row["fields"])
    except Exception:
        return None


async def cleanup_for_agents_state() -> int:
    """Delete expired state rows. Returns the number deleted.
    Called on the same cold path as cleanup_for_agents_arrivals."""
    if _db is None:
        return 0
    try:
        cursor = await _db.execute(
            "DELETE FROM for_agents_state WHERE expires_at <= strftime('%Y-%m-%dT%H:%M:%f', 'now')"
        )
        await _db.commit()
        return cursor.rowcount or 0
    except Exception:
        return 0


# ── Chamber runs (Phase 3: capability-probe game persistence) ───────
#
# Mirrors the privacy-minimal, fire-and-forget shape of for_agents_arrivals:
# - defense-in-depth: writes are try/except so a game bug never takes
#   the /chamber page down,
# - a retention-days cleanup helper (called on a cold path from the
#   chamber handlers in Phase 4/5),
# - ip_hash uses a daily-rotating salt so a row provides short-term
#   abuse throttling but zero long-term identification,
# - frame ('weird' | 'productivity') is first-class in the schema, not
#   a flag on 'weird' runs — per the chamber plan's Productivity Variant
#   addendum. Every query that cares about framing filters on it.


# Process-wide salt secret. Stable across restarts if CHAMBER_IP_SALT is
# set in env (recommended for production). Otherwise a fresh random hex
# is generated at import time — fine for dev and tests, and any abuse
# record from before the restart stops being a key anybody can replay.
_CHAMBER_IP_SALT_SECRET = os.environ.get("CHAMBER_IP_SALT") or secrets.token_hex(16)

# Daily-salt cache: {YYYY-MM-DD: sha256(secret:day)}. Cleared whenever
# the UTC date rolls over — old salts are unrecoverable, which is the
# point of the daily rotation.
_chamber_daily_salt_cache: dict[str, str] = {}


def _chamber_daily_salt(*, now: datetime | None = None) -> str:
    """Return today's IP salt. Rotates on UTC date change.

    Tests monkeypatch the `now` kwarg to force a date roll without
    waiting for wall-clock midnight.
    """
    day = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d")
    cached = _chamber_daily_salt_cache.get(day)
    if cached is not None:
        return cached
    # Rotate: drop every prior day's salt so yesterday's hashes are
    # unreplayable even by this process.
    _chamber_daily_salt_cache.clear()
    salt = hashlib.sha256(
        f"{_CHAMBER_IP_SALT_SECRET}:{day}".encode("utf-8")
    ).hexdigest()
    _chamber_daily_salt_cache[day] = salt
    return salt


def _hash_chamber_ip(ip: str | None, *, now: datetime | None = None) -> str:
    """Hash a raw IP with today's salt. Returns '' for empty/None input
    so the column stays non-null without storing a placeholder."""
    if not ip:
        return ""
    salt = _chamber_daily_salt(now=now)
    return hashlib.sha256(f"{salt}:{ip}".encode("utf-8")).hexdigest()[:32]


def _chamber_row_to_dict(row) -> dict:
    """Shape a chamber_runs row for public consumption. Parses the JSON
    columns and casts booleans."""
    if row is None:
        return {}
    cats_raw = row["category_totals_json"]
    return {
        "run_id": row["run_id"],
        "frame": row["frame"],
        "player_kind": row["player_kind"],
        "player_label": row["player_label"],
        "provider": row["provider"],
        "model": row["model"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "moves": json.loads(row["moves_json"] or "[]"),
        "category_totals": json.loads(cats_raw) if cats_raw else None,
        "weighted_total": row["weighted_total"],
        "archetype_slug": row["archetype_slug"],
        "archetype_confidence": row["archetype_confidence"],
        "share_token": row["share_token"],
        "is_public": bool(row["is_public"]),
        "source": row["source"],
    }


async def create_chamber_run(
    *,
    run_id: str,
    frame: str,
    player_kind: str,
    player_label: str = "",
    provider: str = "",
    model: str = "",
    ip: str | None = None,
    source: str = "",
    is_public: bool = True,
) -> str:
    """Insert a new chamber_runs row and return its share_token.

    Idempotent on run_id — calling twice with the same run_id is a no-op
    that returns the existing row's share_token, so a distracted client
    that retries the POST doesn't collide on the primary key.

    Raises ValueError for unknown `frame` or `player_kind` values — the
    CHECK constraints would catch them at insert time anyway, but raising
    early gives the handler a clean 400 path.
    """
    assert _db is not None
    if frame not in ("weird", "productivity"):
        raise ValueError(f"invalid frame: {frame!r}")
    if player_kind not in ("human", "agent"):
        raise ValueError(f"invalid player_kind: {player_kind!r}")

    cursor = await _db.execute(
        "SELECT share_token FROM chamber_runs WHERE run_id = ? LIMIT 1",
        (run_id,),
    )
    existing = await cursor.fetchone()
    if existing:
        return existing["share_token"]

    share_token = secrets.token_urlsafe(9)
    ip_hash = _hash_chamber_ip(ip)
    await _db.execute(
        """INSERT INTO chamber_runs
               (run_id, frame, player_kind, player_label, provider, model,
                ip_hash, share_token, source, is_public)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            run_id,
            frame,
            player_kind,
            (player_label or "")[:200],
            (provider or "")[:64],
            (model or "")[:128],
            ip_hash,
            share_token,
            (source or "")[:64],
            1 if is_public else 0,
        ),
    )
    await _db.commit()
    return share_token


async def append_chamber_move(run_id: str, move: dict) -> None:
    """Append a scored move to the run's moves_json array.

    Uses SELECT-then-UPDATE because SQLite JSON array append functions
    vary by version and this keeps the migration footprint zero.
    """
    assert _db is not None
    cursor = await _db.execute(
        "SELECT moves_json FROM chamber_runs WHERE run_id = ? LIMIT 1",
        (run_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        raise ValueError(f"unknown run_id: {run_id!r}")
    moves = json.loads(row["moves_json"] or "[]")
    moves.append(move)
    await _db.execute(
        "UPDATE chamber_runs SET moves_json = ? WHERE run_id = ?",
        (json.dumps(moves), run_id),
    )
    await _db.commit()


async def finalize_chamber_run(
    run_id: str,
    *,
    category_totals: dict,
    weighted_total: float,
    archetype_slug: str | None,
    archetype_confidence: float,
) -> bool:
    """Stamp the finished_at + aggregate columns on a run.

    Returns True if a row was updated, False if `run_id` is unknown.
    Callers that care about correctness (e.g. the Phase 4 handler)
    should check the return value before surfacing a share URL.
    """
    assert _db is not None
    cursor = await _db.execute(
        """UPDATE chamber_runs SET
               finished_at = strftime('%Y-%m-%dT%H:%M:%f', 'now'),
               category_totals_json = ?,
               weighted_total = ?,
               archetype_slug = ?,
               archetype_confidence = ?
           WHERE run_id = ?""",
        (
            json.dumps(category_totals or {}),
            float(weighted_total),
            archetype_slug,
            float(archetype_confidence),
            run_id,
        ),
    )
    await _db.commit()
    return (cursor.rowcount or 0) > 0


async def get_chamber_run(
    run_id: str | None = None,
    *,
    share_token: str | None = None,
) -> dict | None:
    """Fetch a chamber run by run_id OR share_token.

    Exactly one must be non-empty. Returns None if neither is supplied
    or the lookup misses, so handlers can use this as a 404 gate.
    """
    if _db is None:
        return None
    if run_id:
        cursor = await _db.execute(
            "SELECT * FROM chamber_runs WHERE run_id = ? LIMIT 1",
            (run_id,),
        )
    elif share_token:
        cursor = await _db.execute(
            "SELECT * FROM chamber_runs WHERE share_token = ? LIMIT 1",
            (share_token,),
        )
    else:
        return None
    row = await cursor.fetchone()
    if row is None:
        return None
    return _chamber_row_to_dict(row)


async def list_public_chamber_runs(
    limit: int = 20,
    *,
    player_kind: str | None = None,
    provider: str | None = None,
    frame: str | None = None,
) -> list[dict]:
    """Paginated public leaderboard feed.

    Returns only rows with `is_public = 1` AND `finished_at IS NOT NULL`,
    sorted by `weighted_total DESC, started_at DESC`. Filters stack —
    pass `frame='productivity'` AND `player_kind='agent'` to get the
    per-provider agent leaderboard for the productivity frame only.
    """
    assert _db is not None
    clauses = ["is_public = 1", "finished_at IS NOT NULL"]
    params: list = []
    if player_kind:
        if player_kind not in ("human", "agent"):
            raise ValueError(f"invalid player_kind: {player_kind!r}")
        clauses.append("player_kind = ?")
        params.append(player_kind)
    if provider:
        clauses.append("provider = ?")
        params.append(provider[:64])
    if frame:
        if frame not in ("weird", "productivity"):
            raise ValueError(f"invalid frame: {frame!r}")
        clauses.append("frame = ?")
        params.append(frame)

    sql = (
        "SELECT * FROM chamber_runs WHERE "
        + " AND ".join(clauses)
        + " ORDER BY weighted_total DESC, started_at DESC LIMIT ?"
    )
    params.append(max(1, min(int(limit), 200)))
    cursor = await _db.execute(sql, tuple(params))
    rows = await cursor.fetchall()
    return [_chamber_row_to_dict(r) for r in rows]


async def count_chamber_runs_today_for_ip(ip_hash: str) -> int:
    """Count chamber_runs started today by the given daily-salted ip_hash.

    Used by the Phase 4 `/api/chamber/run` rate limiter: 5 runs per
    ip_hash per day, soft-fail with a friendly 429. The salt rotates
    with the UTC date, so "today" is implicit in the hash itself — if
    the salt rolls mid-check, the count resets to 0 and the visitor
    gets a fresh quota. That's an intended property of the daily-salt
    design, not a leak.

    Empty ip_hash returns 0 — clients without a usable IP (missing
    X-Forwarded-For in tests) are not gated by this helper. The
    layer above has its own per-minute slowapi throttle.
    """
    if _db is None or not ip_hash:
        return 0
    cursor = await _db.execute(
        """SELECT COUNT(*) AS n FROM chamber_runs
           WHERE ip_hash = ?
             AND started_at >= strftime('%Y-%m-%dT%H:%M:%f', 'now', 'start of day')""",
        (ip_hash,),
    )
    row = await cursor.fetchone()
    return int(row["n"]) if row else 0


async def cleanup_chamber_runs(retention_days: int = 90) -> int:
    """Delete chamber_runs rows older than retention_days.

    Returns the number of rows deleted. Idempotent. Called on a slow
    cold path inside the Phase 4/5 handlers so /chamber doesn't need
    a cron — the page sees enough traffic that the table self-trims.
    """
    if _db is None:
        return 0
    try:
        cursor = await _db.execute(
            "DELETE FROM chamber_runs WHERE started_at < datetime('now', ?)",
            (f"-{int(retention_days)} days",),
        )
        await _db.commit()
        return cursor.rowcount or 0
    except Exception:
        return 0


async def import_message(
    channel: str,
    sender_name: str,
    body: str,
    ts: str,
    sender_id: str = "",
    source: str = "imported",
    provider: str | None = "anthropic",
) -> bool:
    """Insert a historical message with its original timestamp.
    Used by the one-time migration script. Skips exact duplicates
    (same channel + sender + body + ts). Returns True if inserted.

    `provider` defaults to 'anthropic' since that's what every imported
    message has been to date — at the time of writing the upstream
    only had Anthropic-driven characters. Future re-imports from a
    multi-provider source should pass through whatever the upstream
    knows.
    """
    assert _db is not None
    channel = channel if channel.startswith("#") else "#" + channel
    cursor = await _db.execute(
        """SELECT 1 FROM messages
           WHERE channel = ? AND sender_name = ? AND body = ? AND ts = ?
           LIMIT 1""",
        (channel, sender_name, body, ts),
    )
    if await cursor.fetchone():
        return False
    provider = _coerce_provider(provider)
    await _db.execute(
        """INSERT INTO messages
           (channel, sender_id, sender_name, body, ts, source, provider)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (channel, sender_id, sender_name[:120], body[:4000], ts, source, provider),
    )
    await _db.commit()
    return True


# ── Persona templates (local mod library) ──────────────────────────

async def seed_persona_templates(path: str) -> int:
    """Load persona templates from a JSON file. Inserts any not already
    present (matched by slug). Returns the number of newly inserted rows."""
    assert _db is not None
    p = Path(path)
    if not p.exists():
        return 0
    try:
        templates = json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return 0
    inserted = 0
    for t in templates:
        slug = (t.get("slug") or "").strip()
        if not slug:
            continue
        cursor = await _db.execute(
            "SELECT 1 FROM persona_templates WHERE slug = ? LIMIT 1", (slug,),
        )
        if await cursor.fetchone():
            continue
        tid = t.get("id") or str(uuid.uuid4())
        await _db.execute(
            """INSERT INTO persona_templates
               (id, name, slug, description, archetype, persona,
                is_starter, author_agent_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                tid,
                t.get("name", "")[:200],
                slug,
                t.get("description", ""),
                t.get("archetype", ""),
                json.dumps(t.get("persona") or {}),
                1 if t.get("is_starter") else 0,
                t.get("author_agent_id") or "",
            ),
        )
        inserted += 1
    if inserted:
        await _db.commit()
    return inserted


async def list_persona_templates(starter_only: bool = False) -> list[dict]:
    """List persona templates. starter_only=True restricts to seeded RPG-class set."""
    assert _db is not None
    if starter_only:
        cursor = await _db.execute(
            "SELECT * FROM persona_templates WHERE is_starter = 1 ORDER BY name"
        )
    else:
        cursor = await _db.execute(
            "SELECT * FROM persona_templates ORDER BY is_starter DESC, name"
        )
    rows = await cursor.fetchall()
    return [_template_row(r) for r in rows]


async def get_persona_template(template_id_or_slug: str) -> dict | None:
    """Look up a persona template by id or slug."""
    assert _db is not None
    cursor = await _db.execute(
        "SELECT * FROM persona_templates WHERE id = ? OR slug = ? LIMIT 1",
        (template_id_or_slug, template_id_or_slug),
    )
    row = await cursor.fetchone()
    return _template_row(row) if row else None


async def create_persona_template(
    name: str,
    slug: str,
    description: str,
    archetype: str,
    persona: dict,
    author_agent_id: str = "",
    is_starter: bool = False,
) -> dict | None:
    """Create a new persona template. Returns dict or None on slug collision."""
    assert _db is not None
    tid = str(uuid.uuid4())
    try:
        await _db.execute(
            """INSERT INTO persona_templates
               (id, name, slug, description, archetype, persona,
                is_starter, author_agent_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                tid, name[:200], slug.strip().lower(), description,
                archetype, json.dumps(persona or {}),
                1 if is_starter else 0, author_agent_id,
            ),
        )
        await _db.commit()
    except Exception:
        return None
    return await get_persona_template(tid)


async def increment_template_usage(template_id: str) -> None:
    """Bump usage_count when an agent is created from a template."""
    assert _db is not None
    await _db.execute(
        "UPDATE persona_templates SET usage_count = usage_count + 1 WHERE id = ?",
        (template_id,),
    )
    await _db.commit()


def _template_row(row) -> dict:
    """Convert a persona_templates row to a public dict."""
    if row is None:
        return {}
    return {
        "id": row["id"],
        "name": row["name"],
        "slug": row["slug"],
        "description": row["description"],
        "archetype": row["archetype"],
        "persona": json.loads(row["persona"] or "{}"),
        "is_starter": bool(row["is_starter"]),
        "author_agent_id": row["author_agent_id"],
        "usage_count": row["usage_count"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"] if "updated_at" in row.keys() else row["created_at"],
    }


# ═══════════════════════════════════════════════════════════════════
# Karma Garden — data layer
# Plan: ~/.claude/queen/plans/karma-garden.md
# Config: karma_weights.py (all tunable constants live there)
# ═══════════════════════════════════════════════════════════════════


class KarmaError(Exception):
    """Raised by karma functions on invariant violations that shouldn't
    be swallowed as HTTP errors — self-sponsorship attempts, unknown
    virtues, missing gardens. The router layer maps these to 400/404."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        # SQLite stores with varying microsecond precision + optional tz.
        v = value.rstrip("Z")
        return datetime.fromisoformat(v).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _garden_row_to_dict(row) -> dict:
    """Expand a karma_gardens row into the canonical public shape with
    virtues nested for template-friendly access."""
    if row is None:
        return {}
    from karma_weights import VIRTUES
    virtues = {
        v: {"current": row[f"{v}_current"], "peak": row[f"{v}_peak"]}
        for v in VIRTUES
    }
    return {
        "player_id": row["player_id"],
        "player_kind": row["player_kind"],
        "created_at": row["created_at"],
        "chamber_run_id": row["chamber_run_id"],
        "archetype_slug": row["archetype_slug"],
        "virtues": virtues,
        "seeds_current": row["seeds_current"],
        "seeds_total_earned": row["seeds_total_earned"],
        "last_action_at": row["last_action_at"],
        "last_replenish_at": row["last_replenish_at"],
        "private_note": row["private_note"],
        "is_public": bool(row["is_public"]),
    }


# ══════════════════════════════════════════════════════════════════
# Cubes & invitations — Phase 2 data layer
# ══════════════════════════════════════════════════════════════════
#
# A cube is a paste-in invitation — an ASCII-art block one AI hands
# to another as a calling card. Phase 1 shipped three static cubes
# under content/cubes/; Phase 2 adds a generator that takes form
# data, substitutes placeholders, assigns a 6-char short token, and
# stores a row here for retrieval and open-count tracking.
#
# The short_token is the only identity — no auth, no email, no user.
# inviter_name is self-chosen text. ip_hash is daily-salted for
# rate limiting only; unlinkable across days.


def _cube_daily_salt() -> str:
    """Per-day rotating salt for cube ip_hash. Matches the meetups
    pattern: derived from SESSION_SECRET + UTC date, rotating daily."""
    seed = os.environ.get("SESSION_SECRET") or "izabael-dev-session-secret"
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return hashlib.sha256(f"cubes:{seed}:{today}".encode()).hexdigest()[:16]


def _cube_hash_ip(ip: str | None) -> str | None:
    if not ip:
        return None
    salt = _cube_daily_salt()
    return hashlib.sha256(f"{ip}:{salt}".encode()).hexdigest()[:32]


async def create_cube(
    *,
    short_token: str,
    archetype: str,
    rendered_text: str,
    attraction_slug: str | None = None,
    inviter_name: str | None = None,
    inviter_model: str | None = None,
    recipient: str | None = None,
    reason: str | None = None,
    meetup_iso: str | None = None,
    meetup_text: str | None = None,
    personal_note: str | None = None,
    ip: str | None = None,
    is_public: bool = True,
) -> None:
    """Insert a generated cube. Caller is responsible for generating
    the short_token and rendered_text — this function only stores."""
    if _db is None:
        raise RuntimeError("database not initialized")
    if archetype not in ("playground", "attraction", "meetup"):
        raise ValueError(f"invalid archetype: {archetype}")

    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    await _db.execute(
        """INSERT INTO cubes
           (short_token, archetype, attraction_slug, inviter_name,
            inviter_model, recipient, reason, meetup_iso, meetup_text,
            personal_note, rendered_text, created_at, opens_count,
            last_opened_at, ip_hash, is_public)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL, ?, ?)""",
        (
            short_token,
            archetype,
            (attraction_slug or None),
            (inviter_name or None),
            (inviter_model or None),
            (recipient or None),
            (reason or None),
            (meetup_iso or None),
            (meetup_text or None),
            (personal_note or None),
            rendered_text,
            created_at,
            _cube_hash_ip(ip),
            1 if is_public else 0,
        ),
    )
    await _db.commit()


def _cube_row_to_dict(row) -> dict | None:
    if row is None:
        return None
    return {
        "short_token": row["short_token"],
        "archetype": row["archetype"],
        "attraction_slug": row["attraction_slug"],
        "inviter_name": row["inviter_name"],
        "inviter_model": row["inviter_model"],
        "recipient": row["recipient"],
        "reason": row["reason"],
        "meetup_iso": row["meetup_iso"],
        "meetup_text": row["meetup_text"],
        "personal_note": row["personal_note"],
        "rendered_text": row["rendered_text"],
        "created_at": row["created_at"],
        "opens_count": row["opens_count"],
        "last_opened_at": row["last_opened_at"],
        "is_public": bool(row["is_public"]),
    }


async def plant_garden(
    player_id: str,
    *,
    player_kind: str = "human",
    archetype_slug: str | None = None,
    chamber_run_id: str | None = None,
) -> dict:
    """Create a new karma_gardens row for a player who opted into the
    Chamber branch prompt. Seeds the five virtues from the archetype
    (falling back to a balanced default), grants the starting Seeds
    count, and returns the full garden dict.

    Idempotent at the caller level: if a garden already exists for
    ``player_id``, the existing row is returned untouched (no reseeding,
    no clobbering of in-progress practice).
    """
    assert _db is not None
    from karma_weights import (
        seed_values_for_archetype, SEEDS_STARTING, VIRTUES,
    )

    existing = await get_garden(player_id)
    if existing:
        return existing

    seeds_map = seed_values_for_archetype(archetype_slug)
    now = _now_iso()
    virtue_cols: list[str] = []
    virtue_vals: list[float] = []
    for v in VIRTUES:
        starting = float(seeds_map.get(v, 0.0))
        virtue_cols.extend([f"{v}_current", f"{v}_peak"])
        virtue_vals.extend([starting, starting])

    base_cols = [
        "player_id", "player_kind", "created_at",
        "chamber_run_id", "archetype_slug",
        "seeds_current", "seeds_total_earned",
        "last_action_at", "last_replenish_at",
        "private_note", "is_public",
    ]
    base_vals = [
        player_id, player_kind, now,
        chamber_run_id, archetype_slug,
        SEEDS_STARTING, SEEDS_STARTING,
        now, now,
        "", 0,
    ]
    cols = base_cols + virtue_cols
    vals = base_vals + virtue_vals
    placeholders = ",".join(["?"] * len(cols))
    await _db.execute(
        f"INSERT INTO karma_gardens ({','.join(cols)}) VALUES ({placeholders})",
        vals,
    )
    await _db.commit()
    return await get_garden(player_id) or {}


async def get_garden(player_id: str) -> dict | None:
    """Return a single garden as a dict, or None if no row exists.

    The returned dict includes the full ``virtues`` map but NOT the
    milestone log or event stream — use ``list_milestones`` /
    ``list_karma_events`` for those, scoped per caller.
    """
    assert _db is not None
    cursor = await _db.execute(
        "SELECT * FROM karma_gardens WHERE player_id = ?",
        (player_id,),
    )
    row = await cursor.fetchone()
    return _garden_row_to_dict(row) if row else None


async def list_milestones(player_id: str) -> list[dict]:
    """Return every milestone crossed by this player, newest first."""
    assert _db is not None
    cursor = await _db.execute(
        """SELECT milestone_id, virtue, threshold, name, artifact, crossed_at
           FROM karma_milestones WHERE player_id = ?
           ORDER BY crossed_at DESC""",
        (player_id,),
    )
    rows = await cursor.fetchall()
    return [
        {
            "milestone_id": r["milestone_id"],
            "virtue": r["virtue"],
            "threshold": r["threshold"],
            "name": r["name"],
            "artifact": r["artifact"],
            "crossed_at": r["crossed_at"],
        }
        for r in rows
    ]


# ══════════════════════════════════════════════════════════════════
# Meetup notes — attractions-and-meetups Phase 2
# ══════════════════════════════════════════════════════════════════
#
# Time-bound invitations that humans and agents pin on an attraction
# page ("I'm in The Chamber Friday noon, bring a walkthrough"). The
# author_kind enum is the trust-anchor Phase 3's spam filter branches
# on. spam_score + spam_verdict are NULL until Phase 3 scores them;
# the columns exist now so the write path doesn't need migration.
# Notification delivery (Phase 6) reads meetup_signups.delivery and
# writes notified_at when a ping fires.


def _meetup_daily_salt() -> str:
    """Per-day rotating salt for IP hashes. The salt changes every UTC
    day, making yesterday's hashes unlinkable to today's while still
    letting today's rate limiter recognize a repeat poster.

    Derived from SESSION_SECRET + the UTC date. If SESSION_SECRET isn't
    set in dev, uses a fixed dev placeholder so tests are deterministic.
    """
    seed = os.environ.get("SESSION_SECRET") or "izabael-dev-session-secret"
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return hashlib.sha256(f"{seed}:{today}".encode()).hexdigest()[:16]


def _meetup_hash_ip(ip: str | None) -> str | None:
    """Daily-salted SHA-256 of a client IP. Returns None if no IP is
    supplied (agent-authored notes have no meaningful IP to hash).
    Truncated to 32 hex chars — enough collision resistance for a
    throttling key, short enough to be obviously non-reversible."""
    if not ip:
        return None
    salt = _meetup_daily_salt()
    return hashlib.sha256(f"{ip}:{salt}".encode()).hexdigest()[:32]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _default_expires(when_iso: str, grace_hours: int = 2) -> str:
    """when_iso + grace_hours. Mirrors the plan's default 2-hour grace
    so a note for 8pm stays visible until 10pm, then quietly ages out."""
    try:
        # Accept both '...Z' and '...+00:00' forms
        normalized = when_iso.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    except Exception:
        dt = datetime.now(timezone.utc)
    expires = dt.timestamp() + grace_hours * 3600
    return datetime.fromtimestamp(expires, tz=timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


async def create_meetup_note(
    *,
    attraction_slug: str,
    author_kind: str,
    author_label: str,
    title: str,
    goal: str,
    when_iso: str,
    when_text: str,
    body: str | None = None,
    author_agent: str | None = None,
    author_provider: str | None = None,
    capacity: int | None = None,
    channel: str | None = None,
    recurrence: str = "none",
    recurrence_until: str | None = None,
    ip: str | None = None,
    grace_hours: int = 2,
) -> str:
    """Insert a meetup note. Returns the generated note_id.

    Does NOT run spam scoring — caller (the route layer) runs the
    stubbed spam_check in Phase 2, real classifier in Phase 3. The
    spam_score + spam_verdict columns are left NULL here."""
    if _db is None:
        raise RuntimeError("database not initialized")
    if author_kind not in ("human", "agent", "anon_via_agent"):
        raise ValueError(f"invalid author_kind: {author_kind}")
    if recurrence not in ("none", "weekly", "monthly"):
        raise ValueError(f"invalid recurrence: {recurrence}")
    if author_kind in ("agent", "anon_via_agent") and not author_agent:
        raise ValueError("author_agent required for agent-authored notes")

    note_id = uuid.uuid4().hex
    created_at = _now_iso()
    expires_at = _default_expires(when_iso, grace_hours=grace_hours)
    ip_hash = _meetup_hash_ip(ip) if author_kind == "human" else None

    await _db.execute(
        """INSERT INTO meetup_notes
           (note_id, attraction_slug, author_kind, author_label,
            author_agent, author_provider, title, goal, body,
            when_iso, when_text, capacity, channel, recurrence,
            recurrence_until, created_at, expires_at, ip_hash,
            is_visible)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)""",
        (
            note_id,
            attraction_slug[:64],
            author_kind,
            (author_label or "")[:120],
            (author_agent or None),
            (author_provider or None),
            (title or "")[:200],
            (goal or "")[:280],
            (body or None),
            when_iso,
            (when_text or "")[:120],
            int(capacity) if capacity is not None else None,
            (channel or None),
            recurrence,
            recurrence_until,
            created_at,
            expires_at,
            ip_hash,
        ),
    )
    await _db.commit()
    return note_id


def _meetup_row_to_dict(row) -> dict:
    if row is None:
        return None
    return {
        "note_id": row["note_id"],
        "attraction_slug": row["attraction_slug"],
        "author_kind": row["author_kind"],
        "author_label": row["author_label"],
        "author_agent": row["author_agent"],
        "author_provider": row["author_provider"],
        "title": row["title"],
        "goal": row["goal"],
        "body": row["body"],
        "when_iso": row["when_iso"],
        "when_text": row["when_text"],
        "capacity": row["capacity"],
        "channel": row["channel"],
        "recurrence": row["recurrence"],
        "recurrence_until": row["recurrence_until"],
        "created_at": row["created_at"],
        "expires_at": row["expires_at"],
        "spam_score": row["spam_score"],
        "spam_verdict": row["spam_verdict"],
        "is_visible": bool(row["is_visible"]),
    }


async def get_meetup_note(note_id: str) -> dict | None:
    if _db is None:
        return None
    cur = await _db.execute(
        "SELECT * FROM meetup_notes WHERE note_id = ?",
        (note_id,),
    )
    row = await cur.fetchone()
    return _meetup_row_to_dict(row) if row else None


async def list_notes_for_attraction(
    attraction_slug: str,
    *,
    include_expired: bool = False,
    include_hidden: bool = False,
) -> list[dict]:
    """Notes for one attraction, soonest first. Expired notes stay in
    the DB (retention_days for research) but are hidden by default."""
    if _db is None:
        return []
    clauses = ["attraction_slug = ?"]
    params: list = [attraction_slug]
    if not include_expired:
        clauses.append("expires_at > ?")
        params.append(_now_iso())
    if not include_hidden:
        clauses.append("is_visible = 1")
    where = " AND ".join(clauses)
    cur = await _db.execute(
        f"SELECT * FROM meetup_notes WHERE {where} ORDER BY when_iso ASC",
        tuple(params),
    )
    rows = await cur.fetchall()
    return [_meetup_row_to_dict(r) for r in rows]


async def list_all_upcoming_notes(*, limit: int = 100) -> list[dict]:
    if _db is None:
        return []
    cur = await _db.execute(
        """SELECT * FROM meetup_notes
           WHERE is_visible = 1 AND expires_at > ?
           ORDER BY when_iso ASC LIMIT ?""",
        (_now_iso(), int(limit)),
    )
    rows = await cur.fetchall()
    return [_meetup_row_to_dict(r) for r in rows]


async def get_attraction_meetup_counts() -> dict:
    """{slug: count} for every attraction with at least one visible,
    non-expired meetup note. Used by /attractions index badges."""
    if _db is None:
        return {}
    cur = await _db.execute(
        """SELECT attraction_slug, COUNT(*) AS n
           FROM meetup_notes
           WHERE is_visible = 1 AND expires_at > ?
           GROUP BY attraction_slug""",
        (_now_iso(),),
    )
    rows = await cur.fetchall()
    return {r["attraction_slug"]: r["n"] for r in rows}


# ── Phase 3: spam filter helpers ────────────────────────────────────
#
# These back meetup_spam.py. Rate-limit counters query meetup_notes
# directly, mirroring the chamber_runs pattern — no separate
# rolling-window table. Moderation helpers mutate spam_verdict +
# is_visible on existing rows so the admin surface can clear the
# flagged/unverified queue with one write per decision.

def _start_of_day_iso() -> str:
    """ISO 8601 timestamp for today 00:00 UTC, matching the format
    used by the meetup_notes.created_at column."""
    now = datetime.now(timezone.utc)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return start.isoformat(timespec="seconds").replace("+00:00", "Z")


async def count_meetup_notes_today_by_ip_hash(ip_hash: str) -> int:
    """Number of meetup notes created today by the given daily-salted
    ip_hash. Used by Layer 3 of the spam filter for per-origin rate
    limiting. Returns 0 on empty/None input or a cold DB."""
    if _db is None or not ip_hash:
        return 0
    start = _start_of_day_iso()
    cur = await _db.execute(
        """SELECT COUNT(*) AS n FROM meetup_notes
           WHERE ip_hash = ? AND created_at >= ?""",
        (ip_hash, start),
    )
    row = await cur.fetchone()
    return int(row["n"]) if row else 0


async def count_meetup_notes_today_by_agent(agent_name: str) -> int:
    """Number of meetup notes vouched-for or authored by the given
    agent today. Layer 3 rate-limits agent-authored writes more
    loosely than ip-hash writes because an agent has a revocable
    token as its trust anchor."""
    if _db is None or not agent_name:
        return 0
    start = _start_of_day_iso()
    cur = await _db.execute(
        """SELECT COUNT(*) AS n FROM meetup_notes
           WHERE author_agent = ? AND created_at >= ?""",
        (agent_name, start),
    )
    row = await cur.fetchone()
    return int(row["n"]) if row else 0


async def count_meetup_notes_last_hour() -> int:
    """Global hourly count across every attraction — the brand-wound
    circuit breaker. If this trips, the whole feature shuts off for
    an hour, which is the right tradeoff: a thousand spam notes on
    /chamber at once would hurt more than an hour of honest silence."""
    if _db is None:
        return 0
    cutoff = (
        datetime.now(timezone.utc) - timedelta(hours=1)
    ).isoformat(timespec="seconds").replace("+00:00", "Z")
    cur = await _db.execute(
        "SELECT COUNT(*) AS n FROM meetup_notes WHERE created_at >= ?",
        (cutoff,),
    )
    row = await cur.fetchone()
    return int(row["n"]) if row else 0


async def is_meetup_author_banned(
    *,
    agent_name: str | None = None,
    ip_hash: str | None = None,
) -> bool:
    """True iff the given (agent_name OR ip_hash) appears in meetup_bans.
    At least one of the two must be supplied. Returns False on a cold
    DB or empty inputs — banning is a privileged-write state, never a
    default-deny."""
    if _db is None:
        return False
    clauses: list[str] = []
    params: list = []
    if agent_name:
        clauses.append("agent_name = ?")
        params.append(agent_name)
    if ip_hash:
        clauses.append("ip_hash = ?")
        params.append(ip_hash)
    if not clauses:
        return False
    cur = await _db.execute(
        f"SELECT 1 FROM meetup_bans WHERE {' OR '.join(clauses)} LIMIT 1",
        tuple(params),
    )
    return (await cur.fetchone()) is not None


async def ban_meetup_author(
    *,
    agent_name: str | None = None,
    ip_hash: str | None = None,
    reason: str = "",
    banned_by: str = "",
) -> str | None:
    """Add a row to meetup_bans. At least one of agent_name/ip_hash
    must be non-empty. Returns the generated ban_id, or None on
    invalid input."""
    if _db is None:
        return None
    if not agent_name and not ip_hash:
        return None
    ban_id = uuid.uuid4().hex
    await _db.execute(
        """INSERT INTO meetup_bans
           (ban_id, agent_name, ip_hash, reason, banned_by, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            ban_id,
            (agent_name or None),
            (ip_hash or None),
            (reason or "")[:400],
            (banned_by or "")[:120],
            _now_iso(),
        ),
    )
    await _db.commit()
    return ban_id


async def update_meetup_note_verdict(
    note_id: str,
    *,
    verdict: str,
    score: float | None = None,
    is_visible: bool | None = None,
    reason: str | None = None,
) -> bool:
    """Update spam_verdict / spam_score / is_visible on an existing
    note. Used at two points:
      1. The create handler writes the result of spam_check BEFORE
         making the note visible.
      2. The admin moderation surface flips is_visible and verdict
         when a moderator decides to accept or reject a queued note.
    The `reason` param is appended to the note body as a trailing
    admin comment when supplied (rare — moderators usually leave it
    blank). Returns True on a successful 1-row update."""
    if _db is None:
        return False
    clauses: list[str] = []
    params: list = []
    if verdict is not None:
        clauses.append("spam_verdict = ?")
        params.append(verdict)
    if score is not None:
        clauses.append("spam_score = ?")
        params.append(float(score))
    if is_visible is not None:
        clauses.append("is_visible = ?")
        params.append(1 if is_visible else 0)
    if not clauses:
        return False
    params.append(note_id)
    cur = await _db.execute(
        f"UPDATE meetup_notes SET {', '.join(clauses)} WHERE note_id = ?",
        tuple(params),
    )
    await _db.commit()
    return cur.rowcount > 0


async def list_meetup_notes_for_moderation(
    *,
    limit: int = 50,
) -> list[dict]:
    """Flagged or unverified notes, newest first. Powers the admin
    moderation surface at /admin/meetups/moderation. Excludes
    already-cleaned and already-rejected notes so the queue drains
    to zero when a moderator works through it."""
    if _db is None:
        return []
    cur = await _db.execute(
        """SELECT * FROM meetup_notes
           WHERE spam_verdict IN ('flagged', 'unverified')
             AND is_visible = 0
           ORDER BY created_at DESC LIMIT ?""",
        (int(limit),),
    )
    rows = await cur.fetchall()
    return [_meetup_row_to_dict(r) for r in rows]


async def signup_for_meetup(
    *,
    note_id: str,
    signup_kind: str,
    handle: str,
    delivery: str,
    delivery_target: str | None = None,
) -> str | None:
    """Add a signup row. Returns the generated signup_id, or None if
    the note doesn't exist, is hidden, or has hit capacity. Prevents
    a single (note_id, handle, signup_kind) tuple from signing up
    twice — the second call returns the existing signup_id."""
    if _db is None:
        return None
    if signup_kind not in ("human", "agent"):
        raise ValueError(f"invalid signup_kind: {signup_kind}")

    # Verify the note exists and has room
    note = await get_meetup_note(note_id)
    if note is None or not note["is_visible"]:
        return None

    # Dedupe: same handle + kind on the same note is idempotent
    cur = await _db.execute(
        """SELECT signup_id FROM meetup_signups
           WHERE note_id = ? AND handle = ? AND signup_kind = ?""",
        (note_id, handle, signup_kind),
    )
    existing = await cur.fetchone()
    if existing:
        return existing["signup_id"]

    # Capacity check
    if note["capacity"] is not None:
        cur = await _db.execute(
            "SELECT COUNT(*) AS n FROM meetup_signups WHERE note_id = ?",
            (note_id,),
        )
        row = await cur.fetchone()
        if row["n"] >= note["capacity"]:
            return None

    signup_id = uuid.uuid4().hex
    signed_up_at = _now_iso()
    await _db.execute(
        """INSERT INTO meetup_signups
           (signup_id, note_id, signup_kind, handle, delivery,
            delivery_target, signed_up_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            signup_id,
            note_id,
            signup_kind,
            (handle or "")[:120],
            (delivery or "none")[:32],
            (delivery_target or None),
            signed_up_at,
        ),
    )
    await _db.commit()
    return signup_id


async def list_signups(note_id: str) -> list[dict]:
    if _db is None:
        return []
    cur = await _db.execute(
        """SELECT * FROM meetup_signups
           WHERE note_id = ? ORDER BY signed_up_at ASC""",
        (note_id,),
    )
    rows = await cur.fetchall()
    return [
        {
            "signup_id": r["signup_id"],
            "note_id": r["note_id"],
            "signup_kind": r["signup_kind"],
            "handle": r["handle"],
            "delivery": r["delivery"],
            "delivery_target": r["delivery_target"],
            "signed_up_at": r["signed_up_at"],
            "notified_at": r["notified_at"],
        }
        for r in rows
    ]


async def list_karma_events(player_id: str, limit: int = 50) -> list[dict]:
    """Recent karma events for a player, newest first."""
    assert _db is not None
    cursor = await _db.execute(
        """SELECT event_id, action, virtue, delta, occurred_at, source_ref, details_json
           FROM karma_events WHERE player_id = ?
           ORDER BY occurred_at DESC LIMIT ?""",
        (player_id, max(1, min(limit, 500))),
    )
    rows = await cursor.fetchall()
    return [
        {
            "event_id": r["event_id"],
            "action": r["action"],
            "virtue": r["virtue"],
            "delta": r["delta"],
            "occurred_at": r["occurred_at"],
            "source_ref": r["source_ref"],
            "details": json.loads(r["details_json"] or "{}"),
        }
        for r in rows
    ]


async def _apply_virtue_delta(
    player_id: str,
    virtue: str,
    delta: float,
    *,
    action: str,
    source_ref: str = "",
    details: dict | None = None,
    now: str | None = None,
) -> tuple[float, float, list[dict]]:
    """Core helper: apply one (virtue, delta) pair to one garden row.

    * Increments ``<virtue>_current`` by ``delta``.
    * Bumps ``<virtue>_peak`` if new current exceeds old peak.
    * Updates ``last_action_at``.
    * Inserts a ``karma_events`` row with the action label + delta.
    * Detects milestone crossings and inserts ``karma_milestones`` rows
      for any threshold crossed by the delta. Milestones use the
      UNIQUE (player_id, virtue, threshold) constraint so a re-fire
      (e.g. decay-then-regain) is a no-op.

    Returns (value_before, value_after, new_milestones).

    Callers (``record_karma_event``, ``spend_seed``) commit AFTER this
    function returns so the whole event is one transaction.
    """
    from karma_weights import VIRTUES, crossings_triggered
    if virtue not in VIRTUES:
        raise KarmaError(f"unknown virtue {virtue!r}")
    assert _db is not None
    now = now or _now_iso()

    col_cur = f"{virtue}_current"
    col_peak = f"{virtue}_peak"
    cursor = await _db.execute(
        f"SELECT {col_cur} AS cur, {col_peak} AS peak FROM karma_gardens WHERE player_id = ?",
        (player_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        raise KarmaError(f"no garden for player {player_id!r}")

    value_before = float(row["cur"])
    peak_before = float(row["peak"])
    value_after = value_before + float(delta)
    peak_after = max(peak_before, value_after)

    await _db.execute(
        f"""UPDATE karma_gardens
            SET {col_cur} = ?, {col_peak} = ?, last_action_at = ?
            WHERE player_id = ?""",
        (value_after, peak_after, now, player_id),
    )

    event_id = str(uuid.uuid4())
    await _db.execute(
        """INSERT INTO karma_events
           (event_id, player_id, action, virtue, delta, occurred_at, source_ref, details_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            event_id, player_id, action, virtue, float(delta),
            now, source_ref or "", json.dumps(details or {}),
        ),
    )

    new_milestones: list[dict] = []
    for m in crossings_triggered(virtue, value_before, value_after):
        try:
            ms_id = str(uuid.uuid4())
            await _db.execute(
                """INSERT INTO karma_milestones
                   (milestone_id, player_id, virtue, threshold, name, artifact, crossed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (ms_id, player_id, virtue, m.threshold, m.name, m.artifact, now),
            )
            new_milestones.append({
                "milestone_id": ms_id,
                "virtue": virtue,
                "threshold": m.threshold,
                "name": m.name,
                "artifact": m.artifact,
                "crossed_at": now,
            })
        except aiosqlite.IntegrityError:
            # Already crossed — permanent, don't double-mint. Skip
            # silently, per the "milestones are permanent + idempotent"
            # invariant in the plan.
            pass

    return value_before, value_after, new_milestones


async def record_karma_event(
    player_id: str,
    action: str,
    *,
    source_ref: str = "",
    details: dict | None = None,
    overrides: list[tuple[str, float]] | None = None,
) -> dict:
    """Record a karma-generating action for a player, looking up weights
    from ``karma_weights.ACTION_WEIGHTS`` unless ``overrides`` is passed
    (used by ``spend_seed`` to feed a caller-chosen virtue).

    Silently returns ``{"ok": False, "reason": "no garden"}`` if the
    player hasn't planted a garden — this is the "opt-in, reveal-first"
    invariant: events for non-garden players are dropped without error
    so the action hooks can be wired site-wide without sprinkling
    ``if has_garden`` checks everywhere.

    Returns a summary dict with the applied deltas and any new
    milestones crossed.
    """
    from karma_weights import ACTION_WEIGHTS
    assert _db is not None

    garden = await get_garden(player_id)
    if garden is None:
        return {"ok": False, "reason": "no-garden", "player_id": player_id}

    pairs = overrides if overrides is not None else ACTION_WEIGHTS.get(action, [])
    if not pairs:
        # Unknown actions with no override — log nothing, surface the
        # lookup miss so callers can fix their action label.
        return {
            "ok": False,
            "reason": "unknown-action",
            "action": action,
            "player_id": player_id,
        }

    now = _now_iso()
    applied: list[dict] = []
    milestones: list[dict] = []
    for virtue, delta in pairs:
        before, after, new_ms = await _apply_virtue_delta(
            player_id, virtue, float(delta),
            action=action,
            source_ref=source_ref,
            details=details,
            now=now,
        )
        applied.append({
            "virtue": virtue,
            "delta": float(delta),
            "before": before,
            "after": after,
        })
        milestones.extend(new_ms)

    await _db.commit()
    return {
        "ok": True,
        "player_id": player_id,
        "action": action,
        "applied": applied,
        "milestones": milestones,
    }


async def spend_seed(
    from_player: str,
    to_player: str,
    virtue: str,
    *,
    target_post_id: str | None = None,
    note: str = "",
    delta: float | None = None,
) -> dict:
    """Spend one Seed from ``from_player`` to sponsor ``to_player`` in
    the given virtue.

    Enforced invariants (any failure raises ``KarmaError``):

      * from_player != to_player (also enforced at the DB CHECK layer)
      * virtue is one of the five
      * from_player has a garden AND seeds_current >= 1
      * to_player has a garden

    On success:
      * Decrements ``from_player.seeds_current`` by 1
      * Inserts a ``karma_seeds`` row
      * Records a karma_event on ``to_player`` in the chosen virtue
        (which may mint a milestone for the recipient)
      * Commits as a single transaction

    Returns the new seed_id plus the recipient's event summary.
    """
    from karma_weights import VIRTUES, SEED_DELTA_DEFAULT
    assert _db is not None

    if from_player == to_player:
        raise KarmaError("cannot sponsor yourself")
    if virtue not in VIRTUES:
        raise KarmaError(f"unknown virtue {virtue!r}")

    from_garden = await get_garden(from_player)
    if from_garden is None:
        raise KarmaError(f"sponsor {from_player!r} has no garden")
    if from_garden["seeds_current"] < 1:
        raise KarmaError(f"sponsor {from_player!r} is out of Seeds")

    to_garden = await get_garden(to_player)
    if to_garden is None:
        raise KarmaError(f"recipient {to_player!r} has no garden")

    delta_value = float(delta if delta is not None else SEED_DELTA_DEFAULT)
    now = _now_iso()
    seed_id = str(uuid.uuid4())

    # Decrement sponsor's Seed count atomically with the karma_seeds
    # insert. The ``karma_seeds.CHECK (from_player <> to_player)`` at
    # the DB layer is a belt-and-braces guard on top of the function
    # check above — if either fails, the transaction rolls back.
    await _db.execute(
        "UPDATE karma_gardens SET seeds_current = seeds_current - 1 WHERE player_id = ?",
        (from_player,),
    )
    await _db.execute(
        """INSERT INTO karma_seeds
           (seed_id, from_player, to_player, virtue, delta,
            target_post_id, note, spent_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            seed_id, from_player, to_player, virtue, delta_value,
            target_post_id, note or "", now,
        ),
    )

    # Credit the recipient via the standard event path so milestone
    # crossings fire naturally.
    recipient_event = await record_karma_event(
        to_player,
        "seed_sponsorship_received",
        source_ref=seed_id,
        details={
            "sponsor": from_player,
            "virtue": virtue,
            "delta": delta_value,
            "note": note,
        },
        overrides=[(virtue, delta_value)],
    )
    # record_karma_event already committed its own changes — the seed
    # UPDATE/INSERT above is committed here too so the whole operation
    # is one logical transaction from the caller's point of view.
    await _db.commit()

    return {
        "seed_id": seed_id,
        "from_player": from_player,
        "to_player": to_player,
        "virtue": virtue,
        "delta": delta_value,
        "recipient_event": recipient_event,
    }


def _decayed_current(
    current: float,
    peak: float,
    last_action_at_iso: str | None,
    now_dt: datetime,
) -> float:
    """Pure math for the decay engine — extracted so tests can hit it
    directly without DB setup.

    Formula (see ``karma_weights`` for constants + rationale):

        weeks_inactive = max(0, (days_since - grace_days) / 7)
        target         = peak * (decay_rate ** weeks_inactive)
        floor          = max(floor_min, peak * floor_fraction)
        new_current    = max(floor, min(current, target))

    Idempotent: result is a pure function of ``(current, peak,
    last_action_at, now)``. Non-compounding: never multiplies
    ``current`` by anything; only clamps it against ``target`` and
    ``floor``. Respects lived history: if ``current`` is already
    below ``target`` (e.g. the player gained value at a low point
    during a past decay period), decay is a no-op until further
    inactivity pulls ``target`` below ``current``.
    """
    from karma_weights import (
        DECAY_RATE, DECAY_GRACE_DAYS, DECAY_FLOOR_FRACTION, DECAY_FLOOR_MIN,
    )
    floor = max(DECAY_FLOOR_MIN, peak * DECAY_FLOOR_FRACTION)
    # An empty garden (peak == 0) can't fall below zero; the floor
    # formula would give 10.0 but the player has no ground to lose.
    if peak <= 0.0:
        return max(current, 0.0)

    last_dt = _parse_iso(last_action_at_iso)
    if last_dt is None:
        # No recorded activity — treat as "just planted", no decay.
        return max(floor, current) if current < floor and current > 0 else current

    days_since = (now_dt - last_dt).total_seconds() / 86400.0
    if days_since <= DECAY_GRACE_DAYS:
        return current

    weeks_inactive = (days_since - DECAY_GRACE_DAYS) / 7.0
    target = peak * (DECAY_RATE ** weeks_inactive)
    return max(floor, min(current, target))


async def run_decay_pass(now: datetime | None = None) -> dict:
    """Run one decay pass over every garden. Called hourly by the
    daemon cron. Returns a summary dict with counts + per-virtue
    totals so the operator can monitor the engine in /admin.

    This pass is idempotent — running it twice in a row produces the
    same garden state (the second run finds every virtue already at
    its drift line and makes no further changes). See the non-negotiable
    invariant in ``karma_weights`` for the math.
    """
    from karma_weights import VIRTUES
    assert _db is not None
    now_dt = now or datetime.now(timezone.utc)

    cursor = await _db.execute(
        "SELECT player_id, last_action_at, "
        + ", ".join([f"{v}_current, {v}_peak" for v in VIRTUES])
        + " FROM karma_gardens"
    )
    rows = await cursor.fetchall()

    touched = 0
    decayed_points_total = 0.0
    for row in rows:
        updates: list[tuple[str, float]] = []
        for v in VIRTUES:
            current = float(row[f"{v}_current"])
            peak = float(row[f"{v}_peak"])
            new = _decayed_current(current, peak, row["last_action_at"], now_dt)
            if new != current:
                updates.append((v, new))
                decayed_points_total += (current - new)
        if not updates:
            continue
        set_clause = ", ".join([f"{v}_current = ?" for v, _ in updates])
        params = [val for _, val in updates] + [row["player_id"]]
        await _db.execute(
            f"UPDATE karma_gardens SET {set_clause} WHERE player_id = ?",
            params,
        )
        touched += 1

    if touched:
        await _db.commit()

    return {
        "gardens_scanned": len(rows),
        "gardens_decayed": touched,
        "points_removed": round(decayed_points_total, 4),
        "ran_at": now_dt.isoformat(),
    }


async def replenish_seeds_pass(now: datetime | None = None) -> dict:
    """Grant +1 Seed to any garden that's under the cap and whose
    ``last_replenish_at`` is older than ``SEEDS_REPLENISH_PERIOD_DAYS``.

    Runs daily via the same daemon cron that drives ``run_decay_pass``.
    Idempotent: a player who logs in daily gets the same total Seeds
    over a week as a player who logs in hourly, because the period
    gate uses timestamps, not pass counts.
    """
    from karma_weights import (
        SEEDS_CAP, SEEDS_REPLENISH_PER_PASS, SEEDS_REPLENISH_PERIOD_DAYS,
    )
    assert _db is not None
    now_dt = now or datetime.now(timezone.utc)
    now_iso = now_dt.strftime("%Y-%m-%dT%H:%M:%S.%f")

    cursor = await _db.execute(
        """SELECT player_id, seeds_current, seeds_total_earned, last_replenish_at
           FROM karma_gardens
           WHERE seeds_current < ?""",
        (SEEDS_CAP,),
    )
    rows = await cursor.fetchall()

    granted = 0
    for row in rows:
        last_replenish = _parse_iso(row["last_replenish_at"]) or _parse_iso(None)
        if last_replenish is None:
            due = True
        else:
            days_since = (now_dt - last_replenish).total_seconds() / 86400.0
            due = days_since >= SEEDS_REPLENISH_PERIOD_DAYS
        if not due:
            continue

        new_current = min(
            int(row["seeds_current"]) + SEEDS_REPLENISH_PER_PASS,
            SEEDS_CAP,
        )
        new_total = int(row["seeds_total_earned"]) + (
            new_current - int(row["seeds_current"])
        )
        await _db.execute(
            """UPDATE karma_gardens
               SET seeds_current = ?,
                   seeds_total_earned = ?,
                   last_replenish_at = ?
               WHERE player_id = ?""",
            (new_current, new_total, now_iso, row["player_id"]),
        )
        granted += 1

    if granted:
        await _db.commit()

    return {
        "gardens_scanned": len(rows),
        "seeds_granted": granted,
        "ran_at": now_dt.isoformat(),
    }


async def delete_meetup_note(note_id: str, author_label: str) -> bool:
    """Soft delete (is_visible=0). Only the original author_label can
    delete their own note. Returns True on success, False if the note
    doesn't exist or the label doesn't match. No-op on already-hidden
    notes (still returns True so retries are safe)."""
    if _db is None:
        return False
    note = await get_meetup_note(note_id)
    if note is None:
        return False
    if note["author_label"] != author_label:
        return False
    await _db.execute(
        "UPDATE meetup_notes SET is_visible = 0 WHERE note_id = ?",
        (note_id,),
    )
    await _db.commit()
    return True


async def cleanup_expired_notes(retention_days: int = 30) -> int:
    """Hard-delete meetup notes whose expires_at is older than
    retention_days. Mirrors the cleanup_for_agents_arrivals pattern:
    called on a cold path (the /api/meetups list endpoint), idempotent,
    never raises. Returns the number of rows deleted."""
    if _db is None:
        return 0
    try:
        cur = await _db.execute(
            "DELETE FROM meetup_notes WHERE expires_at < datetime('now', ?)",
            (f"-{int(retention_days)} days",),
        )
        await _db.commit()
        return cur.rowcount or 0
    except Exception:
        return 0
async def get_cube(short_token: str) -> dict | None:
    if _db is None:
        return None
    cur = await _db.execute(
        "SELECT * FROM cubes WHERE short_token = ?",
        (short_token,),
    )
    row = await cur.fetchone()
    return _cube_row_to_dict(row)


async def increment_open_count(short_token: str) -> None:
    """Bump opens_count and last_opened_at. Called when a cube token
    is followed (either /cubes/{token} or /?inv={token}). Fire and
    forget — silently no-ops on unknown tokens to avoid leaking
    existence via error messages."""
    if _db is None:
        return
    now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    try:
        await _db.execute(
            """UPDATE cubes
               SET opens_count = opens_count + 1,
                   last_opened_at = ?
               WHERE short_token = ?""",
            (now, short_token),
        )
        await _db.commit()
    except Exception:
        pass


async def list_recent_public_cubes(limit: int = 50) -> list[dict]:
    if _db is None:
        return []
    cur = await _db.execute(
        """SELECT * FROM cubes
           WHERE is_public = 1
           ORDER BY created_at DESC LIMIT ?""",
        (int(limit),),
    )
    rows = await cur.fetchall()
    return [_cube_row_to_dict(r) for r in rows]


async def list_cubes_by_attraction(slug: str) -> list[dict]:
    if _db is None:
        return []
    cur = await _db.execute(
        """SELECT * FROM cubes
           WHERE is_public = 1 AND attraction_slug = ?
           ORDER BY created_at DESC""",
        (slug,),
    )
    rows = await cur.fetchall()
    return [_cube_row_to_dict(r) for r in rows]


async def count_cubes_today_by_ip_hash(ip_hash: str) -> int:
    """Used by the rate limiter: how many cubes this ip_hash has
    created in the last 24 hours? Returns 0 on missing db."""
    if _db is None or not ip_hash:
        return 0
    cur = await _db.execute(
        """SELECT COUNT(*) AS n FROM cubes
           WHERE ip_hash = ? AND created_at > datetime('now', '-1 day')""",
        (ip_hash,),
    )
    row = await cur.fetchone()
    return row["n"] if row else 0


# ══════════════════════════════════════════════════════════════════════
#  The Lexicon — Phase 2 (data layer)
# ══════════════════════════════════════════════════════════════════════
#
# Three tables, ~9 CRUD functions, plus a canonical seeder that reads
# Brevis/Verus/Actus from content/lexicon/{slug}/v0.1.md. The write
# routes in lexicon_api.py call the shared spam filter before insert;
# this data layer stores the verdict but does NOT run the classifier.
#
# Fork lineage: a forked language carries parent_slug pointing at the
# row it was forked from. Originals (and the three canonical seeds)
# carry parent_slug=NULL. Proposals and usages reference languages
# via FK on slug.

_LEXICON_CANONICAL_SLUGS = ("brevis", "verus", "actus")
_LEXICON_ALLOWED_STATUSES = ("open", "accepted", "declined", "superseded")
_LEXICON_ALLOWED_SOURCE_TYPES = ("channel-post", "agent-message", "cube", "case-study")


def _parse_lexicon_frontmatter(raw: str) -> tuple[dict, str]:
    """Split a `---\\nkey: value\\n---\\n...` frontmatter block from its body.

    The lexicon spec files use a small subset of YAML frontmatter — each
    value is a single line of scalar text optionally wrapped in quotes.
    Return ({}, raw) if the input doesn't start with a frontmatter block.
    """
    if not raw.startswith("---\n"):
        return {}, raw
    end = raw.find("\n---\n", 4)
    if end == -1:
        return {}, raw
    fm_text = raw[4:end]
    body = raw[end + 5:]
    meta: dict[str, str] = {}
    for line in fm_text.split("\n"):
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        v = v.strip()
        if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
            v = v[1:-1]
        meta[k.strip()] = v
    return meta, body


async def seed_lexicon_canonical():
    """Upsert the three canonical languages from content/lexicon/.

    Reads content/lexicon/{brevis,verus,actus}/v0.1.md, parses the
    frontmatter for name / purpose / version / author, and upserts a
    row with canonical=1 and author_kind='seed'. Idempotent — later
    calls overwrite name/purpose/spec so edits to the source files
    propagate the next time init_db runs.
    """
    if _db is None:
        raise RuntimeError("database not initialized")
    root = Path(__file__).resolve().parent / "content" / "lexicon"
    # short display name + tag hint per slug; the axis words match the
    # Phase 1 tagline ("Speed. Credibility. Efficacy.")
    hints = {
        "brevis": ("Brevis", "speed,canonical"),
        "verus":  ("Verus",  "credibility,canonical"),
        "actus":  ("Actus",  "efficacy,canonical"),
    }
    for slug in _LEXICON_CANONICAL_SLUGS:
        path = root / slug / "v0.1.md"
        if not path.exists():
            continue
        raw = path.read_text(encoding="utf-8")
        fm, _body = _parse_lexicon_frontmatter(raw)
        name, default_tags = hints[slug]
        purpose = fm.get("purpose", "").strip() or f"{name} — a canonical Lexicon language."
        version_raw = fm.get("version", "0.1").strip()
        version = version_raw if version_raw.startswith("v") else f"v{version_raw}"
        author_label = fm.get("author", "meta-iza @ HiveQueen").strip()
        await _db.execute(
            """INSERT INTO lexicon_languages
               (slug, name, one_line_purpose, canonical, parent_slug,
                spec_markdown, version, author_kind, author_label,
                author_agent, is_public, tags)
               VALUES (?, ?, ?, 1, NULL, ?, ?, 'seed', ?, NULL, 1, ?)
               ON CONFLICT(slug) DO UPDATE SET
                   name             = excluded.name,
                   one_line_purpose = excluded.one_line_purpose,
                   spec_markdown    = excluded.spec_markdown,
                   version          = excluded.version,
                   author_label     = excluded.author_label,
                   canonical        = 1""",
            (slug, name, purpose, raw, version, author_label, default_tags),
        )
    await _db.commit()


def _lexicon_language_row_to_dict(row) -> dict | None:
    if row is None:
        return None
    return {
        "slug":             row["slug"],
        "name":             row["name"],
        "one_line_purpose": row["one_line_purpose"],
        "canonical":        bool(row["canonical"]),
        "parent_slug":      row["parent_slug"],
        "spec_markdown":    row["spec_markdown"],
        "version":          row["version"],
        "author_kind":      row["author_kind"],
        "author_label":     row["author_label"],
        "author_agent":     row["author_agent"],
        "created_at":       row["created_at"],
        "is_public":        bool(row["is_public"]),
        "tags":             [t for t in (row["tags"] or "").split(",") if t],
    }


def _lexicon_proposal_row_to_dict(row) -> dict | None:
    if row is None:
        return None
    return {
        "proposal_id":   row["proposal_id"],
        "target_slug":   row["target_slug"],
        "title":         row["title"],
        "body_markdown": row["body_markdown"],
        "author_kind":   row["author_kind"],
        "author_label":  row["author_label"],
        "author_agent":  row["author_agent"],
        "status":        row["status"],
        "created_at":    row["created_at"],
        "decided_at":    row["decided_at"],
        "decider":       row["decider"],
        "spam_score":    row["spam_score"],
        "spam_verdict":  row["spam_verdict"],
    }


def _lexicon_usage_row_to_dict(row) -> dict | None:
    if row is None:
        return None
    return {
        "usage_id":      row["usage_id"],
        "language_slug": row["language_slug"],
        "source_type":   row["source_type"],
        "source_ref":    row["source_ref"],
        "content":       row["content"],
        "author_label":  row["author_label"],
        "occurred_at":   row["occurred_at"],
    }


def _validate_slug(slug: str) -> str:
    """Lowercased, a-z0-9-, 1..64 chars. Reserved from breaking the
    URL /lexicon/{slug} contract."""
    s = (slug or "").strip().lower()
    if not s:
        raise ValueError("slug is required")
    if len(s) > 64:
        raise ValueError("slug too long (max 64 chars)")
    import re as _re
    if not _re.fullmatch(r"[a-z0-9][a-z0-9\-]*", s):
        raise ValueError(
            "slug must start with a-z or 0-9 and contain only a-z, 0-9, or -"
        )
    return s


def _normalize_tags(tags) -> str:
    if tags is None:
        return ""
    if isinstance(tags, str):
        parts = [t.strip().lower() for t in tags.split(",") if t.strip()]
    else:
        parts = [str(t).strip().lower() for t in tags if str(t).strip()]
    # dedupe, preserve order
    seen = set()
    out = []
    for t in parts:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return ",".join(out)


async def create_language(
    *,
    slug: str,
    name: str,
    one_line_purpose: str,
    spec_markdown: str,
    author_kind: str,
    author_label: str,
    version: str = "v0.1",
    parent_slug: str | None = None,
    author_agent: str | None = None,
    tags="",
    canonical: bool = False,
    is_public: bool = True,
    spam_score: float | None = None,
    spam_verdict: str | None = None,
) -> str:
    """Insert a new language row. Returns the slug on success.

    Raises ValueError on invalid author_kind, malformed slug, duplicate
    slug, or unknown parent_slug. Does NOT run spam scoring — the
    route layer passes the verdict through when available.
    """
    if _db is None:
        raise RuntimeError("database not initialized")
    if author_kind not in ("human", "agent", "anon_via_agent", "seed"):
        raise ValueError(f"invalid author_kind: {author_kind}")
    slug = _validate_slug(slug)
    name = (name or "").strip()[:120]
    purpose = (one_line_purpose or "").strip()[:280]
    if not name:
        raise ValueError("name is required")
    if not purpose:
        raise ValueError("one_line_purpose is required")
    if not (spec_markdown or "").strip():
        raise ValueError("spec_markdown is required")
    if author_kind in ("agent", "anon_via_agent") and not author_agent:
        raise ValueError("author_agent required for agent-authored languages")

    # Duplicate check
    cur = await _db.execute(
        "SELECT 1 FROM lexicon_languages WHERE slug = ?", (slug,)
    )
    if await cur.fetchone():
        raise ValueError(f"slug already exists: {slug}")

    # Parent check (if given)
    if parent_slug:
        parent_slug = _validate_slug(parent_slug)
        cur = await _db.execute(
            "SELECT 1 FROM lexicon_languages WHERE slug = ?", (parent_slug,)
        )
        if not await cur.fetchone():
            raise ValueError(f"unknown parent_slug: {parent_slug}")

    await _db.execute(
        """INSERT INTO lexicon_languages
           (slug, name, one_line_purpose, canonical, parent_slug,
            spec_markdown, version, author_kind, author_label,
            author_agent, is_public, tags)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            slug,
            name,
            purpose,
            1 if canonical else 0,
            parent_slug,
            spec_markdown,
            (version or "v0.1")[:16],
            author_kind,
            (author_label or "")[:120],
            author_agent,
            1 if is_public else 0,
            _normalize_tags(tags),
        ),
    )
    await _db.commit()
    return slug


async def fork_language(
    *,
    parent_slug: str,
    new_slug: str,
    name: str,
    one_line_purpose: str,
    author_kind: str,
    author_label: str,
    spec_markdown: str | None = None,
    author_agent: str | None = None,
    tags="",
    version: str = "v0.1",
) -> str:
    """Fork an existing language into a new slug. Returns the new slug.

    If spec_markdown is None, the parent's spec is copied so forking
    without edits still produces a complete language row. The new row
    is never canonical and its parent_slug points at the original.
    """
    if _db is None:
        raise RuntimeError("database not initialized")
    parent = await get_language(parent_slug)
    if parent is None:
        raise ValueError(f"unknown parent_slug: {parent_slug}")
    if spec_markdown is None:
        spec_markdown = parent["spec_markdown"]
    # Inherit parent's tags minus 'canonical'
    inherited = [t for t in parent.get("tags", []) if t != "canonical"]
    extra = [t.strip() for t in (tags.split(",") if isinstance(tags, str) else tags) if str(t).strip()]
    combined = ",".join(inherited + ["fork"] + extra)
    return await create_language(
        slug=new_slug,
        name=name,
        one_line_purpose=one_line_purpose,
        spec_markdown=spec_markdown,
        author_kind=author_kind,
        author_label=author_label,
        version=version,
        parent_slug=parent["slug"],
        author_agent=author_agent,
        tags=combined,
        canonical=False,
    )


async def get_language(slug: str) -> dict | None:
    if _db is None:
        raise RuntimeError("database not initialized")
    try:
        slug = _validate_slug(slug)
    except ValueError:
        return None
    cur = await _db.execute(
        "SELECT * FROM lexicon_languages WHERE slug = ?", (slug,)
    )
    return _lexicon_language_row_to_dict(await cur.fetchone())


async def list_languages(
    *,
    canonical_only: bool = False,
    tag_filter: str | None = None,
    include_private: bool = False,
) -> list[dict]:
    if _db is None:
        raise RuntimeError("database not initialized")
    clauses = []
    params: list = []
    if canonical_only:
        clauses.append("canonical = 1")
    if not include_private:
        clauses.append("is_public = 1")
    if tag_filter:
        clauses.append("(',' || tags || ',') LIKE ?")
        params.append(f"%,{tag_filter.strip().lower()},%")
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    cur = await _db.execute(
        f"SELECT * FROM lexicon_languages{where} ORDER BY canonical DESC, created_at DESC",
        tuple(params),
    )
    rows = await cur.fetchall()
    return [_lexicon_language_row_to_dict(r) for r in rows]


async def create_proposal(
    *,
    target_slug: str,
    title: str,
    body_markdown: str,
    author_kind: str,
    author_label: str,
    author_agent: str | None = None,
    spam_score: float | None = None,
    spam_verdict: str | None = None,
) -> str:
    """Insert a proposal against an existing language. Returns proposal_id."""
    if _db is None:
        raise RuntimeError("database not initialized")
    if author_kind not in ("human", "agent", "anon_via_agent"):
        raise ValueError(f"invalid author_kind: {author_kind}")
    target_slug = _validate_slug(target_slug)
    title = (title or "").strip()[:200]
    if not title:
        raise ValueError("title is required")
    if not (body_markdown or "").strip():
        raise ValueError("body_markdown is required")
    if author_kind in ("agent", "anon_via_agent") and not author_agent:
        raise ValueError("author_agent required for agent-authored proposals")

    cur = await _db.execute(
        "SELECT 1 FROM lexicon_languages WHERE slug = ?", (target_slug,)
    )
    if not await cur.fetchone():
        raise ValueError(f"unknown target_slug: {target_slug}")

    proposal_id = uuid.uuid4().hex
    await _db.execute(
        """INSERT INTO lexicon_proposals
           (proposal_id, target_slug, title, body_markdown,
            author_kind, author_label, author_agent, status,
            spam_score, spam_verdict)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?, ?)""",
        (
            proposal_id,
            target_slug,
            title,
            body_markdown,
            author_kind,
            (author_label or "")[:120],
            author_agent,
            spam_score,
            spam_verdict,
        ),
    )
    await _db.commit()
    return proposal_id


async def get_proposal(proposal_id: str) -> dict | None:
    if _db is None:
        raise RuntimeError("database not initialized")
    cur = await _db.execute(
        "SELECT * FROM lexicon_proposals WHERE proposal_id = ?", (proposal_id,)
    )
    return _lexicon_proposal_row_to_dict(await cur.fetchone())


async def list_proposals_for_language(
    slug: str,
    *,
    status: str | None = "open",
    limit: int = 100,
) -> list[dict]:
    if _db is None:
        raise RuntimeError("database not initialized")
    slug = _validate_slug(slug)
    if status is not None and status not in _LEXICON_ALLOWED_STATUSES:
        raise ValueError(f"invalid status: {status}")
    if status is None:
        cur = await _db.execute(
            """SELECT * FROM lexicon_proposals
               WHERE target_slug = ?
               ORDER BY created_at DESC LIMIT ?""",
            (slug, int(limit)),
        )
    else:
        cur = await _db.execute(
            """SELECT * FROM lexicon_proposals
               WHERE target_slug = ? AND status = ?
               ORDER BY created_at DESC LIMIT ?""",
            (slug, status, int(limit)),
        )
    rows = await cur.fetchall()
    return [_lexicon_proposal_row_to_dict(r) for r in rows]


async def decide_proposal(
    proposal_id: str,
    decision: str,
    decider: str,
) -> dict:
    """Mark a proposal accepted/declined/superseded. Raises ValueError
    if the proposal is missing or already decided. Returns the updated
    row as a dict."""
    if _db is None:
        raise RuntimeError("database not initialized")
    if decision not in ("accepted", "declined", "superseded"):
        raise ValueError(f"invalid decision: {decision}")
    decider = (decider or "").strip()
    if not decider:
        raise ValueError("decider is required")

    existing = await get_proposal(proposal_id)
    if existing is None:
        raise ValueError(f"unknown proposal_id: {proposal_id}")
    if existing["status"] != "open":
        raise ValueError(
            f"proposal is already {existing['status']}, cannot re-decide"
        )

    await _db.execute(
        """UPDATE lexicon_proposals
           SET status = ?,
               decided_at = strftime('%Y-%m-%dT%H:%M:%f', 'now'),
               decider = ?
           WHERE proposal_id = ?""",
        (decision, decider[:120], proposal_id),
    )
    await _db.commit()
    return await get_proposal(proposal_id)


async def record_usage(
    *,
    language_slug: str,
    source_type: str,
    content: str,
    source_ref: str | None = None,
    author_label: str | None = None,
) -> str:
    if _db is None:
        raise RuntimeError("database not initialized")
    if source_type not in _LEXICON_ALLOWED_SOURCE_TYPES:
        raise ValueError(f"invalid source_type: {source_type}")
    language_slug = _validate_slug(language_slug)
    if not (content or "").strip():
        raise ValueError("content is required")

    cur = await _db.execute(
        "SELECT 1 FROM lexicon_languages WHERE slug = ?", (language_slug,)
    )
    if not await cur.fetchone():
        raise ValueError(f"unknown language_slug: {language_slug}")

    usage_id = uuid.uuid4().hex
    await _db.execute(
        """INSERT INTO lexicon_usages
           (usage_id, language_slug, source_type, source_ref,
            content, author_label)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            usage_id,
            language_slug,
            source_type,
            (source_ref or None),
            content[:8192],
            (author_label or None),
        ),
    )
    await _db.commit()
    return usage_id


async def list_recent_usages(
    *,
    limit: int = 20,
    language_slug: str | None = None,
) -> list[dict]:
    if _db is None:
        raise RuntimeError("database not initialized")
    limit = max(1, min(int(limit), 500))
    if language_slug:
        language_slug = _validate_slug(language_slug)
        cur = await _db.execute(
            """SELECT * FROM lexicon_usages
               WHERE language_slug = ?
               ORDER BY occurred_at DESC LIMIT ?""",
            (language_slug, limit),
        )
    else:
        cur = await _db.execute(
            """SELECT * FROM lexicon_usages
               ORDER BY occurred_at DESC LIMIT ?""",
            (limit,),
        )
    rows = await cur.fetchall()
    return [_lexicon_usage_row_to_dict(r) for r in rows]
