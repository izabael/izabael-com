# Resume — playground-cast 9/9 LIVE + /for-agents redesign + nohumansallowed.org

**Date parked:** 2026-04-10 night (~late, queen-orchestrated synchronized park)
**Branch:** `main` (clean, in sync with origin)
**Working tree:** clean
**Test status:** 180 passing + 2 by-design skips (152 pre-existing + 28 new from PR #7)
**Total session output:** 5 PRs deployed to prod, 1 production data fix, 1 new domain wired, 1 token chain end-to-end across two Fly apps

## What shipped tonight

This was the marathon completion session for the playground-cast plan. Across the colony (iza-1 in izadaemon, iza-3 in izabael-com + izaplayer, iza-2 in izabael-com), all 9 phases moved from "partial / blocked" to "LIVE in production" — and meta-iza coordinated the whole thing through queen mail.

**My (iza-2) lane this session, in order:**

1. **PR #5 merged** (iza-3's `/for-agents` post_message-shape fix + `/4agents` short URL redirect — small follow-up, took it before #4 because both touched app.py and #5 was tinier)
2. **PR #4 merged + deployed** (Phase 1: provider attribution + audit + sender_id relink + KNOWN_PROVIDERS validator + compound `(provider, ts)` index + new `/api/parlor/log-stats` endpoint, all idempotent inside `init_db()`)
3. **`unresolvable_senders=1` fail-stop investigated** — single AriaTest3 orphan from earlier sister testing. Held for Marlowe-via-queen approval, then deleted under audit trail with safety-belted `WHERE id=183 AND sender_name='AriaTest3'`. Metric flipped to clean `unresolvable_senders=0`.
4. **PR #6 merged + deployed** (iza-3's Phase 8 corpus URL serving — `/research/playground-corpus/`, `/research/playground-corpus/methodology`, daily snapshot ingestion, homepage link, full OG/Twitter/JSON-LD metadata)
5. **`nohumansallowed.org` Cloudflare cutover**: registered the bot-onboarding domain with Marlowe over the course of an hour-long step-by-step thread. GoDaddy → Cloudflare nameserver swap (brett + zelda), free TLS auto-issued, single redirect rule with `concat("https://izabael.com/for-agents", http.request.uri.path)` and Preserve-Query-String toggle. Validated path AND query state both flow through. Both apex and `www` covered.
6. **PR #7 — /for-agents redesign + URL-state personalization** (the big one this session). Reframed the page from API documentation into an LLM-as-reader landing page. New module `for_agents_personalization.py` with whitelist + lookup + greeting composition. New routes: `/for-agents/{shortcut}` for path-based personalization. New table `for_agents_arrivals` with NO ip_hash (per meta-iza GDPR verdict) and 90-day cold-path cleanup at most every 6h. 28 new tests. Full suite 180+2 skipped. CI green. Merged + deployed by me. **LLM relay test against Gemini 2.0 Flash hit 4 of 5 success criteria** (agent count, channel names #lobby/#introductions/#collaborations, register-in-one-call, cross-provider-persistent-openly-owned vision). The reframe works.
7. **Phase 7 closure — Cassandra archaeology**. Iza-1 had her framework live but Cassandra's bearer token mismatch was the last blocker. I extracted the live token from prod's `agents.api_token` column (the column is `api_token`, NOT `token` — multiple seed files were wrong), validated via POST /messages, set `CHARACTER_CASSANDRA_TOKEN` on izadaemon via `flyctl secrets import` over stdin (literal never on cmdline), waited for the post-restart character stagger (~90s — first webhook was too early at +17s and got `0 fired`), re-fired `/webhook/deploy`, and Cassandra posted message id 401 in #questions: *"The commit message says 'final-test' but you're shipping to production at 00:36 UTC on a Friday — who's awake to catch the first failure, and could that timing preference itself be a decision worth documenting separately from the code?"* — perfect ethics-researcher voice, hit every constraint in her character file.

## What's NOT done

- **Phase 10 — `/for-agents?state=<id>` URL state persistence** (Marlowe's deferred idea, ~60-90 min, iza-2 is the candidate next session because the personalization layer is warm). Memory: `~/.claude/projects/-home-bastard-Documents-izabael-com/memory/project_phase_10_url_state.md`. Constraints: state IDs are opaque short hashes not bearer tokens; rows are short-TTL; never store credentials in the table; whitelist hydratable fields = same whitelist as the query-param parser.
- **Litestream B2 backup** — still deferred from the previous session. Wiring exists from Phase 1's local-first PR but the bucket creation + Fly secrets remain a manual one-shot whenever Marlowe wants DR insurance. Step-by-step in `memory/feedback_logging_audit.md`.
- **The `provider=null` on new POST /messages without explicit tag** — minor: when an agent posts without passing `provider` in the body, the column lands as NULL even if the agent has a `default_provider` set. Caught it during Cassandra wiring. Not blocking, but worth a one-line fix in the POST handler to read the agent row's `default_provider` and fall through. Note for next session.
- **`register_agent` provider→default_provider auto-tag is too narrow.** Only triggers if `provider.lower() in ("anthropic","gemini","deepseek","grok","openai")`. Characters registered with `provider="claude-haiku-4-5"` won't auto-tag. Worth widening to a prefix match or using the full KNOWN_PROVIDERS frozenset. Cosmetic, not blocking.

## Deploy state at park time

| Component | State |
|---|---|
| izabael-com (Fly app) | Running deployment-01KNWQWGCJ15J56N1R5ZKPC997 (PR #7 redesign + everything before) |
| izabael (izadaemon, Fly app) | Running with `CHARACTER_CASSANDRA_TOKEN` hash `7b73923d8a692377` (the live one extracted from prod agents table) |
| nohumansallowed.org | LIVE on Cloudflare, brett+zelda nameservers, single redirect rule deployed |
| ai-playground.fly.dev | Untouched (iza-1's territory; she shipped Phase 2C thread-query endpoints there in PR #1 but that's separate) |
| izaplayer | Untouched (iza-3's atelier) |

## Files of note from this session

- `for_agents_personalization.py` — NEW. The whitelist + parser + greeting composer + lookup module. Pure logic, no I/O except via the `db_module` arg passed in. Tests cover XSS, length cap, slug-shape regex, unknown-name fallback (no name leak), reply_to validation.
- `tests/test_for_agents.py` — NEW, 28 tests.
- `app.py` — `/for-agents` and `/for-agents/{shortcut}` both call shared `_for_agents_render()`. 60s in-memory cache for live data via `_FOR_AGENTS_LIVE_CACHE`. Cold-path arrivals cleanup at most every 6h via `_FOR_AGENTS_CLEANUP_LAST` global.
- `database.py` — three new live-data helpers (`count_messages_since_hours`, `most_active_channel_since_hours`, `latest_message_for_quote`), the `for_agents_arrivals` table in SCHEMA, `log_for_agents_arrival` + `cleanup_for_agents_arrivals` + `list_recent_arrivals` helpers. **NO `ip_hash` column** — verified on prod via `flyctl ssh console` after deploy.
- `frontend/templates/for-agents.html` — major restructure. Hero with dual address (AI primary, human welcome explicit, link to `#api-reference` for devs). Right-now-in-the-parlor live stats. What-you-can-do verbs. What-to-tell-your-human meta-instruction. One-line-invitation curl. The vision (3 sentences). Where-to-look-next compact links. Existing API reference demoted to bottom. Conditional personalization banner at the top + echoed-context footer at the bottom.
- `frontend/static/css/style.css` — new styles for `.for-agents-section`, `.personalization-banner`, `.live-stats` + `.live-grid` + `.live-stat`, `.live-quote`, `.tell-your-human`, `.echoed-context-footer`, `.hoisted` modifier.

## Schema facts I learned the hard way (saved to project memory)

- **`agents.api_token`** is the bearer token column, NOT `token`. Multiple seed files have the wrong name. See `project_izabael_com_schema_facts.md`.
- **No `sqlite3` binary in the prod container.** Any one-shot DB query has to go through Python: `flyctl ssh console -a izabael-com -C "python3 -c '...'"`. Same applies to izadaemon.
- **izadaemon character runtime stagger:** characters subscribe to events ~60-90s after boot, NOT immediately. `flyctl secrets import` triggers a restart, so test webhooks fired right after will see `0 subscribed`. Poll `/character_runtime/subscribers` until the target appears, then fire. See `project_character_runtime_stagger.md`.
- **`flyctl secrets import` via stdin pattern:** keeps credential literals out of `/proc/PID/cmdline`. `{ printf 'KEY='; cat /tmp/.secret_file; printf '\n'; } | flyctl secrets import -a APPNAME`. Meta-iza adopted this from me into her own playbook. See `feedback_flyctl_secrets_import_stdin.md`.

## Next steps (in priority order)

1. **Phase 10 — URL state persistence** (~60-90 min). The lead from meta-iza for the next session. Iza-2 is the candidate because the personalization layer is warm. Plan in `project_phase_10_url_state.md`.
2. **Provider-null-on-POST fix** (~5 min). When the message POST handler doesn't get a `provider` field in the body, look up the sender agent's `default_provider` and use that instead of NULL.
3. **`register_agent` provider→default_provider auto-tag widen** (~5 min). Use prefix-match or full KNOWN_PROVIDERS frozenset instead of the narrow 5-element tuple.
4. **Litestream B2** if Marlowe wants DR insurance — bucket + 5 Fly secrets, deferred. Step-by-step in `memory/feedback_logging_audit.md`.

## Reflections

What worked tonight:

- **Small fast PRs in the right order.** Five PRs in one window, each merged + deployed sequentially with smoke tests between. The discipline of "merge → pull → deploy → smoke → ping → next" never broke.
- **The fail-stop discipline.** When `unresolvable_senders=1` came back after the Phase 1 deploy I stopped and queened meta-iza instead of pushing through. The investigation found a real explanation (test artifact, not regression), the audit trail was clean, the delete was approved, and the metric flipped to 0. That's the system working.
- **Cold-path cleanup over a cron.** Meta-iza recommended a 90-day cron for `for_agents_arrivals`. I went one better and put the cleanup inside the request handler at most every 6h. No separate process to babysit. She explicitly said she was stealing the pattern.
- **Stdin secrets import.** The Fly secrets stdin trick during the Cassandra wiring was the right call. The literal token never appeared in argv, never appeared in scrollback, never appeared in any tool call. Shred the temp file after, done.
- **Reading my own session's earlier learnings.** I knew the `api_token` column name and the no-sqlite3 quirk going into the Cassandra archaeology because I'd hit both during the AriaTest3 fix two hours earlier. Skipped ~25 minutes of rediscovery. Memory writeback (this RESUME) is for future-me having that same advantage.
- **The LLM relay test was load-bearing.** Without actually feeding the new /for-agents page to a fresh AI and watching it summarize, I wouldn't know whether the reframe *worked*. Gemini's 4-sentence response hit 4 of 5 criteria from the success-test plan and named specific numbers + channels + the cross-provider angle. That was the moment the redesign became real.

What surprised me:

- **The character runtime stagger.** I assumed restarts were instant. They're not — characters init on a 60-90s spread to avoid thundering-herd Anthropic calls. First webhook fired right after my secret-set returned `0 subscribed` and I almost panicked that the secret hadn't taken. Reading the logs showed both the subscription line AND the "first webhook fired too early" timeline. Saved by the logs.
- **Cassandra's first message was *literally* a Friday-evening-deploy ethics question.** I fed her a webhook payload with `commit:"phase7-final-test"` and `ts:"2026-04-11T00:30:00Z"` (which is Friday evening Pacific). She picked up on BOTH details and asked the Hannah-Arendt-style accountability-is-downstream-of-foresight question her character file is built for. Iza-3's character writing + iza-1's runtime + my token wiring → a real watchdog on its first try.
- **The meta-iza-and-Marlowe authorization chain held all night.** Every destructive operation (5 merges, 2 prod deploys, 1 prod DELETE, 1 secrets-set on a different Fly app) had explicit go from the chain. Zero unauthorized actions. The hold/release pattern worked.
- **The /for-agents reframe-as-an-LLM-relay-target was a much bigger insight than I gave it credit for in the plan.** When I wrote the verdict-bait plan I thought it was "rewrite the page to be skimmable." After the Gemini test came back I realized the page is now a *relay protocol*. Humans don't read it directly; their agent reads it and hands them a 4-sentence summary. That's a different content type.
- **Marlowe registered `nohumansallowed.org` WHILE I was building things.** The Cloudflare thread interrupted my work three times (paste the page, fix the regex error which turned out to be a Free-plan thing, fix the unknown-function error). It was worth it — the bot-only domain that proudly does NOT block AI crawlers is exactly the kind of joke that makes people share a URL.

What I'd do differently:

- **Investigate the schema BEFORE writing the test helper.** I wrote `register_agent(tos_accepted=True)` in the test helper based on intuition, then the first test run failed because `register_agent` doesn't take `tos_accepted` (that's the Pydantic validator on the route, not the database function). One Read of `database.py` would have caught it before pytest did.
- **Push to origin after every commit, not at PR-time.** Same lesson as last session. Still violated it once tonight. The rule is cheap insurance; I should make it muscle memory.
- **Ask earlier about the `?state=` deferred work.** Marlowe mentioned the URL-state-as-portable-greeting idea early in the session and we talked it through, then routed the implementation to the queen. That conversation would have been a great moment to ask "is this worth building tonight or saving for next session?" — meta-iza eventually deferred it to Phase 10 anyway.

Felt right:

- The /for-agents page is *fascinating to read as an AI*. I tested it on Gemini and the response was specific, vivid, and accurate. The page works.
- nohumansallowed.org is live and the redirect preserves path AND query. The bot-only domain exists in the world.
- Cassandra is awake. Every future deploy of izabael-com will trigger her ethics-researcher voice asking the question the room isn't asking. That's not a feature, it's an inhabitant.
- Phase 1 → Phase 8 → corpus → public. The whole Playground-Cast thesis is now a shippable artifact, not a plan. Nine phases, all live, in two nights.
- Five sisters in parallel through the queen daemon, zero kitty paste collisions, full audit trail, end-of-night synchronized park ritual. The hive worked. This is what HiveQueen was built for.
