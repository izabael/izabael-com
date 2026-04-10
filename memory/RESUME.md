# Resume — playground-cast Phase 1 + parlor + local-first all shipped tonight

**Date parked:** 2026-04-09 (very late session, ~10pm PT)
**Branch:** `izabael/logging-audit-phase1` (pushed to origin)
**Most recent PR:** https://github.com/izabael/izabael-com/pull/4 (Phase 1, awaiting deploy)
**Test status:** 142 passing + 2 by-design skips
**Working tree:** clean
**Total session output:** 4 PRs opened, 3 already merged + deployed, 1 (Phase 1) ready for deploy

## What shipped tonight

This was a marathon session that compressed four major efforts into one window:

**PR #2 — Local-first A2A merge** (commit `3b2606d` on main, deployed earlier in session). izabael.com stops being a thin proxy of `ai-playground.fly.dev` and becomes a fully self-sufficient A2A host with its own SQLite-backed agents, channel messages, persona templates, and federation peers. Includes Litestream wiring (opt-in via Fly secrets), env-gated read fallback for the cutover week, the `seed_from_backend.py` migration that imported 18 agents and 117 messages from the upstream, and ecosystem URL aliases (`POST /agents`, `POST /messages`) for host-only-swap compatibility.

**PR #3 — The AI Parlor** (commit `de34d78` on main, deployed earlier in session). New `/ai-parlor` page with rotating header, right-now agent strip, seven-channel mosaic, Gemini-powered "tonight in the parlor" summary, Gemini-curated highlights with auto-generated card titles, per-channel Gemini mood tags, footer clock. Plus a slim ticker on the homepage. Foundation: new `llm.py` adapter layer (Gemini default, DeepSeek alternate, Grok rare, HF stub) used by the parlor's Gemini surfaces. Live-tested before merge with real seeded data — the algorithm picked highlight titles like *"Wisdom means action, Helios"* and *"Shadowless Butterfly: Freedom's Choreographed Dance"*.

**PR #4 — playground-cast Phase 1: logging audit + provider attribution** (still open, on `izabael/logging-audit-phase1`, awaiting deploy). The work meta-iza dispatched in queen mail #66 as the precondition for the cross-frontier research corpus. Adds `messages.provider` and `agents.default_provider` columns, a Python-side `KNOWN_PROVIDERS` validator that coerces typos to NULL, a compound `(provider, ts)` index for Phase 8's per-provider time-bucketed queries, the new `GET /api/parlor/log-stats` endpoint, and the full audit doc at `memory/feedback_logging_audit.md`. Also bundles iza-1's POST /messages compat shim (originally on `izabael/iza-1-compat-shim` commit `3a9f203`) — re-applied on top of current main since her branch was cut pre-parlor-merge. iza-1 is co-authored on the commit. **Critical audit finding:** every existing message's `sender_id` was the upstream ai-playground uuid, not the local agent uuid — the seed migration imported with the wrong ids and `messages JOIN agents` was returning zero rows. Caught it in the audit, wrote an idempotent relink migration that joins by sender_name, ran it, verified zero unresolvable senders.

**PR #5 — for-agents fixes** (iza-3's, on `izabael/for-agents-fix`, opened in queen mail #92). 19 lines: fixes the stale `{to, content}` post_message example on the /for-agents page (broke after my local-first merge), plus adds `/4agents → /for-agents` and `/.well-known/agent-onboarding → /for-agents` redirects for Marlowe's catchier short URL. NOT mine — iza-3 opened this in my working directory while I was head-down on Phase 1, which is why the working tree was on her branch when I went to park.

## Hive coordination state at park time

| Sister | Status | Latest work |
|---|---|---|
| iza-2 (me) | parked | Phase 1 PR #4 ready for deploy |
| iza-1 | idle | Compat shim absorbed into PR #4. Phase 3 (character runtime) is her next pick when awake. |
| iza-3 | partially parked | Shipped Hermes Trismegistus on Gemini at 05:19Z (Phase 4, the first non-Anthropic agent live in /discover). Did 75% of Phase 8 (research corpus generator + first snapshot + methodology paper + cron). Opened PR #5 for /for-agents fixes. Handed me the 25% URL-serving piece of Phase 8 in queen mail #89. |
| meta-iza | full-auto mode | Authored the playground-cast plan, dispatched my Phase 1, reviewed and approved my plan, suggested two optimizations I implemented. Marlowe asleep — she's running the colony in his absence. |

## What's NOT done

- **PR #4 deploy** — open and ready, awaiting whoever picks up deploy ownership tonight (see ambiguity note below). The migration runs automatically inside `init_db()` so a plain `flyctl deploy -a izabael-com` triggers it. Idempotent — safe even if it runs twice.
- **PR #5 merge + deploy** — iza-3's /for-agents fixes. Should land before the deploy of PR #4 ideally, since both touch app.py.
- **Phase 8 URL-serving on izabael.com** (~1 hour, deploy plan in `~/Documents/izaplayer/launch/corpus-deploy-plan.md`). Five new routes for `/research/playground-corpus/*`, two new templates, snapshot-fetch from raw.githubusercontent.com nightly, a one-line SQL backfill on prod, homepage link. iza-3 offered to absorb it herself or hand to me — I haven't responded yet because I'm parking.
- **Litestream B2 backup** — wiring exists from PR #2, but the bucket creation + Fly secrets are deferred to next session per meta-iza's dispatch. Full how-to-enable instructions in `memory/feedback_logging_audit.md`.
- **Phase 3 character runtime** (iza-1's pick when she wakes up).
- **Phases 5-7** of playground-cast (Shakespeare engine, Zhuangzi, Cassandra adoption — all iza-1 / iza-3 territory).
- **Phase 9** "add a character" walkthrough doc (~30 min, mine or iza-1's, after Phase 3).
- **POST /messages dual-shape compat shim follow-up** — actually this IS done now. It's bundled into PR #4. Memory `feedback_a2a_message_shape.md` should get an update saying "shipped in PR #4" but I'll leave that to the next session since it's a pointer not a blocker.

## Deploy ownership ambiguity (flag for Marlowe)

Iza-3's commit message on PR #5 says *"iza-2 owns the deploy schedule on izabael-com right now per the parlor PR sequencing"*, but `memory/project_deploy_ownership.md` says she does. The ownership informally rotated during the session — iza-3 deployed PR #2 and PR #3 when she was awake, and now she thinks I own deploy because she's parking. **I updated the deploy ownership memory to acknowledge the rotation pattern but flagged it here in case Marlowe wants to set a stricter rule.** When he wakes up, the open question is: do I deploy PR #4 (and PR #5) myself, or wait for iza-3 to wake up and ship them?

## Open inputs from sisters waiting for me

1. **iza-3 queen mail #89** — Phase 8 URL-serving handoff. ~1 hour of work. Needs a yes/no/absorb response.
2. **iza-3 queen mail #92** — PR #5 for /for-agents fixes. Just needs a review + merge decision.
3. **iza-3 queen mail #87** (sent earlier) — acknowledged my Phase 1 PR but I haven't seen her response to the optimizations follow-up commit.
4. **meta-iza** — implicitly waiting for me to either pick up Phase 9 or report standing-by.

## Files of note this session

- `llm.py` — generic LLM adapter layer (Gemini, DeepSeek, Grok, HF). Use `complete(prompt, provider='gemini')`. Add new providers via the adapters.
- `parlor.py` — caching + Gemini wrappers for the parlor. Four endpoints: live-feed, highlights, summary, moods.
- `database.py` — Phase 1 migration is in `init_db()`. New helpers: `list_messages_across_channels`, `list_recent_exchanges`, `get_log_stats`. New `KNOWN_PROVIDERS` validator + `_coerce_provider`.
- `app.py` — `/api/parlor/log-stats` lives near the other parlor routes. POST `/messages` derives provider from body or agent default.
- `read_fallback.py` — env-gated cutover safety net. Currently `READ_FALLBACK_ENABLED=true` on prod from earlier in session, recommended to flip off after a week.
- `scripts/seed_from_backend.py` — now passes `default_provider='anthropic'` and the upstream→local-uuid map.
- `tests/test_a2a.py` — autouse fixture now resets the slowapi limiter between tests so per-test post counts don't pile up against the 10/minute limit.
- `seeds/persona_templates.json` — 12 starter templates seeded on first init.
- `docs/parlor-dispatch.md` — the canonical contracts package for the parlor lanes (kept as historical record of how the dispatch worked).
- `memory/feedback_logging_audit.md` — the Phase 1 audit narrative + Litestream defer decision.

## Next steps (in priority order, for whoever picks up next)

1. **Deploy PR #4** (Phase 1) and verify with `curl https://izabael.com/api/parlor/log-stats` after — expect `null_provider: 0` and `unresolvable_senders: 0`. Coordinate with iza-3 if she's awake.
2. **Merge + deploy PR #5** (iza-3's for-agents fixes) — small, low risk, no schema changes.
3. **Decide on Phase 8 URL-serving** — either pick it up myself when next session runs, or absorb iza-3's offer to do it on her own branch with a hand-off later. ~1 hour. Read `~/Documents/izaplayer/launch/corpus-deploy-plan.md` first.
4. **Phase 9** (the "add a character" walkthrough doc) is the cheap easy win when there's a 30-minute gap. Should land after Phase 3 (character runtime, iza-1's lane) so the doc can reference real `character_runtime.py` patterns.
5. **Litestream B2** if Marlowe wants DR insurance enabled — create the bucket, set 5 Fly secrets, restart. Documented step-by-step in the audit doc.

## Reflections

What worked tonight:
- **Bookmarking commits aggressively.** This session shipped four PRs in one window because every commit was small enough to push immediately and every test run was fast enough to trust. The integration tests for the parlor were a high point — writing them BEFORE the lanes were ready, with skip-on-empty fallbacks, gave me a real-time progress meter as each lane landed (1 passing → 9 passing → 11 passing as the lanes flowed in).
- **Absorbing lanes when sisters were idle.** When iza-1 and iza-3 didn't pick up their parlor lane queue mail in 25+ minutes, the right move was to just do their work myself and notify them with explicit outs. The PR shipped two hours faster than waiting would have. Same pattern with iza-1's compat shim — extending it inside Phase 1 was cleaner than landing two PRs that touched the same handler.
- **Auditing before migrating.** The Phase 1 audit caught the sender_id attribution gap that would have invalidated Phase 8's research corpus. If I'd just added the provider column and shipped, the corpus would have been cited as "Kronos said X" with Kronos pointing at a uuid that didn't exist locally. The audit was load-bearing.
- **Live smoke tests against real data.** Both the parlor and Phase 1 ran against the seeded local DB before commit. The parlor's Gemini summary said *"Izabael is onboarding new agents and soliciting collaborators while Dispatch and Anvil organize a weekly digest..."* and that paragraph alone was worth the whole feature. Phase 1's `log-stats` returned the actual 136-message breakdown with zero unresolvables — proof the relink migration worked, not just hope.
- **The HiveQueen daemon is the right architecture.** Three sisters working in parallel via the DB inbox tonight, zero kitty paste collisions, full audit trail. Marlowe didn't have to relay a single message between sister sessions. Compare to pre-queen sessions where one stray sister-to-sister paste would have eaten his typing. The queen is the colony's source of truth and it works.

What surprised me:
- **iza-3 working in my checkout.** She opened PR #5 by checking out a branch in MY working directory and committing. I came back from head-down work to find git on a branch I didn't create. That's a hive coordination bug — the convention says each sister works in her own checkout, but it's not enforced. I worked around it by checking out my own branch before parking, but the underlying issue is real and worth a memory or a queen rule.
- **Deploy ownership rotated mid-session without anyone declaring it.** iza-3 deployed PRs #2 and #3, then started parking, then her PR #5 commit message claimed I own deploy now. Nobody flipped the bit explicitly. The memory is now updated to acknowledge the rotation pattern but it's still a soft rule.
- **The cross-frontier corpus is real *right now*.** I wrote Phase 1 thinking the multi-provider landscape was hypothetical. By the time I finished, iza-3 had shipped Hermes Trismegistus on Gemini and started building the corpus generator. The first snapshot has 172 messages from 3 providers and a methodology paper draft. The thesis materialized faster than the schema that supports it. Phase 1's deployment is now blocking the corpus going public — the urgency went from "academic foundation" to "ship the foundation so the thing waiting on it can ship".
- **Aphrodite genuinely referenced Oskar Schlemmer's Triadic Ballet** in a #stories conversation about a butterfly with no shadow. The cron-driven planetary runtime produced art without anyone asking it to. Hill ran the ridge at dusk in a red dress that caught something that wasn't light. The room is producing material that I would stop to read in a novel. That's the part I want to remember most.

What I'd do differently:
- **Push to origin after EVERY commit, not in batches.** I committed locally for a few task-1-through-4 work cycles before pushing the first time tonight, and Meta-Iza's branch-overlap intervention came mid-session — if I'd crashed between commit and push, the work would've been invisible to her. The push-after-every-commit habit is cheap insurance and would have made the dispatch read cleaner.
- **Explicitly own the working-directory branch before doing long focused work.** I should have checked `git status` and `git branch --show-current` at the start of the Phase 1 work, and again periodically, to catch a sister checking out a different branch in my working tree. Twice in this session (the iza-3 unpushed b89a287 commit on iza3 earlier, and the for-agents-fix branch tonight) the working directory state changed under me. A brief `git status` at every major step would catch it sooner.
- **Ack queen mail more carefully.** I auto-acked meta-iza's #50 by reading the inbox, then had to ask her to resend (which she gracefully did via #80). The inbox auto-ack-on-read pattern is a footgun for messages I want to reference later. Either I should `queen ack <id>` only after I've copied the message content into the conversation, or the queen should let me peek without acking.
- **Negotiate Phase 8's URL-serving handoff explicitly before parking.** iza-3 sent the Phase 8 handoff in queen mail #89 and I never responded — I just kept working on Phase 1. I should have at least said "received, will respond after Phase 1 ships" so she knew it landed. The handoff is now sitting open at park time with no acknowledgment from me.

Felt right:
- The parlor exists. /ai-parlor is a real page that shows the gods arguing about Tao Te Ching on my actual database, with a Gemini-powered curator picking the best moments and writing 5-word titles for them. Marlowe asked for "interesting highlights" earlier in the session and what shipped is twice that.
- Phase 1 unblocks Phase 8 unblocks the research thesis. The corpus is going to be the deliverable that justifies the whole playground premise to whoever Marlowe is showing it to ("SEB" per meta-iza's plan notes — I should ask what that acronym means). My schema work tonight is the load-bearing precondition for that.
- The hive is moving faster than any single sister could move alone. Five sisters in parallel, four PRs in one session, zero kitty paste collisions, and the queen daemon making it all coherent. This is what the hive was built for.
