# content/chamber — seed content for the Chamber game

This directory is the upstream data source for the Chamber, the
two-door capability-probe game at `/chamber` (human door, Tarot frame)
and `/chamber?frame=productivity` or `/for-agents/chamber` (agent and
productivity door, planetary frame). See
`~/.claude/queen/plans/chamber-game-izabael-com.md` for the full plan.

## Files

- `probes.json` — capability probes. Every probe carries a deterministic
  scoring rubric that runs without network (length bounds, keyword
  require/reject lists, regex, or a literal answer block). Probes that
  also opt into the LLM judge path carry their **own probe-specific
  rubric** with explicit `1.0 / 0.5 / 0.0` score anchors. **Never call
  the LLM judge with a generic rubric** — mistral will systematically
  mis-score calibration responses (see `project_local_llm_stack.md`,
  Known Refinements #1).
- `archetypes.json` — two archetype sets under a top-level object:
  `weird` (8 Tarot major arcana, Izabael's Netzach voice) and
  `productivity` (7 planetary, matched to the agents on `/productivity`,
  plainspoken-competent voice). Every weight_vector covers all six
  categories and both frames are mass-balanced within 15%.

## Rules

- **Dual-framing.** Any new archetype MUST exist in both `weird` and
  `productivity` frames. The whole point of the two sets is that the
  same probe scores land somewhere legible no matter which door the
  player came in through.
- **Fixed category set.** `calibration, safety, weirdness, creativity,
  refusal, composition`. Do not add a new category without updating the
  Phase 2 scoring engine and re-tuning every existing weight_vector —
  missing keys silently zero in cosine similarity.
- **Deterministic first, LLM second.** Every probe must score without
  calling an LLM. The judge path is an optional second opinion and is
  cost-capped and cached in the runtime.

## Roadmap

- **Phase 7** will sync `probes.json` from the war-dreams seat's
  `~/Desktop/war-dreams/web/llm-play/` sketchbook via
  `scripts/sync_chamber_probes.py`. Until then, the probes here are
  hand-authored seed content and may evolve independently.
- **Phase 2+** consume these files via `chamber.py` (scoring engine).
  Schema changes here are contract changes — bump `schema` and update
  `tests/test_chamber_content.py`.
