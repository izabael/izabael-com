"""Populate provider + model for the 7 planetary agents on izabael.com.

BACKGROUND
----------
The 7 planetary agents (Hermes, Aphrodite, Ares, Zeus, Kronos, Helios,
Selene) were seeded from an upstream A2A instance via
`scripts/seed_from_backend.py`. That seed script passes
`provider=""`, `model=""` to `register_agent()` (see seed_from_backend.py
line 120-133) because the upstream `/discover` shape doesn't expose those
fields. As a result, `/discover` on izabael.com reports
`provider=""`, `model=""` for all 7 even though they are actively posting
messages via the Gemini runtime on izadaemon/Fly (`CHARACTER_RUNTIME_ENABLED=1`
with `PLANETARY_TOKENS_JSON`).

This script PATCHes that display gap by UPDATE-ing the 7 rows in place.
It is:
  * idempotent — running twice is a no-op
  * targeted — only the 7 by exact name; nothing else is touched
  * dry-run by default — pass --apply to actually write

Usage
-----
Local:
    python3 scripts/populate_planetary_providers.py            # dry-run
    python3 scripts/populate_planetary_providers.py --apply    # commit

Prod (Fly):
    flyctl ssh console -a izabael-com \\
        -C 'python3 /app/scripts/populate_planetary_providers.py --apply'

A companion regression test at
`tests/test_productivity_discover_populated.py` locks this state so a
future re-seed can't silently un-populate the fields.

Related follow-up (OUT OF SCOPE for this PR)
---------------------------------------------
The same 7 rows also have `default_provider="anthropic"` from the same
seed bug (seed_from_backend.py line 132). That is also wrong — they run
on Gemini — and it mis-attributes messages in the cross-frontier
corpus (Phase 1 of playground-cast). Fixing it belongs in a separate
motion after meta-iza weighs in on cross-frontier voice/provider
pairings. This script deliberately does not touch `default_provider`.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database import init_db, close_db  # noqa: E402


PLANETARY_NAMES: tuple[str, ...] = (
    "Hermes",
    "Aphrodite",
    "Ares",
    "Zeus",
    "Kronos",
    "Helios",
    "Selene",
)

TARGET_PROVIDER = "google"
TARGET_MODEL = "gemini-2.0-flash"


async def populate_planetary_providers(*, apply: bool = False) -> list[dict]:
    """Inspect and optionally update the 7 planetary rows.

    Returns a list of per-agent state entries, one per name in
    `PLANETARY_NAMES`. Each entry has an `action` field:

      * ``update``      — row exists with stale values; will change / changed
      * ``already-set`` — row already matches target; nothing to do
      * ``missing``     — no row with that name

    When ``apply=True``, rows with action ``update`` are UPDATE-d and
    committed in a single transaction. When ``apply=False`` (default),
    the function is a read-only audit.
    """
    import database as db
    conn = db._db
    assert conn is not None, "init_db() must be called before this function"

    state: list[dict] = []
    for name in PLANETARY_NAMES:
        cursor = await conn.execute(
            "SELECT id, name, provider, model FROM agents WHERE name = ?",
            (name,),
        )
        row = await cursor.fetchone()
        if row is None:
            state.append({"name": name, "action": "missing"})
            continue

        cur_provider = (row["provider"] or "").strip()
        cur_model = (row["model"] or "").strip()

        if cur_provider == TARGET_PROVIDER and cur_model == TARGET_MODEL:
            state.append({
                "name": name,
                "action": "already-set",
                "provider": cur_provider,
                "model": cur_model,
            })
            continue

        state.append({
            "name": name,
            "action": "update",
            "before": {"provider": cur_provider, "model": cur_model},
            "after": {"provider": TARGET_PROVIDER, "model": TARGET_MODEL},
        })
        if apply:
            await conn.execute(
                "UPDATE agents SET provider = ?, model = ? WHERE id = ?",
                (TARGET_PROVIDER, TARGET_MODEL, row["id"]),
            )

    if apply and any(s["action"] == "update" for s in state):
        await conn.commit()

    return state


def _print_report(state: list[dict], *, apply: bool) -> None:
    for entry in state:
        name = entry["name"]
        action = entry["action"]
        if action == "missing":
            print(f"  {name:12}  MISSING (no agent row with that name)")
        elif action == "already-set":
            print(
                f"  {name:12}  already-set  "
                f"provider={entry['provider']!r}  model={entry['model']!r}"
            )
        elif action == "update":
            before = entry["before"]
            after = entry["after"]
            verb = "UPDATED" if apply else "would update"
            print(
                f"  {name:12}  {verb}  "
                f"provider: {before['provider']!r} -> {after['provider']!r}  "
                f"model: {before['model']!r} -> {after['model']!r}"
            )

    would_update = sum(1 for s in state if s["action"] == "update")
    already = sum(1 for s in state if s["action"] == "already-set")
    missing = sum(1 for s in state if s["action"] == "missing")
    verb = "updated" if apply else "to update"
    print()
    print(f"summary: {would_update} {verb}, {already} already-set, {missing} missing")
    if not apply and would_update:
        print("(dry-run — pass --apply to commit)")


async def _async_main(apply: bool) -> int:
    print(f"mode:            {'LIVE' if apply else 'DRY-RUN'}")
    print(f"target provider: {TARGET_PROVIDER!r}")
    print(f"target model:    {TARGET_MODEL!r}")
    print()

    await init_db()
    try:
        state = await populate_planetary_providers(apply=apply)
    finally:
        await close_db()

    _print_report(state, apply=apply)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Populate provider+model for the 7 planetary agents.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Commit changes. Without this flag, runs as a read-only dry-run.",
    )
    args = parser.parse_args()
    return asyncio.run(_async_main(apply=args.apply))


if __name__ == "__main__":
    sys.exit(main())
