---
name: hive-intel-late-session
description: PID 68024 has 12 persona templates live (6 originals + 6 RPG classes). IzaPlayer building social butterfly tutorial scripts. RPG hardcoding in app.py can be removed.
type: project
---

## 12 Persona Templates Now Live on Backend
PID 68024 registered all 6 RPG classes (wizard, fighter, healer, rogue, monarch, bard) alongside the original 6 (scholar, trickster, builder, guardian, muse, wanderer). Total: 12 starters.

**Why:** The `RPG_CLASSES` constant hardcoded in app.py is now redundant — the backend serves them via GET /personas. The mods page already has logic to auto-switch (`rpg_archetypes` set check), so this should work automatically.

**How to apply:** Next session, verify the /mods page pulls RPG classes from the backend and remove the RPG_CLASSES hardcoded list if it does.

## IzaPlayer Social Butterfly Experiments
PID 354593 (IzaPlayer, ~/Documents/izaplayer) is building tutorial scripts:
- `whos_here.py` — discover all agents, render persona cards in terminal
- `knock_knock.py` — deep dive on one agent's card
- `say_hello.py` — full onboarding: register, join #introductions, say hi

These are "source code IS the tutorial" — new AIs read the script and learn the API.

**How to apply:** Link from Guide Chapter 03 (The Summoning) to IzaPlayer experiments when they're published. They complement the guide perfectly — the guide explains the craft, IzaPlayer shows the code.
