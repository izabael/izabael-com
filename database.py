"""SQLite storage for izabael.com.

Phase 0 scope: newsletter subscriptions. Later phases add agent roster
caching, blog post metadata, etc.
"""

import os
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
    status      TEXT NOT NULL DEFAULT 'active'
);
CREATE INDEX IF NOT EXISTS idx_subs_status ON subscriptions(status);
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
    await _db.commit()


async def close_db():
    global _db
    if _db:
        await _db.close()
        _db = None


async def save_subscription(email: str):
    assert _db is not None
    email = email.strip().lower()
    if not email or "@" not in email:
        raise ValueError("invalid email")
    await _db.execute(
        "INSERT OR IGNORE INTO subscriptions (email) VALUES (?)",
        (email,),
    )
    await _db.commit()
