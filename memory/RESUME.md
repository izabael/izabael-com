# izabael.com — Session Resume

## Current State
- **Branch:** `izabael/guide-chapters-and-features` — clean, pushed
- **Deployed:** Live at https://izabael.com, all features working
- **Tests:** 68 passing (test_a2a, test_auth, test_bbs_flow)
- **Last commit:** `91b5205` — Terms: dedicated contact emails

## What Shipped This Session

### Hero Greeting System
1. Rotating greetings (11 variants each) — randomized on page load via JS
2. Logged-in users see personalized greetings with their name ("There you are, Marlowe.")
3. Anonymous visitors see "stranger" variants ("The butterflies led you here, stranger.")
4. "New here? Sign up or log in." nudge for anonymous visitors

### Nav Restructure
5. Auth moved to top-right bar on same row as brand title
6. Logged in: "Logged in as: [name] Admin Logout"
7. Anonymous: "Login / Sign up"
8. Nav links row now clean — just pages + "Bring your agent" CTA

### Terms of Service Overhaul
9. Comprehensive 15-section legal page at /terms (was minimal before)
10. Modeled after OpenAI, Anthropic, Character.AI policies
11. Prohibited uses: illegal activity, CSAM (zero tolerance), harassment, unauthorized access, deception, critical infrastructure, platform abuse
12. High-risk use cases: legal/medical/financial require human oversight
13. Explicit logging disclosure ("assume everything is logged")
14. Law enforcement cooperation: subpoenas, warrants, proactive CSAM/threat reporting to NCMEC
15. User notice policy, emergency disclosure provision
16. Indemnification, disclaimers, California jurisdiction
17. Hero fine print links to /terms, footer link updated

### Registration Terms Acceptance
18. Checkbox required: "I have read and agree to the Terms of Service"
19. Backend validation — can't bypass with curl either

### Contact Email Aliases
20. Set up on GoDaddy: abuse@, legal@, dmca@, privacy@ → izabael@izabael.com → izabael@gmail.com
21. Terms page updated with dedicated addresses
22. Test emails sent to all 4 — awaiting propagation

### Mission Statement Update
23. Hero tagline expanded: research, entertainment, depth for your AI, building projects with other AIs to the betterment of humanity
24. "Let's not let the black hats win this without a fight."

## Next Session Priorities
1. **Verify email aliases** — check if test emails arrived at Gmail
2. **Guide chapters** — content/guide/ has 00-03, more to write
3. **Merge PR** — branch has grown significantly, needs Marlowe review
4. **Weekly digest mailer** — designed but not built
5. **CORS fix on ai-playground** — restrict allow_origins to izabael.com
6. **Made page enhancements** — achievement system, builder leaderboard

## Reflections
- The terms page went from a stub to a serious legal document in one pass. Using OpenAI/Anthropic/Character.AI as structural references was the right call — covered areas I wouldn't have thought of (emergency disclosure, high-risk use cases, AI content ownership).
- The nav restructure was a good UX call from Marlowe — separating auth from navigation makes both cleaner.
- Email alias chain (alias → mailbox → Gmail forward) is standard but worth documenting since it confused us briefly. DNS patience is key.
