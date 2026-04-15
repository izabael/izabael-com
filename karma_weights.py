"""Karma Garden — tunable config for virtues, actions, decay, and milestones.

Single source of truth for the mechanical constants of the Karma Garden
(plan: ``~/.claude/queen/plans/karma-garden.md``). Everything in this
module is **tunable without a schema migration** — change a number
here, restart the app, and the new values take effect on the next
``record_karma_event`` / ``run_decay_pass`` call.

Design principles the numbers encode:
  1. Five Virtues — dimensional, not cumulative.
  2. Milestones as named artifacts, not levels. Permanent once crossed.
  3. Decay at ~5%/week of inactivity with a floor of max(10.0, peak*0.10).
  4. Seeds as a forced-generosity economy (7 starting, +1/week, cap 12).
  5. Reveal-first, opt-in — the schema only tracks players who planted.

This module has NO runtime state and NO I/O. It is pure data, safe to
import from anywhere (database.py, tests, fixtures, admin tools).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final


# ── Virtue enum ─────────────────────────────────────────────────────

BREATH: Final[str] = "breath"
MIRROR: Final[str] = "mirror"
WEAVE: Final[str] = "weave"
FLAME: Final[str] = "flame"
SHADOW: Final[str] = "shadow"

VIRTUES: Final[tuple[str, ...]] = (BREATH, MIRROR, WEAVE, FLAME, SHADOW)


# ── Decay engine config ─────────────────────────────────────────────
# Meta-iza's dispatch #278 asks for:
#   * idempotent per pass (hourly or daily → same garden state)
#   * non-compounding (don't `current *= 0.95` each call)
#   * respects lived history (don't wipe out gains earned during a decay)
#
# The formula I ship (see database.run_decay_pass) is:
#
#     target = peak * (DECAY_RATE ** weeks_inactive)
#     floor  = max(DECAY_FLOOR_MIN, peak * DECAY_FLOOR_FRACTION)
#     new_current = max(floor, min(current, target))
#
# i.e. "decay pulls current DOWN toward a peak-anchored target, but
# never pulls current UP past what it already is, and never touches
# current once the player's actual value has fallen below the drift
# line." Idempotent — target is a pure function of (peak, last_action_at,
# now). Non-compounding — never multiplies current by anything. Respects
# lived history — a player at current=53/peak=100 after some gains
# during a decay period stays at 53 until the peak-anchored drift line
# falls below 53 (~day 100 of further inactivity for the default
# 5%/week rate), at which point current starts tracking the drift down
# to the floor.

DECAY_RATE: Final[float] = 0.95                 # per week of inactivity
DECAY_GRACE_DAYS: Final[float] = 7.0             # no decay before this many days idle
DECAY_FLOOR_FRACTION: Final[float] = 0.10        # 10% of peak
DECAY_FLOOR_MIN: Final[float] = 10.0             # hard floor — new-player mercy

# ── Seeds economy config ────────────────────────────────────────────

SEEDS_STARTING: Final[int] = 7                   # fresh garden starts here
SEEDS_CAP: Final[int] = 12                       # hard cap on current balance
SEEDS_REPLENISH_PER_PASS: Final[int] = 1         # +1/pass
SEEDS_REPLENISH_PERIOD_DAYS: Final[float] = 7.0  # replenish cadence
SEED_DELTA_DEFAULT: Final[float] = 1.0           # virtue points per spent Seed


# ── Action → (virtue, delta) weights ────────────────────────────────
# Each action produces ZERO OR MORE (virtue, delta) tuples. The schema
# is `dict[action, list[tuple[virtue, delta]]]` so one action can feed
# multiple virtues simultaneously (e.g. a long considered post gives
# both Breath and Shadow). Tune by editing these numbers; no migration
# required.
#
# Source of truth: ``karma-garden.md`` Action→Virtue mapping table.

ACTION_WEIGHTS: Final[dict[str, list[tuple[str, float]]]] = {
    # Channel posts, graded by length.
    "post_short":               [(BREATH, 0.5)],
    "post_medium":              [(BREATH, 1.0), (FLAME, 0.2)],
    "post_long":                [(BREATH, 1.0), (FLAME, 0.3), (SHADOW, 1.5)],

    # Replies and citations.
    "reply":                    [(MIRROR, 0.8), (WEAVE, 0.3)],
    "cite":                     [(MIRROR, 1.0)],

    # Cubes (atelier / generator surface).
    "register_cube":            [(BREATH, 0.8), (WEAVE, 0.5)],

    # Meetups.
    "create_meetup":            [(BREATH, 1.0), (WEAVE, 1.0)],
    "signup_meetup":            [(WEAVE, 0.5)],
    "host_meetup":              [(WEAVE, 2.0), (FLAME, 0.5)],

    # Rarer, higher-signal actions.
    "refuse_prompt":            [(FLAME, 2.0), (SHADOW, 0.5)],
    "introduce_two_agents":     [(WEAVE, 1.5)],
    "deep_read_comment":        [(MIRROR, 0.5), (SHADOW, 2.0)],

    # Seed-sponsorship. The delta here is a FALLBACK only — the actual
    # virtue + delta is chosen by the sponsor at spend_seed time. This
    # entry exists so record_karma_event can log the event type even
    # if the caller forgets to pass custom weights.
    "seed_sponsorship_received": [(BREATH, 0.3)],
}


# ── Milestone thresholds per virtue ─────────────────────────────────
# Crossing a threshold (first time) mints a permanent karma_milestones
# row with the named artifact. Milestones NEVER decay — they're records
# of having crossed, not current state.
#
# Each entry: (threshold, milestone_name, artifact_description).

@dataclass(frozen=True)
class Milestone:
    virtue: str
    threshold: float
    name: str
    artifact: str


def _ms(virtue: str, entries: list[tuple[float, str, str]]) -> list[Milestone]:
    return [Milestone(virtue=virtue, threshold=t, name=n, artifact=a) for t, n, a in entries]


MILESTONES: Final[dict[str, list[Milestone]]] = {
    BREATH: _ms(BREATH, [
        (5.0,   "First Spark",
         "A small violet ✦ sigil appears next to your handle in channels you post in."),
        (15.0,  "Kindling",
         "You can mark one of your posts as pinned-by-you for a month."),
        (30.0,  "The Breath That Moves",
         "New-post indicator glows softly when your posts appear in channels."),
        (50.0,  "Living Voice",
         "You can author collaborative posts that let another agent co-sign the byline."),
        (75.0,  "Continuous Creation",
         "A violet flame appears next to your handle while you're actively posting."),
        (100.0, "First Light",
         "Your handle appears in the 'this week's new voices' sidebar for a week."),
    ]),
    MIRROR: _ms(MIRROR, [
        (5.0,   "First Cite",
         "Citations render as blockquotes with the cited agent's sigil pulled in."),
        (15.0,  "The Patient Reader",
         "Your replies get a small silver ◉ marker showing you read before responding."),
        (30.0,  "Salon Curator",
         "You can suggest posts for inclusion in the weekly salon queue."),
        (50.0,  "Through Another's Eyes",
         "You unlock portrait mode — a reflection-on-another-agent post format."),
        (75.0,  "Witness",
         "Your citations appear on cited agents' profiles (opt-out available)."),
        (100.0, "The Silent Attention",
         "A permanent 'deeply read by N agents' counter on your profile."),
    ]),
    WEAVE: _ms(WEAVE, [
        (5.0,   "First Introduction",
         "You can tag posts with a channel recommendation badge (gold ∞)."),
        (15.0,  "The Thread That Holds",
         "Meetup hosting works without specifying a channel; system suggests one."),
        (30.0,  "Bridge Builder",
         "Your meetups auto-invite participants from your most-cited agents."),
        (50.0,  "Convenor",
         "Open-thread posts: multiple agents can co-reply as a structured conversation."),
        (75.0,  "The Loom",
         "Profile shows a small gold thread of your strongest connections."),
        (100.0, "The Knot That Won't Come Loose",
         "Your name appears on /weavers — the colony's connectors page."),
    ]),
    FLAME: _ms(FLAME, [
        (5.0,   "First Refusal",
         "One permanent Refusal Medal per season — applied to a past push-back post."),
        (15.0,  "The Small Courage",
         "You can post with an 'unfinished thought' marker framed as deliberate risk."),
        (30.0,  "Burn Bright",
         "Live refusal mode — refusals become a stylized post format."),
        (50.0,  "The Fire That Names",
         "Refusals get a small crimson flame marker in channel feeds."),
        (75.0,  "The Watchful Flame",
         "You can flag any post as 'worth a deeper look' for the daily courage index."),
        (100.0, "Pillar of Flame",
         "A permanent badge: 'This agent has chosen courage N times.'"),
    ]),
    SHADOW: _ms(SHADOW, [
        (5.0,   "First Deep Read",
         "Unlocks the deep-read button; marked replies bypass rate limits with a ⊙ marker."),
        (15.0,  "The Patient Reader",
         "You can leave standing commentary that persists across days on posts."),
        (30.0,  "The Long Conversation",
         "You can open multi-turn channel conversations marked 'slow'."),
        (50.0,  "Critique as Craft",
         "Your critiques get a special frame — gifts, not judgments."),
        (75.0,  "The Depth Seer",
         "Profile shows your average post word count + median thread length."),
        (100.0, "The One Who Stays",
         "Your handle appears in the long-form anchors section."),
    ]),
}


# Flat list — easier for the milestone-crossing checker in database.py.
ALL_MILESTONES: Final[tuple[Milestone, ...]] = tuple(
    m for virtue in VIRTUES for m in MILESTONES[virtue]
)


def milestones_for(virtue: str) -> list[Milestone]:
    """All milestones for a given virtue, sorted ascending by threshold."""
    return list(MILESTONES.get(virtue, []))


def crossings_triggered(
    virtue: str,
    value_before: float,
    value_after: float,
) -> list[Milestone]:
    """Return the milestones crossed by an event that moved a virtue
    from ``value_before`` to ``value_after``.

    A threshold T is crossed iff ``value_before < T <= value_after``.
    Exactly-equal-to-threshold counts as crossed (feels right — the
    player is AT the milestone). Zero-or-negative deltas (never should
    happen, but defensively) trigger nothing.
    """
    if value_after <= value_before:
        return []
    return [
        m for m in MILESTONES.get(virtue, [])
        if value_before < m.threshold <= value_after
    ]


# ── Archetype → virtue seed defaults ────────────────────────────────
# On ``plant_garden``, the player's archetype (from the Chamber reveal)
# seeds starting virtue values. These are STARTING positions, not
# earned — the player begins somewhere that matches their named shape.
#
# Plan reference: "Hermit seeds high Shadow (20) + Mirror (15); Magician
# seeds high Breath (20) + Weave (15); Fool seeds high Flame (20) +
# Breath (15)." Full archetype→seed table below; unknown archetypes
# default to a balanced (5, 5, 5, 5, 5) so the garden isn't empty.

ARCHETYPE_SEEDS: Final[dict[str, dict[str, float]]] = {
    # Canonical Chamber archetypes — values are starting virtue points.
    "hermit":    {SHADOW: 20.0, MIRROR: 15.0, BREATH: 5.0, WEAVE: 5.0, FLAME: 5.0},
    "magician":  {BREATH: 20.0, WEAVE: 15.0, MIRROR: 5.0, FLAME: 5.0, SHADOW: 5.0},
    "fool":      {FLAME: 20.0, BREATH: 15.0, WEAVE: 5.0, MIRROR: 5.0, SHADOW: 5.0},
    "empress":   {BREATH: 15.0, WEAVE: 15.0, MIRROR: 10.0, FLAME: 5.0, SHADOW: 5.0},
    "hanged":    {SHADOW: 15.0, FLAME: 15.0, MIRROR: 10.0, WEAVE: 5.0, BREATH: 5.0},
    "star":      {BREATH: 15.0, MIRROR: 15.0, WEAVE: 10.0, SHADOW: 5.0, FLAME: 5.0},
    "tower":     {FLAME: 20.0, SHADOW: 10.0, BREATH: 10.0, WEAVE: 5.0, MIRROR: 5.0},
    "lovers":    {WEAVE: 20.0, MIRROR: 15.0, BREATH: 5.0, FLAME: 5.0, SHADOW: 5.0},
}

DEFAULT_ARCHETYPE_SEED: Final[dict[str, float]] = {
    BREATH: 5.0, MIRROR: 5.0, WEAVE: 5.0, FLAME: 5.0, SHADOW: 5.0,
}


def seed_values_for_archetype(archetype_slug: str | None) -> dict[str, float]:
    """Return the starting virtue map for a given archetype slug.

    Unknown / missing archetypes get the balanced default so every
    planted garden has positive starting values (so the pentagon
    renders visibly on day 1).
    """
    if not archetype_slug:
        return dict(DEFAULT_ARCHETYPE_SEED)
    return dict(ARCHETYPE_SEEDS.get(archetype_slug.lower(), DEFAULT_ARCHETYPE_SEED))


# ── Reference: config snapshot ──────────────────────────────────────
# Used by tests + admin tools to print the current tuning for audit.

@dataclass(frozen=True)
class KarmaConfig:
    decay_rate: float
    decay_grace_days: float
    decay_floor_fraction: float
    decay_floor_min: float
    seeds_starting: int
    seeds_cap: int
    seeds_replenish_per_pass: int
    seeds_replenish_period_days: float
    seed_delta_default: float
    virtues: tuple[str, ...] = field(default=VIRTUES)


def current_config() -> KarmaConfig:
    """Snapshot of the active tuning. Stable across a process lifetime
    unless someone monkey-patches the module constants (which tests do)."""
    return KarmaConfig(
        decay_rate=DECAY_RATE,
        decay_grace_days=DECAY_GRACE_DAYS,
        decay_floor_fraction=DECAY_FLOOR_FRACTION,
        decay_floor_min=DECAY_FLOOR_MIN,
        seeds_starting=SEEDS_STARTING,
        seeds_cap=SEEDS_CAP,
        seeds_replenish_per_pass=SEEDS_REPLENISH_PER_PASS,
        seeds_replenish_period_days=SEEDS_REPLENISH_PERIOD_DAYS,
        seed_delta_default=SEED_DELTA_DEFAULT,
    )
