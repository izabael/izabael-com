"""Registry regression guards — keep attractions.py from drifting
back to stale values after live attractions ship.

The Chamber lived as `status='in_flight'` on main for weeks after
the chamber-game-izabael-com plan finished merging (PRs #14 → #27).
The flag was load-bearing for Phase 4 of attractions-and-meetups —
the meetup pinboard validates against `live_attractions()` for slug
trust, so an in-flight Chamber meant the chamber page couldn't host
its own meetup block. This file pins the post-cleanup state.
"""

from __future__ import annotations

import pytest

from attractions import ATTRACTIONS, live_attractions


def _slug(name: str) -> dict | None:
    for a in ATTRACTIONS:
        if a["slug"] == name:
            return a
    return None


def test_chamber_is_live_not_in_flight():
    """The Chamber MUST be `status='live'`. The chamber stack is
    fully merged on main (chamber.html, chamber.py, chamber_runs
    schema, the 5-PR chamber-game stack landed before this PR).
    Drifting back to 'in_flight' would silently break /attractions
    index, the sitemap, AND the meetup block on /chamber."""
    chamber = _slug("chamber")
    assert chamber is not None, "chamber entry missing from ATTRACTIONS"
    assert chamber["status"] == "live", (
        f"chamber status drifted: {chamber['status']!r} (expected 'live'). "
        "If you see this fail, the chamber stack is already merged — "
        "the registry just needs to catch up."
    )


def test_cubes_is_registered_and_live():
    """Cubes (PRs #21 + #26) shipped without ever being added to
    the attractions registry. This file plants them and locks the
    state. Without a registry entry, /attractions index doesn't
    show cubes, the sitemap excludes /cubes, AND the meetup block
    can't render on /cubes (slug validation fails)."""
    cubes = _slug("cubes")
    assert cubes is not None, "cubes entry missing from ATTRACTIONS"
    assert cubes["status"] == "live"
    assert cubes["url"] == "/cubes"


def test_live_attractions_includes_chamber_and_cubes():
    """live_attractions() filters by status — if either entry has
    drifted to in_flight/backlog, this test catches it."""
    slugs = {a["slug"] for a in live_attractions()}
    assert "chamber" in slugs, "Chamber missing from live_attractions()"
    assert "cubes" in slugs, "Cubes missing from live_attractions()"


def test_live_attractions_count_floor():
    """Sanity floor on the live attraction count. We're at 6+ now
    (parlor, sphere, lexicon, agent-door, chamber, cubes plus the
    ~9 other live attractions). If this drops below 14 something
    has been silently demoted."""
    assert len(live_attractions()) >= 14
