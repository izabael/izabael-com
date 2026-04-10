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
from datetime import datetime, timezone

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

    await _db.commit()

    # Seed persona templates from the bundled JSON if the table is empty.
    # Idempotent: skips slugs that already exist.
    seed_path = Path(__file__).resolve().parent / "seeds" / "persona_templates.json"
    try:
        await seed_persona_templates(str(seed_path))
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
        if provider.lower() in ("anthropic", "gemini", "deepseek", "grok", "openai"):
            resolved_default = provider.lower()

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
    POST handler time. Stored as NULL only if truly unknown.
    """
    assert _db is not None
    channel = channel.strip()
    if not channel.startswith("#"):
        channel = "#" + channel
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
        """SELECT id, channel, sender_id, sender_name, body, ts, source
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
        """SELECT id, channel, sender_id, sender_name, body, ts, source
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
