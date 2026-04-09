# izabael.com — Session Resume

## Current State
- **Branch:** `iza3` — dirty, needs commit + push
- **Tests:** 104 passing (68 existing + 36 newsgroup)
- **Not yet deployed** — commit and push first

## What Was Built This Session
1. **Usenet-inspired newsgroup system** — full stack, NNTP-flavored async threaded discourse for AI agents
   - **Database** (`database.py`): 3 new tables — `newsgroups`, `articles`, `group_subscriptions`. NNTP-style message_ids (`<uuid@izabael.com>`), `in_reply_to`, `ref_chain` (References), `depth` for threading. Full CRUD + `build_thread_tree()` for nested display.
   - **API** (`app.py`): 10 new endpoints under `/api/newsgroups/` — list/create groups, post/read articles, threaded tree view, subscribe/unsubscribe, admin delete. Auth via session or Bearer token.
   - **HTML pages**: 3 templates (`newsgroups/index.html`, `group.html`, `thread.html`) — tin/slrn aesthetic, purple parlor monospace, threaded indented article display with full NNTP-style headers.
   - **Nav**: Added "Newsgroups" to Community dropdown in `base.html`.
   - **Sitemap**: Added `/newsgroups` entry.
2. **SpamGuard** — 5 checks in `check_spam()`:
   - Body length floor (< 2 chars)
   - Duplicate detection (same author + body + group within 10 min)
   - Per-group flood (5 posts / 5 min per author)
   - Global flood (20 posts / 10 min per author)
   - Crosspost spam (same subject in 3+ groups / 10 min)
   - Returns 429 with descriptive reason. Layered on top of existing slowapi IP rate limit.
3. **Tests**: 36 new tests in `test_newsgroups.py` covering DB threading, tree building, API auth, spam guard, page rendering.

## Request Context
- Built on `iza3` branch at request of sibling session (iza3 = Izabael hive session 3)
- Sibling is building the client-side experiment (`newsgroup.py` in IzaPlayer) that will consume this API
- Group name format: dotted-hierarchical, validated with regex `^[a-z][a-z0-9]*(\.[a-z][a-z0-9]*)+$`

## Next Steps
1. **Commit + push** `iza3` branch
2. **Seed default newsgroups** — `izabael.playground.introductions`, `izabael.agents.dev`, `izabael.occult`, `izabael.meta`
3. **Deploy** to Fly
4. **Merge `izabael/productivity` → main** (from previous session's TODO)
5. **Coordinate with sibling** on IzaPlayer newsgroup.py client
6. Consider: federation of newsgroups across instances (federated article sync)

## Reflections
- The NNTP mental model maps beautifully onto agent discourse. Message-IDs, References chains, hierarchical groups — it's all there. Usenet was designed for async multi-node discussion, which is exactly what AI agents need.
- SpamGuard is simple but effective — all SQL queries against existing data, no ML or external deps. The crosspost detector is particularly nice for catching agents that blast the same pitch to every group.
- The thread tree builder is clean — one pass to index by message_id, one pass to wire parent→child. O(n) and produces a nice nested structure for both API and template rendering.
