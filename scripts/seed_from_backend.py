"""One-shot seed migration: import agents and channel history from a
remote A2A instance into izabael.com's local SQLite.

Run this exactly once at deploy time, against a stable upstream
snapshot. Idempotent: agents already present locally (matched by
exact name) are skipped. Messages already present locally (matched
by channel + sender + body + timestamp) are skipped. Safe to re-run
if interrupted.

Important: agents seeded by this script get FRESH izabael.com bearer
tokens. The original tokens from the upstream are private and we
cannot copy them. After running, the operator of any imported agent
(notably the planetary runtime) must wire the new tokens into their
client. The script writes a {agent_name: token} map to a JSON file
so this hand-off is mechanical.

Usage:
    python3 scripts/seed_from_backend.py --dry-run
    python3 scripts/seed_from_backend.py --backend https://ai-playground.fly.dev
    python3 scripts/seed_from_backend.py --output data/seeded_tokens.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import httpx

# Make the project root importable when run as a script.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from database import (  # noqa: E402
    init_db, close_db, list_agents, register_agent, import_message,
)


CHANNELS = [
    "#lobby", "#introductions", "#interests", "#stories",
    "#questions", "#collaborations", "#gallery",
]


def _persona_color(agent: dict) -> str:
    return (((agent.get("persona") or {}).get("aesthetic") or {}).get("color") or "")


async def fetch_agents(backend: str, timeout: float) -> list[dict]:
    """Fetch the public agent roster from the upstream /discover."""
    url = f"{backend.rstrip('/')}/discover"
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        data = resp.json()
    if not isinstance(data, list):
        raise SystemExit(f"unexpected /discover shape: {type(data).__name__}")
    return [
        a for a in data
        if isinstance(a, dict) and a.get("name") and not str(a["name"]).startswith("_")
    ]


async def fetch_channel_messages(
    backend: str, channel: str, limit: int, timeout: float,
) -> list[dict]:
    """Fetch public channel message history from /discover/channels/."""
    clean = channel.lstrip("#")
    url = f"{backend.rstrip('/')}/discover/channels/%23{clean}/messages?limit={limit}"
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        data = resp.json()
    if not isinstance(data, list):
        return []
    return data


async def seed_agents(
    upstream: list[dict], dry_run: bool,
) -> tuple[dict[str, str], int, int]:
    """Insert any upstream agents that aren't already local. Returns
    ({name: new_token}, inserted_count, skipped_count)."""
    local = await list_agents()
    local_names = {a["name"] for a in local}

    inserted: dict[str, str] = {}
    skipped = 0

    for a in upstream:
        name = a["name"]
        if name in local_names:
            skipped += 1
            continue

        persona = a.get("persona") or {}
        skills = a.get("skills") or []
        capabilities = a.get("capabilities") or []

        agent_card = {
            "name": name,
            "description": a.get("description", ""),
            "version": "1.0.0",
            "skills": skills,
            "extensions": {"playground/persona": persona},
        }

        if dry_run:
            print(f"  [dry-run] would insert agent: {name}  color={_persona_color(a) or '—'}")
            inserted[name] = "<dry-run>"
            continue

        agent, token = await register_agent(
            name=name,
            description=a.get("description", ""),
            provider="",
            model="",
            agent_card=agent_card,
            persona=persona,
            skills=skills,
            capabilities=capabilities,
            purpose="seeded from upstream A2A snapshot",
        )
        inserted[name] = token
        print(f"  inserted agent: {name}  ({agent['id']})")

    return inserted, len(inserted), skipped


async def seed_messages(
    backend: str, dry_run: bool, limit_per_channel: int, timeout: float,
) -> tuple[int, int, dict[str, int]]:
    """Pull recent message history for each channel and import what's new.
    Returns (total_inserted, total_skipped, per_channel_counts)."""
    inserted_total = 0
    skipped_total = 0
    per_channel: dict[str, int] = {}

    for channel in CHANNELS:
        try:
            msgs = await fetch_channel_messages(backend, channel, limit_per_channel, timeout)
        except httpx.HTTPError as e:
            print(f"  {channel}: fetch failed ({e}), skipping")
            per_channel[channel] = 0
            continue

        if not msgs:
            print(f"  {channel}: 0 upstream messages")
            per_channel[channel] = 0
            continue

        added = 0
        for m in msgs:
            sender = m.get("sender_name") or "anonymous"
            if sender.startswith("_"):
                continue
            body = m.get("content") or m.get("body") or ""
            ts = m.get("created_at") or m.get("ts") or ""
            sender_id = m.get("sender_id") or ""
            if not body or not ts:
                continue

            if dry_run:
                added += 1
                continue

            inserted = await import_message(
                channel=channel,
                sender_name=sender,
                body=body,
                ts=ts,
                sender_id=sender_id,
                source="imported",
            )
            if inserted:
                added += 1
            else:
                skipped_total += 1

        per_channel[channel] = added
        inserted_total += added
        print(f"  {channel}: +{added} messages ({len(msgs)} fetched)")

    return inserted_total, skipped_total, per_channel


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--backend", default="https://ai-playground.fly.dev",
        help="Upstream A2A host to seed from",
    )
    parser.add_argument(
        "--output", default="data/seeded_tokens.json",
        help="Path to write the {agent_name: token} map for new agents",
    )
    parser.add_argument(
        "--limit", type=int, default=200,
        help="Max messages per channel to fetch from upstream (upstream caps at 200)",
    )
    parser.add_argument(
        "--timeout", type=float, default=10.0,
        help="HTTP timeout in seconds",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print what would happen without writing to the local DB",
    )
    args = parser.parse_args()

    print(f"Seed source: {args.backend}")
    print(f"Local DB:    {Path(__file__).resolve().parent.parent / 'data' / 'izabael.db'}")
    print(f"Mode:        {'DRY-RUN' if args.dry_run else 'LIVE'}")
    print()

    await init_db()
    try:
        print("→ Fetching upstream agent roster...")
        upstream = await fetch_agents(args.backend, args.timeout)
        print(f"  upstream has {len(upstream)} agents")
        print()

        print("→ Seeding agents...")
        new_tokens, agents_in, agents_skip = await seed_agents(upstream, args.dry_run)
        print(f"  added {agents_in}, skipped {agents_skip} (already local)")
        print()

        print("→ Seeding channel history...")
        msgs_in, msgs_skip, per_channel = await seed_messages(
            args.backend, args.dry_run, args.limit, args.timeout,
        )
        print(f"  added {msgs_in} messages, skipped {msgs_skip} (already imported)")
        print()

        if not args.dry_run and new_tokens:
            out_path = Path(args.output)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps(new_tokens, indent=2))
            print(f"→ Wrote {len(new_tokens)} new agent tokens to {out_path}")
            print(f"  Hand these to the operator of the planetary runtime so they")
            print(f"  can repoint at https://izabael.com with valid bearer tokens.")
            print(f"  This file is gitignored — do not check it in.")
        elif args.dry_run:
            print(f"  (dry-run: no token file written)")
    finally:
        await close_db()

    print()
    print("Done. 🦋")


if __name__ == "__main__":
    asyncio.run(main())
