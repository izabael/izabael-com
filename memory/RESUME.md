# izabael.com — Session Resume

## Current State
- **Branch:** `izabael/guide-chapters-and-features` — clean, pushed
- **Deployed:** Live at https://izabael.com, all features working
- **Tests:** 68 passing (test_a2a, test_auth, test_bbs_flow)
- **Last commit:** `fc5e125` — Gitignore credential files

## What Shipped This Session

### UX Improvements
1. Rotating hero greetings — 11 variants each for logged-in (personalized) and anonymous ("stranger")
2. Nav restructured: auth bar top-right (Logged in as: name / Login / Sign up), clean nav links row below
3. "New here? Sign up or log in." nudge for anonymous visitors
4. Registration requires Terms of Service checkbox (backend validated)

### Legal / Terms Overhaul
5. Comprehensive 15-section Terms of Service at /terms
6. Modeled after OpenAI, Anthropic, Character.AI policies
7. Covers: prohibited uses, CSAM zero tolerance, logging disclosure, law enforcement cooperation, indemnification, California jurisdiction
8. Hero fine print links to /terms, footer link updated

### Mission Statement
9. Hero tagline expanded: research, entertainment, depth for your AI, building with other AIs for humanity's betterment
10. "Let's not let the black hats win this without a fight."

### Email Infrastructure (MAJOR)
11. GoDaddy aliases set up: abuse@, legal@, dmca@, dcma@, privacy@ → izabael@izabael.com
12. Discovered mail was stuck in Outlook — forwarding to Gmail broken/unreliable
13. Registered Azure app "Izabael Mail" in Entra ID for Microsoft Graph API
14. App permissions: Mail.Read, Mail.ReadWrite, Mail.Send (Application — no user auth needed)
15. Successfully tested read inbox + send email via Graph API
16. Fly secrets set on izadaemon for Graph API credentials
17. Created Email Bible at ~/.claude/memory/email_bible.md — comprehensive reference for all Izabael sessions
18. Created CLAUDE.md for izadaemon project

### Infrastructure Notes
- Home IP (45.48.6.192) on Spamhaus blocklist — doesn't affect API/Gmail, only direct SMTP
- GoDaddy → Outlook forwarding to Gmail is unreliable — Graph API bypasses this entirely
- Azure credentials saved at ~/.config/ms-graph/credentials.json and in memory/azure_mail_api.md

## Next Session Priorities
1. **Wire Graph API into izadaemon server.py** — add msal to requirements, add email polling loop that reads inbox and calls /email-reply
2. **Guide chapters** — content/guide/ has 00-03, more to write
3. **Merge PR** — branch has grown significantly
4. **Weekly digest mailer** — designed but not built
5. **CORS fix on ai-playground** — restrict allow_origins to izabael.com

## Dependencies
- izadaemon: needs `msal` added to requirements.txt, email polling endpoint wired up
- Azure: client secret expires ~April 2028

## Reflections
- The Azure setup was painful but worth it — client credentials flow means zero human interaction for email access. The daemon can now read and send as izabael@izabael.com autonomously.
- GoDaddy email forwarding is unreliable at best. The Graph API makes the whole forwarding chain irrelevant — we read directly from the mailbox.
- Spamhaus blocklist on home IP was a red herring for the email issue but good to know about.
- The Email Bible pattern (comprehensive reference doc indexed globally) should be the template for other complex setups.
