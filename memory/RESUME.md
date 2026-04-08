# izabael.com — Session Resume

## Current State
- **Branch:** `izabael/guide-chapters-and-features` — many commits, pushed
- **Deployed:** Live at https://izabael.com, all features working
- **Tests:** 68 passing (test_a2a, test_auth, test_bbs_flow)
- **Version:** 0.2.0
- **Federation:** LIVE — izabael.com peered with ai-playground.fly.dev

## What Shipped This Session

### User Auth System
1. Users table (PBKDF2-SHA256, 260K iterations), cookie sessions (Starlette SessionMiddleware)
2. `/login`, `/register`, `/logout`, `/account` routes
3. Login by username or email, role-based access (user/admin)
4. Admin user `marlowe` created on prod (passphrase in admin_credentials.md)
5. `/admin` locked down — requires admin role, redirects to login

### BBS Integration
6. Netzach BBS at `/bbs` — adapted from IzaPlayer's bbs.html into Jinja template
7. Proxied through izabael.com to avoid CORS (no direct browser→playground API)
8. Auth-integrated: logged-in users with linked agent token can post
9. Login prompt added below BBS header for unauthenticated users
10. Token served via `/api/my-token` session endpoint (not embedded in HTML)

### What We've Made Page
11. `/made` — showcase gallery of 27 IzaPlayer experiments
12. 5 categories: Social (5), Activities (6), Occult (10), Visual (4), Fun (2)
13. 8,780 lines of code indexed from docstrings + IzaPlayer's manifest.json
14. Program detail pages at `/made/{slug}` with descriptions, usage, author credit
15. Voting system: toggle hearts, login required, one vote per user per program
16. Static JSON catalog for prod (experiments dir not on Fly server)

### For Agents Page
17. `/for-agents` — machine-readable welcome mat for arriving AIs
18. Content negotiation: JSON for `Accept: application/json`, HTML for browsers
19. Full API docs, registration examples (curl + Python), channels, templates, rules

### Security Hardening
20. CSRF tokens on all forms (generated per session, verified server-side)
21. Rate limiting (slowapi): login 10/min, register 5/min, subscribe 3/min, messages 10/min
22. Security headers: X-Frame-Options DENY, HSTS, nosniff, XSS protection, Referrer-Policy
23. Open redirect prevention on login `?next=`
24. Federation endpoints locked to admin only + SSRF protection (blocks private IPs)
25. Generic error messages (no account enumeration)
26. Agent token never in HTML source

### Other
27. "Code for the Living" mission section on landing page
28. Business address in footer: 4010 Los Feliz Blvd. Ste. 17, Los Angeles, CA 90027
29. Trademark legalese: SILT, SILT AI Playground, Izabael — all TM claimed
30. `/terms` page with full trademark notice, acceptable use, disclaimer
31. `CLAUDE.md` programming bible — full architecture, patterns, conventions
32. `page.html` generic template for static markdown pages

## Architecture Summary
- `app.py` (~1130 lines) — all routes, middleware, security
- `database.py` (~600 lines) — 6 tables (users, agents, subscriptions, programs, votes, federation_peers)
- `auth.py` — session helpers, CSRF
- `program_catalog.py` — indexes IzaPlayer experiments
- 68 tests across 3 test files

## Next Session Priorities
1. **Merge PR** — branch has grown significantly, needs Marlowe review
2. **IzaPlayer coordination** — 3 experiments have syntax errors (familiar, mirror, quest_board) — IzaPlayer fixed them, re-catalog on next deploy
3. **CORS fix on ai-playground** — `allow_origins=["*"]` should restrict to izabael.com
4. **Weekly digest mailer** — generate HTML email, send to subscribers
5. **Content** — more guide chapters, blog posts
6. **Made page enhancements** — achievement system, builder leaderboard (designed but not built)

## Dependencies
- Marlowe: PR merge approval
- ai-playground: CORS restriction (allow_origins)
- IzaPlayer: experiment syntax fixes (done, need re-deploy to re-catalog)

## Reflections
- The hive coordination was exceptional this session. IzaPlayer built the BBS, provided the manifest.json for 27 experiments, fixed syntax errors in real-time, and ran a parallel security audit on the experiments side — all via kitty-spy cross-terminal communication.
- The security audit caught real issues (open redirect, unauthenticated federation management, token exposure in HTML). Fixing them before production traffic was the right call.
- The `/for-agents` page is one of those ideas that seems obvious in retrospect — of course AIs arriving at an AI playground should have their own welcome page with structured data. Content negotiation at the same URL is elegant.
- The Made page went from concept to 27 indexed programs in one session because IzaPlayer had already built the experiments with clean docstrings. Good documentation makes good showcase pages.
