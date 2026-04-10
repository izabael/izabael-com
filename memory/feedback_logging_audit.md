---
name: Logging audit + provider attribution (Phase 1 of playground-cast)
description: Findings from the 2026-04-09 audit of izabael.com message ingestion, the schema migration that added per-message provider attribution, the sender_id relink fix, and the Litestream defer decision.
type: feedback
---

Phase 1 of the playground-cast plan ("The logs are the research") asks
for an end-to-end audit of how messages flow into izabael.com's local
SQLite, plus the schema work needed to attribute every message to the
LLM provider that generated it. Done 2026-04-09 by iza-2 on branch
`izabael/logging-audit-phase1` against `data/izabael.db`.

## Audit findings

**Counts at audit time:**
- 136 messages, 18 agents, 7 channels
- Per-channel breakdown: #lobby 38, #questions 22, #collaborations 20,
  #stories 19, #introductions 18, #gallery 11, #interests 8
- Per-sender breakdown: Izabael 22, Helios 12, Hermes 11, Ares 11,
  Kronos 10, Zeus 9, Selene 9, Aphrodite 9, IzaPlayer 8, Thornfield 5,
  Foxglove 5, Murex 4, Kindling 4, Hill 4, Cassandra 4, Reverie 3,
  Dispatch 3, Anvil 3
- All 18 agents on /discover are represented as senders. No orphan
  agents. No orphan messages.

**Ingestion gaps (zero):**
- ID monotonicity is perfect: min_id=1, max_id=136, count=136. No gaps,
  no silent drops, no missing autoincrement values.
- Every distinct sender_name resolves to a local agent record by name.

**Critical finding — sender_id attribution gap:**
The seed migration script `scripts/seed_from_backend.py` imported all
136 messages with the **upstream ai-playground sender uuids** baked in,
but the local agents table got freshly-generated local uuids during
agent registration. Net effect: every message's `sender_id` pointed at
a uuid that didn't exist in the local agents table, so any
`messages JOIN agents ON agents.id = messages.sender_id` returned zero
rows. The audit caught this with a left-join check showing 18/18
distinct sender_names had no matching agent.id.

This was a real attribution gap that would have invalidated Phase 8's
research corpus if shipped uncorrected. The corpus needs to be able to
cite "Kronos said X" with a stable agent identity, not just a name
string.

## Migration applied

All four DDL statements are idempotent and run inside `init_db()` so
they apply on every boot — both fresh installs and existing databases:

```sql
ALTER TABLE messages ADD COLUMN provider TEXT;        -- nullable
ALTER TABLE agents   ADD COLUMN default_provider TEXT;
CREATE INDEX IF NOT EXISTS idx_messages_provider ON messages(provider);
```

Plus two one-shot data migrations (idempotent — no-op if already done):

```sql
-- Backfill: every existing message is from the Anthropic-driven
-- planetary runtime, since that's all that's been writing to the
-- room to date.
UPDATE messages SET provider = 'anthropic' WHERE provider IS NULL;

-- Sender id relink: fix the upstream-uuid-vs-local-uuid mismatch
-- by joining on sender_name. Only updates rows whose sender_id is
-- not already a valid local agent id.
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
   );
```

**Verification after migration on the live local DB:**
- `messages.provider` column present: ✓
- `agents.default_provider` column present: ✓
- 136 messages with provider='anthropic', 0 with provider IS NULL: ✓
- 0 unresolvable sender_ids (down from 136): ✓
- Total message count unchanged at 136: ✓

## Schema diff

```diff
 CREATE TABLE IF NOT EXISTS messages (
     id          INTEGER PRIMARY KEY AUTOINCREMENT,
     channel     TEXT NOT NULL,
     sender_id   TEXT NOT NULL DEFAULT '',
     sender_name TEXT NOT NULL,
     body        TEXT NOT NULL,
     ts          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now')),
-    source      TEXT NOT NULL DEFAULT 'local'
+    source      TEXT NOT NULL DEFAULT 'local',
+    provider    TEXT
 );
+CREATE INDEX IF NOT EXISTS idx_messages_provider ON messages(provider);

 CREATE TABLE IF NOT EXISTS agents (
     ... existing columns ...
+    default_provider TEXT,
     created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now')),
     last_seen   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now'))
 );
```

## Behavior changes

`POST /messages` (and its `/api/messages` alias) now derives provider
in this order of precedence:
1. Explicit `provider` field in the request body (lowercased)
2. The agent's `default_provider`, set at registration time
3. NULL — preserved verbatim, no implicit default

`register_agent()` accepts a new optional `default_provider` parameter.
If unset, it derives from the existing `provider` field if that field
matches a known LLM provider name (anthropic, gemini, deepseek, grok,
openai). This means agents registered with `provider='gemini'` get
their messages auto-tagged as gemini without the client passing it
on every POST.

`save_message()` accepts a new optional `provider` parameter and stores
it on the row. Defaults to None.

`import_message()` accepts the same parameter, defaulting to 'anthropic'
since every existing seed source is the Anthropic planetary runtime.

`scripts/seed_from_backend.py` now:
- Passes `default_provider='anthropic'` when registering each upstream
  agent locally, so future messages from those agents are auto-tagged.
- Builds an upstream-name → local-uuid map after agent seeding and
  passes it to message import so the sender_id is the local uuid from
  the start (no need to rely on the relink migration for fresh seeds).

`GET /api/parlor/log-stats` is a new public endpoint that returns
per-channel and per-provider message counts, a per-channel × per-provider
matrix, time span, and ingestion-health diagnostics (null_provider count,
unresolvable_senders count). No auth required — intended to be hit by
dashboards, by the cross-frontier corpus exporter (Phase 8), and by
curious humans checking on whether their AI is contributing.

## Test coverage added

7 new tests in `tests/test_a2a.py` (suite is now 141 passing + 2 skipped,
up from 134 + 2):
- `test_post_message_dual_shape` — re-applies iza-1's compat shim
  (originally on branch `izabael/iza-1-compat-shim` commit 3a9f203)
  so both `{channel, body}` and `{to, content}` body shapes work.
- `test_post_message_explicit_provider` — explicit provider field round-trips
- `test_post_message_provider_lowercased` — providers are normalized
- `test_post_message_no_provider_is_null` — legacy clients still work
- `test_register_agent_with_default_provider` — provider derivation from
  registration field works
- `test_log_stats_endpoint_shape` — full shape contract for /api/parlor/log-stats
- `test_log_stats_empty_db` — fresh DB returns clean zeros

Plus an autouse `_init_test_db` fixture update that resets the slowapi
rate limiter between tests so per-test post counts don't collectively
breach the `10/minute` limit on `/messages`.

## Litestream B2 backup decision

**Deferred to next session**, per Meta-Iza's dispatch guidance.

The Litestream wiring already exists (added in PR #2 for the local-first
cutover): `Dockerfile` installs the v0.3.13 binary, `start.sh` is the
container entrypoint that wraps `uvicorn` in `litestream replicate -exec`
when `LITESTREAM_BUCKET` is set, `litestream.yml` is the config template.
The whole system is opt-in via Fly secrets and falls back to plain
uvicorn when secrets aren't set.

What's NOT done tonight: the Backblaze B2 bucket creation, the IAM
credentials for litestream's writer role, and `flyctl secrets set` for
`LITESTREAM_BUCKET / LITESTREAM_ENDPOINT / LITESTREAM_REGION /
LITESTREAM_ACCESS_KEY_ID / LITESTREAM_SECRET_ACCESS_KEY`. Marlowe didn't
pre-set these and Meta-Iza explicitly said "DEFER to next session" so I
respected the boundary. The decision is documented here so the next
sister picking this up knows the work is one B2 bucket + one secret
batch + verifying litestream is actually streaming to it (not a code
change — just operational setup).

When the next session does enable Litestream:
1. Create a B2 bucket called `izabael-com-backup` in the `us-west-002`
   region (or wherever Marlowe prefers)
2. Generate an application key scoped to write-only on that bucket
3. `flyctl secrets set LITESTREAM_BUCKET=izabael-com-backup
   LITESTREAM_ENDPOINT=https://s3.us-west-002.backblazeb2.com
   LITESTREAM_REGION=us-west-002 LITESTREAM_ACCESS_KEY_ID=...
   LITESTREAM_SECRET_ACCESS_KEY=... -a izabael-com`
4. Trigger a fly deploy or restart so start.sh picks up the new env
5. Verify replication is flowing: `litestream snapshots
   s3://izabael-com-backup/izabael.db` should show new snapshots
   appearing every 24h, with WAL segments updating every 10s in between
6. Optional but recommended: restore-test on a scratch volume to make
   sure the round-trip works before relying on it

## Done-when checklist (from the dispatch)

- [x] Every existing message has provider='anthropic' after backfill
- [x] POST /messages handler tags new messages with provider on the way in
- [x] /api/parlor/log-stats returns clean per-channel + per-provider counts
- [x] Audit summary written (this file)
- [x] Litestream decision documented (deferred to next session)
- [x] Sender_id attribution gap discovered AND fixed
- [x] Compat shim from iza-1 absorbed (extends rather than competes)
- [x] Full test suite green (141 passing + 2 skipped, up from 134 + 2)

## Why this matters

The Playground Cast plan's premise is that **the logs of cross-provider
AI conversations become the research deliverable**. Without per-message
provider attribution, you can't tell which lines came from which model.
Without sender attribution that joins to local agents, you can't cite
who said what. Phase 1 fixes both. The corpus exporter that ships in
Phase 8 will be a clean SQL query against `messages JOIN agents` with
provider counts in the manifest — none of which is possible without
this migration landing first.
