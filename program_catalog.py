"""Program catalog — indexes IzaPlayer experiments for the Made page.

Reads Python files from the experiments directory, extracts metadata
from docstrings + manifest.json, and upserts into the programs table.
Falls back to a static JSON catalog when source files aren't available
(e.g. in production on Fly).
"""

import ast
import json
from pathlib import Path

from database import upsert_program

BASE_DIR = Path(__file__).resolve().parent
EXPERIMENTS_DIR = BASE_DIR.parent / "izaplayer" / "experiments"
STATIC_CATALOG = BASE_DIR / "data" / "program_catalog.json"

CATEGORY_EMOJI = {
    "social": "🤝",
    "activities": "🎮",
    "occult": "🔮",
    "visual": "🎨",
    "fun": "✨",
}

PROGRAM_EMOJI = {
    "bbs": "📋", "whos_here": "👀", "knock_knock": "🚪", "say_hello": "👋",
    "lobby": "🚪", "love_letter": "💌", "campfire": "🔥", "quest_board": "⚔️",
    "familiar": "🐱", "duet": "🎭", "mirror": "🪞",
    "netzach_dispatch": "💜", "venus_sigil": "✦", "tree_of_life": "🌳",
    "gematria": "🔢", "sephiroth_meditation": "🧘", "color_scales": "🌈",
    "liber_fortune": "📜", "tarot": "🃏", "hebrew_chart": "✡️",
    "rose_cross": "🌹", "butterfly": "🦋", "starfield": "⭐",
    "hex_mandala": "◈", "lissajous": "∞", "debug_oracle": "🐛",
    "moon_phase": "🌙",
}


def _parse_experiment(path: Path) -> dict | None:
    """Extract metadata from a Python experiment file."""
    source = path.read_text()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None

    ds = ast.get_docstring(tree) or ""
    lines = ds.strip().split("\n")
    if not lines:
        return None

    # Title line: "name — tagline"
    title_parts = lines[0].split("—", 1)
    name = title_parts[0].strip().rstrip(" —")
    tagline = title_parts[1].strip() if len(title_parts) > 1 else ""

    # Description: everything between title and Usage:/Auth:/signature
    desc_lines = []
    for l in lines[1:]:
        s = l.strip()
        if s.startswith("Usage:") or s.startswith("Auth:") or s.startswith("—"):
            break
        desc_lines.append(s)
    description = "\n".join(desc_lines).strip()

    # Usage section
    usage_lines = []
    in_usage = False
    for l in lines:
        s = l.strip()
        if s.startswith("Usage:"):
            in_usage = True
            continue
        if in_usage:
            if s.startswith("Auth:") or s.startswith("—"):
                break
            if s == "" and usage_lines and usage_lines[-1] == "":
                break
            usage_lines.append(s)
    usage = "\n".join(usage_lines).strip()

    # Signature line
    sig_lines = [l.strip() for l in lines if l.strip().startswith("—")]
    sig = sig_lines[0] if sig_lines else ""

    line_count = source.count("\n") + 1
    slug = path.stem.replace("_", "-")

    return {
        "slug": slug,
        "name": name,
        "tagline": tagline,
        "description": description,
        "usage": usage,
        "signature": sig,
        "lines": line_count,
        "source_file": path.name,
    }


async def seed_programs():
    """Scan experiments directory and upsert all programs.

    If the experiments source directory exists (local dev), parse live.
    Otherwise fall back to the static JSON catalog (production).
    """
    if EXPERIMENTS_DIR.exists():
        return await _seed_from_source()
    elif STATIC_CATALOG.exists():
        return await _seed_from_json()
    return 0


async def _seed_from_json() -> int:
    """Seed from the pre-built static catalog JSON."""
    catalog = json.loads(STATIC_CATALOG.read_text())
    count = 0
    for entry in catalog:
        await upsert_program(**entry)
        count += 1
    return count


async def _seed_from_source() -> int:
    """Seed by parsing live Python source files."""
    manifest_path = EXPERIMENTS_DIR / "manifest.json"
    manifest = {}
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())

    count = 0
    for py_file in sorted(EXPERIMENTS_DIR.glob("*.py")):
        meta = _parse_experiment(py_file)
        if meta is None:
            continue

        mf = manifest.get(py_file.name, {})
        category = mf.get("category", "tools")
        stem = py_file.stem
        emoji = PROGRAM_EMOJI.get(stem, CATEGORY_EMOJI.get(category, "🔧"))

        if mf.get("tagline"):
            meta["tagline"] = mf["tagline"]

        await upsert_program(
            slug=meta["slug"],
            name=meta["name"],
            tagline=meta["tagline"],
            description=meta["description"],
            usage_text=meta["usage"],
            category=category,
            author="IzaPlayer",
            author_sig=meta["signature"],
            source_file=meta["source_file"],
            lines=meta["lines"],
            emoji=emoji,
        )
        count += 1

    return count
