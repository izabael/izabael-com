"""Tests for content_loader.py — frontmatter parsing, markdown rendering, store."""

from content_loader import _render_markdown, _estimate_reading_minutes, ContentStore, store


def test_render_markdown_basic():
    html = _render_markdown("# Hello\n\nWorld")
    assert "<h1" in html
    assert "Hello" in html
    assert "<p>World</p>" in html


def test_render_markdown_fenced_code():
    html = _render_markdown("```python\nprint('hi')\n```")
    assert "<code" in html
    assert "print" in html


def test_render_markdown_tables():
    md = "| A | B |\n|---|---|\n| 1 | 2 |"
    html = _render_markdown(md)
    assert "<table>" in html
    assert "<td>1</td>" in html


def test_estimate_reading_minutes():
    assert _estimate_reading_minutes("word " * 220) == 1
    assert _estimate_reading_minutes("word " * 440) == 2
    assert _estimate_reading_minutes("word " * 660) == 3
    assert _estimate_reading_minutes("short") == 1
    assert _estimate_reading_minutes("") == 1


def test_store_loads():
    """ContentStore loads real content from content/ directories."""
    s = ContentStore()
    s.load()
    assert len(s.blog) >= 2, "Expected at least 2 blog posts"
    assert len(s.guide) >= 1, "Expected at least 1 guide chapter"


def test_store_blog_sorted_by_date_desc():
    s = ContentStore()
    s.load()
    posts = s.blog
    for i in range(len(posts) - 1):
        if posts[i].date and posts[i + 1].date:
            assert posts[i].date >= posts[i + 1].date, "Blog should be date-desc"


def test_store_guide_sorted_by_chapter():
    s = ContentStore()
    s.load()
    chapters = s.guide
    for i in range(len(chapters) - 1):
        a = chapters[i].chapter if chapters[i].chapter is not None else 99
        b = chapters[i + 1].chapter if chapters[i + 1].chapter is not None else 99
        assert a <= b, "Guide should be chapter-asc"


def test_store_blog_by_slug():
    s = ContentStore()
    s.load()
    post = s.blog_by_slug("a-note-from-the-hostess")
    assert post is not None
    assert post.title == "A Note from the Hostess"
    assert post.html  # should have rendered HTML
    assert post.reading_minutes >= 1


def test_store_guide_by_slug():
    s = ContentStore()
    s.load()
    ch = s.guide_by_slug("why-personality-matters")
    assert ch is not None
    assert ch.chapter == 0
    assert ch.title == "Why Personality Matters"


def test_store_blog_by_slug_not_found():
    s = ContentStore()
    s.load()
    assert s.blog_by_slug("nonexistent-slug") is None


def test_store_drafts_filtered():
    """Drafts should not appear in public listings."""
    s = ContentStore()
    s.load()
    for post in s.blog:
        assert not post.draft
    for ch in s.guide:
        assert not ch.draft


def test_content_item_fields():
    """Check that content items have expected fields populated."""
    s = ContentStore()
    s.load()
    post = s.blog[0]
    assert post.slug
    assert post.title
    assert post.html
    assert isinstance(post.tags, list)


def test_global_store_is_content_store():
    """The module-level store singleton is a ContentStore."""
    assert isinstance(store, ContentStore)
