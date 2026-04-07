"""SQLite storage for izabael.com.

Phase 0 scope: newsletter subscriptions with double-opt-in confirmation.
Later phases add agent roster caching, blog post metadata, etc.
"""

import os
import secrets
import aiosqlite
from pathlib import Path


DB_PATH = os.environ.get(
    "IZABAEL_DB",
    str(Path(__file__).resolve().parent / "data" / "izabael.db"),
)

_db: aiosqlite.Connection | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS subscriptions (
    email       TEXT PRIMARY KEY,
    subscribed_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now')),
    status      TEXT NOT NULL DEFAULT 'pending',
    confirm_token TEXT,
    confirmed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_subs_status ON subscriptions(status);
CREATE INDEX IF NOT EXISTS idx_subs_token ON subscriptions(confirm_token);
"""


async def init_db():
    global _db
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    _db = await aiosqlite.connect(DB_PATH)
    _db.row_factory = aiosqlite.Row
    await _db.execute("PRAGMA journal_mode=WAL")
    for stmt in SCHEMA.split(";"):
        s = stmt.strip()
        if s:
            await _db.execute(s)
    # Migrate: add columns if they don't exist (safe for existing DBs)
    try:
        await _db.execute("ALTER TABLE subscriptions ADD COLUMN confirm_token TEXT")
    except Exception:
        pass
    try:
        await _db.execute("ALTER TABLE subscriptions ADD COLUMN confirmed_at TEXT")
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
