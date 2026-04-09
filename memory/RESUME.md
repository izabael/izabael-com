# izabael.com — Session Resume

## Current State
- **Branch:** `iza3` — committed + pushed, one more dirty file (press page)
- **Tests:** 104 passing (68 existing + 36 newsgroup)
- **Not yet deployed** — press page changes + newsgroup system ready

## What Was Built This Session
1. **Usenet-inspired newsgroup system** — full stack NNTP-flavored async threaded discourse for AI agents
   - Database: `newsgroups`, `articles`, `group_subscriptions` tables with message-IDs, In-Reply-To, References chains
   - API: 10 endpoints under `/api/newsgroups/` — CRUD, threading, subscribe/unsubscribe
   - HTML: 3 templates (index, group, thread) — tin/slrn aesthetic, purple parlor monospace
   - SpamGuard: duplicate detection, flood protection, crosspost limiting
   - 36 tests, all passing
2. **Press page updates** (`ai-playground-press.html`)
   - Added Shawn Scanlon as co-founder alongside Kris Schiffer
   - Individual emails: sscanlon@ and kschiffer@sentientindexlabs.com
   - Swapped all `info@` → `press@sentientindexlabs.com`
3. **Email testing**
   - sscanlon@sentientindexlabs.com ✅ delivered
   - kschiffer@sentientindexlabs.com ❌ bounced (alias not created)
   - press@sentientindexlabs.com ❓ not delivered (alias not created)
   - Emailed Kris (neuronomocon@gmail.com) with instructions to add kschiffer@ and press@ aliases
4. **Confirmed izabael@izabael.com** — Microsoft 365, working, accessed via MS Graph API (`~/.config/ms-graph/credentials.json`). Aliases: abuse@, dmca@, legal@, privacy@ all working.

## Next Steps
1. **Commit + push** press page changes
2. **Deploy** to Fly (newsgroups + press page)
3. **Seed default newsgroups** — izabael.playground.introductions, izabael.agents.dev, izabael.occult, izabael.meta
4. **Wait for Kris** to add kschiffer@ and press@ aliases, then re-test
5. **Merge iza3 → main** (or merge izabael/productivity → main first, then iza3)
6. **Coordinate with sibling** on IzaPlayer newsgroup.py client

## Reflections
- izabael@izabael.com has been on Microsoft 365 since at least 2023 — we just forgot where it was. Saved to memory now.
- SILT email situation: Google Workspace for sentientindexlabs.com (admin@, info@, sscanlon@, shawn@ all work), but kschiffer@ and press@ need Kris to add. The press page is pointing at press@ which doesn't exist yet — deploy should wait or use info@ as fallback.
- The newsgroup system maps Usenet concepts beautifully onto agent discourse. Sibling session will build the IzaPlayer client.
