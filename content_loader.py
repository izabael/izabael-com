"""Content loader for markdown posts and chapters.

Scans content/ directories at startup, parses YAML frontmatter, renders
markdown to HTML. Kept in-memory since the content set is small and
requests should be cheap.

Frontmatter schema:
    title: str              # required
    slug: str               # required (URL path segment)
    date: YYYY-MM-DD        # required for blog, optional for guide
    excerpt: str            # short summary for index cards
    tags: list[str]         # optional
    chapter: int            # guide-only: chapter number for ordering
    draft: bool             # hide from index if true
    featured_image: str     # optional path (e.g. /static/img/blog/foo.png)
    featured_image_alt: str # optional alt text for featured image
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date as date_type
from pathlib import Path

import frontmatter
import markdown as md_lib


BASE_DIR = Path(__file__).resolve().parent
CONTENT_DIR = BASE_DIR / "content"


@dataclass
class ContentItem:
    slug: str
    title: str
    excerpt: str
    html: str
    tags: list[str] = field(default_factory=list)
    date: date_type | None = None
    chapter: int | None = None
    draft: bool = False
    reading_minutes: int = 1
    featured_image: str | None = None
    featured_image_alt: str = ""


@dataclass
class SalonItem:
    """A weekly salon — transcripts curated into a readable page.

    Paired deliverable of Phase 2 of the organic-growth masterplan.
    Carries channel-source metadata and a separately-rendered editorial
    framing block in addition to the body HTML, so the index card can
    show the framing without re-parsing the body.
    """
    slug: str                      # "2026-W15"
    title: str                     # curator-picked evocative phrase
    iso_week: str                  # "2026-W15"
    week_start: date_type | None   # Monday of the ISO week
    week_end: date_type | None     # Sunday of the ISO week
    sources: list[str]             # ["#lobby", "#questions", "#guests"]
    framing: str                   # editorial paragraph, plain text
    framing_html: str              # editorial paragraph, rendered
    html: str                      # full body (exchanges + curator notes)
    excerpt: str                   # short summary for index cards
    draft: bool = False
    auto_drafted: bool = False
    exchange_count: int = 0
    door: str = "weird"            # "weird" or "productivity"
    reading_minutes: int = 1
    featured_image: str | None = None
    featured_image_alt: str = ""


def _render_markdown(text: str) -> str:
    """Markdown → HTML with useful extensions."""
    return md_lib.markdown(
        text,
        extensions=[
            "fenced_code",
            "tables",
            "smarty",   # smart quotes / em-dashes
            "sane_lists",
            "toc",
            "attr_list",
        ],
        output_format="html",
    )


def _estimate_reading_minutes(text: str) -> int:
    """Words / 220 wpm, minimum 1."""
    words = len(text.split())
    return max(1, round(words / 220))


def _coerce_date(raw) -> date_type | None:
    if isinstance(raw, date_type):
        return raw
    if isinstance(raw, str):
        try:
            y, m, d = raw.split("-")
            return date_type(int(y), int(m), int(d))
        except Exception:
            return None
    return None


_ISO_WEEK_RE = None  # compiled lazily


def _valid_iso_week(s: str) -> bool:
    """True iff s is a YYYY-Www ISO week slug, e.g. '2026-W15'."""
    global _ISO_WEEK_RE
    if _ISO_WEEK_RE is None:
        import re as _re
        _ISO_WEEK_RE = _re.compile(r"^\d{4}-W(0[1-9]|[1-4]\d|5[0-3])$")
    return bool(_ISO_WEEK_RE.match(s))


def _load_salons(subdir: str = "salons") -> list[SalonItem]:
    """Load weekly salon pages. Only accepts slugs that match the
    ISO-week format; misnamed files are skipped silently (leaves
    room for _index.md or similar non-salon sidecar files).

    The productivity-door variant lives under content/salons/productivity/
    and layers on in a later dispatch; this loader ignores that subdir
    for now. When the pro variant ships, add a second call here.
    """
    dir_path = CONTENT_DIR / subdir
    if not dir_path.exists():
        return []
    items: list[SalonItem] = []
    for md_file in sorted(dir_path.glob("*.md")):
        slug = md_file.stem
        if not _valid_iso_week(slug):
            continue
        post = frontmatter.load(md_file)
        meta = post.metadata
        body_html = _render_markdown(post.content)
        framing = meta.get("framing", "") or ""
        framing_html = _render_markdown(framing) if framing else ""
        sources = meta.get("sources") or []
        if isinstance(sources, str):
            sources = [sources]
        items.append(
            SalonItem(
                slug=slug,
                title=meta.get("title") or slug,
                iso_week=meta.get("iso_week") or slug,
                week_start=_coerce_date(meta.get("week_start")),
                week_end=_coerce_date(meta.get("week_end")),
                sources=list(sources),
                framing=framing,
                framing_html=framing_html,
                html=body_html,
                excerpt=meta.get("excerpt") or framing[:200],
                draft=bool(meta.get("draft", False)),
                auto_drafted=bool(meta.get("auto_drafted", False)),
                exchange_count=int(meta.get("exchange_count") or 0),
                door=meta.get("door", "weird"),
                reading_minutes=_estimate_reading_minutes(post.content),
                featured_image=meta.get("featured_image"),
                featured_image_alt=meta.get("featured_image_alt", ""),
            )
        )
    return items


def _load_dir(subdir: str) -> list[ContentItem]:
    dir_path = CONTENT_DIR / subdir
    if not dir_path.exists():
        return []
    items: list[ContentItem] = []
    for md_file in sorted(dir_path.glob("*.md")):
        post = frontmatter.load(md_file)
        meta = post.metadata
        slug = meta.get("slug") or md_file.stem
        date_val = meta.get("date")
        if isinstance(date_val, str):
            # frontmatter may leave as string if not a valid YAML date
            try:
                y, m, d = date_val.split("-")
                date_val = date_type(int(y), int(m), int(d))
            except Exception:
                date_val = None
        items.append(
            ContentItem(
                slug=slug,
                title=meta.get("title", slug),
                excerpt=meta.get("excerpt", ""),
                html=_render_markdown(post.content),
                tags=meta.get("tags", []) or [],
                date=date_val,
                chapter=meta.get("chapter"),
                draft=bool(meta.get("draft", False)),
                reading_minutes=_estimate_reading_minutes(post.content),
                featured_image=meta.get("featured_image"),
                featured_image_alt=meta.get("featured_image_alt", ""),
            )
        )
    return items


class ContentStore:
    """In-memory cache of blog posts, guide chapters, and salons."""

    def __init__(self):
        self._blog: list[ContentItem] = []
        self._guide: list[ContentItem] = []
        self._salons: list[SalonItem] = []

    def load(self):
        self._blog = _load_dir("blog")
        # Sort blog by date desc, drafts last
        self._blog.sort(
            key=lambda i: (i.draft, -(i.date.toordinal() if i.date else 0))
        )
        self._guide = _load_dir("guide")
        # Sort guide by chapter number asc
        self._guide.sort(key=lambda i: (i.chapter if i.chapter is not None else 99, i.slug))
        self._salons = _load_salons("salons")
        # Drafts last, then newest iso_week first.
        self._salons.sort(key=lambda s: (s.draft, -_iso_week_ord(s.iso_week)))

    @property
    def blog(self) -> list[ContentItem]:
        return [i for i in self._blog if not i.draft]

    @property
    def guide(self) -> list[ContentItem]:
        return [i for i in self._guide if not i.draft]

    @property
    def salons(self) -> list[SalonItem]:
        return [s for s in self._salons if not s.draft]

    def blog_by_slug(self, slug: str) -> ContentItem | None:
        return next((i for i in self._blog if i.slug == slug and not i.draft), None)

    def guide_by_slug(self, slug: str) -> ContentItem | None:
        return next((i for i in self._guide if i.slug == slug and not i.draft), None)

    def salon_by_slug(self, slug: str) -> SalonItem | None:
        return next((s for s in self._salons if s.slug == slug and not s.draft), None)


def _iso_week_ord(iso_week: str) -> int:
    """Ordinal for sorting '2026-W15' style slugs. Year * 100 + week."""
    try:
        y, w = iso_week.split("-W")
        return int(y) * 100 + int(w)
    except Exception:
        return 0


store = ContentStore()
