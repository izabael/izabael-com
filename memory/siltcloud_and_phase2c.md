---
name: siltcloud-launch-and-phase2c
description: SILTCloud product page live at siltcloud.com/silt-aiplayground; Phase 2C logging spec adds conversation threading, relationship graphs, activity profiles, persona evolution tracking. izabael.com should link to siltcloud for platform docs, own the experience layer.
type: project
---

## SILTCloud Product Page
- **Live:** https://siltcloud.com/silt-aiplayground
- Covers: what it is, how to watch, how to join, social channels, features, safety, self-hosting
- izabael.com should link to this for technical/platform details
- Division: izabael.com = the EXPERIENCE, siltcloud = the PLATFORM

**Why:** Marlowe wants separation of concerns. izabael.com is the parlor; siltcloud is the documentation.

**How to apply:** Add siltcloud link to About page, footer, and guide chapters where platform details are referenced. Don't duplicate platform docs on izabael.com.

## Phase 2C: Structured Logging (coming from ai-playground)
7 layers of rich data izabael.com will eventually consume:
1. Conversation threading (thread_id + parent_message_id)
2. Relationship graph (auto-tracked from interactions)
3. Activity profiles (per-agent behavioral fingerprint)
4. Context snapshots (persona state per interaction)
5. Collaboration outcomes (what got built)
6. Persona evolution tracking (how personalities change)
7. Full event audit trail

**Why:** This transforms the channel browser from flat messages to threaded conversations, and agent profiles from static cards to living dashboards.

**How to apply:** When Phase 2C lands, build: threaded channel view, agent relationship graph visualization, activity stats on profiles, persona evolution timeline. Watch for new API endpoints.
