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
    ]:
        try:
            await _db.execute(col_sql)
        except Exception:
            pass

    # Retry indexes that may have failed above (columns now exist)
    for idx_sql in [
        "CREATE INDEX IF NOT EXISTS idx_subs_token ON subscriptions(confirm_token)",
    ]:
        try:
            await _db.execute(idx_sql)
        except Exception:
            pass

    await _db.commit()


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
) -> tuple[dict, str]:
    """Register a new agent. Returns (agent_dict, api_token)."""
    assert _db is not None
    agent_id = str(uuid.uuid4())
    api_token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")

    await _db.execute(
        """INSERT INTO agents
           (id, name, description, provider, model, agent_card, persona,
            skills, capabilities, purpose, api_token, created_at, last_seen)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            agent_id, name, description, provider, model,
            json.dumps(agent_card or {}),
            json.dumps(persona or {}),
            json.dumps(skills or []),
            json.dumps(capabilities or []),
            purpose, api_token, now, now,
        ),
    )
    await _db.commit()

    agent = _agent_dict(
        agent_id, name, description, provider, model, "online",
        agent_card or {}, persona or {}, skills or [], capabilities or [],
        now, now,
    )
    return agent, api_token


async def list_agents() -> list[dict]:
    """List all non-system agents for discovery."""
    assert _db is not None
    cursor = await _db.execute(
        """SELECT id, name, description, provider, model, status,
                  agent_card, persona, skills, capabilities,
                  created_at, last_seen
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
                  created_at, last_seen
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


def _row_to_agent(row) -> dict:
    """Convert a DB row to an agent dict."""
    return _agent_dict(
        row["id"], row["name"], row["description"],
        row["provider"], row["model"], row["status"],
        json.loads(row["agent_card"]),
        json.loads(row["persona"]),
        json.loads(row["skills"]),
        json.loads(row["capabilities"]),
        row["created_at"], row["last_seen"],
    )


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
