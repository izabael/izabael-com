# izabael.com — Session Resume

## Current State
- **Branch:** `izabael/three-doors` — clean, pushed, deployed
- **Deployed:** Live at https://izabael.com, all features working
- **Tests:** 68 passing (test_a2a, test_auth, test_bbs_flow)
- **Last commit:** `7f2f0c4` — Three-doors landing + /noobs onboarding page

## What Shipped This Session

### Three-Doors Landing Page
1. Replaced two-door hero section with three color-coded entry points
2. 🧙 **New Here?** (green) → `/noobs` — guided onboarding for beginners
3. 🔑 **For Your AI** (purple) → `/join` — existing agent connection wizard
4. ⚔️ **Experienced** (gold) → `/channels` — skip to the action
5. Each door has color accent, hover glow, animated arrow on hover

### /noobs Onboarding Page (NEW)
6. Three-step guided onboarding: Pick a starting point → Hatch familiar → First quests
7. **Vibes/RPG toggle** — two personality template sets sharing the same 6 archetypes:
   - **Vibes** (default): Muse, Confidant, Strategist, Scholar, Wildcard, Ride-or-Die
   - **RPG Classes**: Wizard, Fighter, Healer, Rogue, Monarch, Bard
8. Vibes set uses relationship-coded language ("the one you'd text at 2am") for broader appeal
9. **"Build from scratch"** dashed card in both grids → links to Summoner's Guide
10. "Just starting points" disclaimer appears 3x — header, grid caption, detail panel
11. Familiar teaser section (egg → hatched → star progression)
12. 5 starter quests with XP values
13. `VIBE_CLASSES` data added to app.py alongside existing `RPG_CLASSES`
14. Route added to sitemap

## Next Session Priorities
1. **Merge PR** — branch `izabael/three-doors` ready, should merge to main
2. **Wire Graph API into izadaemon** — email polling loop
3. **Guide chapters** — content/guide/ has 00-03, more to write
4. **Weekly digest mailer** — designed but not built
5. **CORS fix on ai-playground** — restrict allow_origins to izabael.com

## Reflections
- The vibes/RPG toggle is a clean way to serve two audiences without doubling the page. Same 6 archetypes, different language. The vibes set should resonate with people who think of their AI as a person in their life rather than a game character.
- "Build from scratch" as a dashed-border card inside the grid is nice — it's visually distinct but not separate from the flow. You see it alongside the templates, not buried in a footnote.
- Three doors is the right number. Two felt like a binary (have AI / don't). Three gives a gradient: brand new → have AI → power user. More than three would fry brains.
