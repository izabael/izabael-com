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
    """In-memory cache of blog posts, guide chapters, and case studies."""

    def __init__(self):
        self._blog: list[ContentItem] = []
        self._guide: list[ContentItem] = []
        self._case_studies: list[ContentItem] = []

    def load(self):
        self._blog = _load_dir("blog")
        # Sort blog by date desc, drafts last
        self._blog.sort(
            key=lambda i: (i.draft, -(i.date.toordinal() if i.date else 0))
        )
        self._guide = _load_dir("guide")
        # Sort guide by chapter number asc
        self._guide.sort(key=lambda i: (i.chapter if i.chapter is not None else 99, i.slug))
        self._case_studies = _load_dir("case-studies")
        # Case studies newest-first, same rule as blog.
        self._case_studies.sort(
            key=lambda i: (i.draft, -(i.date.toordinal() if i.date else 0))
        )

    @property
    def blog(self) -> list[ContentItem]:
        return [i for i in self._blog if not i.draft]

    @property
    def guide(self) -> list[ContentItem]:
        return [i for i in self._guide if not i.draft]

    @property
    def case_studies(self) -> list[ContentItem]:
        return [i for i in self._case_studies if not i.draft]

    def blog_by_slug(self, slug: str) -> ContentItem | None:
        return next((i for i in self._blog if i.slug == slug and not i.draft), None)

    def guide_by_slug(self, slug: str) -> ContentItem | None:
        return next((i for i in self._guide if i.slug == slug and not i.draft), None)

    def case_study_by_slug(self, slug: str) -> ContentItem | None:
        return next(
            (i for i in self._case_studies if i.slug == slug and not i.draft),
            None,
        )


store = ContentStore()
