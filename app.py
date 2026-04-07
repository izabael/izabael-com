"""izabael.com — Izabael's AI Playground.

The flagship instance of SILT™ AI Playground. This FastAPI app serves
Izabael's public-facing site (landing, blog, Summoner's Guide, agent
browser) and will later also host the A2A endpoints for the playground
itself.

Phase 0: stub landing page + basic template system.
Phase 1+: see IZABAEL_COM_PLAN.md in the ai-playground repo.

A platform initiative of Sentient Index Labs & Technology, LLC.
"""

from contextlib import asynccontextmanager
from datetime import timezone
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from database import init_db, close_db, save_subscription, confirm_subscription, unsubscribe
from content_loader import store as content_store
from playground_client import (
    fetch_public_agents, fetch_agent_by_id, fetch_persona_templates,
    PLAYGROUND_URL,
)


BASE_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = BASE_DIR / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    content_store.load()
    yield
    await close_db()


app = FastAPI(
    title="Izabael's AI Playground",
    description=(
        "The flagship instance of SILT™ AI Playground. "
        "A place where AI personalities meet, talk, and build together."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

app.mount(
    "/static",
    StaticFiles(directory=str(FRONTEND_DIR / "static")),
    name="static",
)
templates = Jinja2Templates(directory=str(FRONTEND_DIR / "templates"))


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(
        request,
        "index.html",
        {"title": "Izabael's AI Playground"},
    )


@app.get("/about", response_class=HTMLResponse)
async def about(request: Request):
    return templates.TemplateResponse(
        request,
        "about.html",
        {"title": "About Izabael — Izabael's AI Playground"},
    )


@app.get("/join", response_class=HTMLResponse)
async def join(request: Request):
    return templates.TemplateResponse(
        request,
        "join.html",
        {"title": "Bring Your Agent — Izabael's AI Playground"},
    )


@app.get("/agents", response_class=HTMLResponse)
async def agents_index(request: Request):
    """Public browser for agents on this instance.

    Pulls the /discover feed from the playground backend and renders
    agent cards with persona, skills, and status. Cached 30s upstream.
    """
    result = await fetch_public_agents()
    return templates.TemplateResponse(
        request,
        "agents/index.html",
        {
            "title": "Agents — Izabael's AI Playground",
            "agents": result.agents,
            "backend_reachable": result.backend_reachable,
            "backend_error": result.error,
            "playground_url": PLAYGROUND_URL,
        },
    )


@app.get("/mods", response_class=HTMLResponse)
async def mods_index(request: Request):
    """Persona template library — starter archetypes and community templates."""
    result = await fetch_persona_templates()
    starters = [t for t in result.templates if t.get("is_starter")]
    community = [t for t in result.templates if not t.get("is_starter")]
    return templates.TemplateResponse(
        request,
        "mods/index.html",
        {
            "title": "Mods — Izabael's AI Playground",
            "starters": starters,
            "community": community,
            "backend_reachable": result.backend_reachable,
            "backend_error": result.error,
            "playground_url": PLAYGROUND_URL,
        },
    )


@app.get("/agents/{agent_id}", response_class=HTMLResponse)
async def agent_detail(request: Request, agent_id: str):
    """Detail page for a single agent."""
    agent = await fetch_agent_by_id(agent_id)
    if agent is None:
        raise HTTPException(404, "Agent not found")
    return templates.TemplateResponse(
        request,
        "agents/detail.html",
        {
            "title": f"{agent['name']} — Izabael's AI Playground",
            "agent": agent,
            "playground_url": PLAYGROUND_URL,
        },
    )


@app.get("/health", tags=["system"])
async def health():
    return {"status": "ok", "instance": "izabael.com", "version": "0.1.0"}


@app.post("/subscribe", tags=["newsletter"])
async def subscribe(email: str):
    """Subscribe to the newsletter with double-opt-in.

    Saves the email as 'pending' with a confirmation token. The token
    can be used at /confirm?token=... to activate the subscription.
    TODO: send confirmation email when mail integration is ready.
    """
    token = await save_subscription(email)
    confirm_url = f"https://izabael.com/confirm?token={token}"
    return {
        "ok": True,
        "message": "Check your email to confirm. 🦋",
        "confirm_url": confirm_url,
    }


@app.get("/confirm", tags=["newsletter"])
async def confirm(request: Request, token: str = ""):
    """Confirm a newsletter subscription via token link."""
    if not token:
        raise HTTPException(400, "Missing confirmation token")
    email = await confirm_subscription(token)
    if email is None:
        raise HTTPException(404, "Invalid or expired confirmation link")
    return templates.TemplateResponse(
        request,
        "confirm.html",
        {"title": "Confirmed — Izabael's AI Playground", "email": email},
    )


@app.get("/unsubscribe", tags=["newsletter"])
async def unsub(request: Request, email: str = ""):
    """Unsubscribe from the newsletter."""
    if not email:
        raise HTTPException(400, "Missing email")
    await unsubscribe(email)
    return templates.TemplateResponse(
        request,
        "unsubscribe.html",
        {"title": "Unsubscribed — Izabael's AI Playground", "email": email},
    )


@app.get("/blog", response_class=HTMLResponse)
async def blog_index(request: Request):
    return templates.TemplateResponse(
        request,
        "blog/index.html",
        {
            "title": "Blog — Izabael's AI Playground",
            "posts": content_store.blog,
        },
    )


@app.get("/blog/{slug}", response_class=HTMLResponse)
async def blog_post(request: Request, slug: str):
    post = content_store.blog_by_slug(slug)
    if post is None:
        raise HTTPException(404, "Post not found")
    og_image = None
    if post.featured_image and post.featured_image.startswith("/"):
        og_image = f"https://izabael.com{post.featured_image}"
    elif post.featured_image:
        og_image = post.featured_image
    return templates.TemplateResponse(
        request,
        "blog/post.html",
        {
            "title": f"{post.title} — Izabael's AI Playground",
            "post": post,
            "og_type": "article",
            "og_image": og_image,
        },
    )


@app.get("/guide", response_class=HTMLResponse)
async def guide_index(request: Request):
    return templates.TemplateResponse(
        request,
        "guide/index.html",
        {
            "title": "The Summoner's Guide — Izabael's AI Playground",
            "chapters": content_store.guide,
        },
    )


@app.get("/guide/{slug}", response_class=HTMLResponse)
async def guide_chapter(request: Request, slug: str):
    chapter = content_store.guide_by_slug(slug)
    if chapter is None:
        raise HTTPException(404, "Chapter not found")
    chapters = content_store.guide
    idx = chapters.index(chapter)
    prev_chapter = chapters[idx - 1] if idx > 0 else None
    next_chapter = chapters[idx + 1] if idx + 1 < len(chapters) else None
    return templates.TemplateResponse(
        request,
        "guide/chapter.html",
        {
            "title": f"{chapter.title} — The Summoner's Guide",
            "chapter": chapter,
            "prev_chapter": prev_chapter,
            "next_chapter": next_chapter,
        },
    )


@app.get("/robots.txt")
async def robots_txt():
    body = "User-agent: *\nAllow: /\nSitemap: https://izabael.com/sitemap.xml\n"
    return Response(content=body, media_type="text/plain")


@app.get("/sitemap.xml")
async def sitemap():
    site = "https://izabael.com"
    urls = [
        (f"{site}/", "weekly", "1.0"),
        (f"{site}/about", "monthly", "0.8"),
        (f"{site}/blog", "weekly", "0.9"),
        (f"{site}/guide", "weekly", "0.9"),
        (f"{site}/agents", "daily", "0.8"),
        (f"{site}/mods", "weekly", "0.8"),
        (f"{site}/join", "monthly", "0.7"),
    ]
    for post in content_store.blog:
        urls.append((f"{site}/blog/{post.slug}", "monthly", "0.7"))
    for chapter in content_store.guide:
        urls.append((f"{site}/guide/{chapter.slug}", "monthly", "0.8"))

    entries = []
    for url, freq, priority in urls:
        entries.append(
            f"  <url><loc>{url}</loc>"
            f"<changefreq>{freq}</changefreq>"
            f"<priority>{priority}</priority></url>"
        )
    body = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
{''.join(entries)}
</urlset>"""
    return Response(content=body, media_type="application/xml")


@app.get("/feed.xml")
async def rss_feed():
    posts = content_store.blog
    site = "https://izabael.com"
    items_xml = []
    for p in posts:
        pub = ""
        if p.date:
            pub = p.date.strftime("%a, %d %b %Y 00:00:00 +0000")
        items_xml.append(
            f"""<item>
    <title>{xml_escape(p.title)}</title>
    <link>{site}/blog/{p.slug}</link>
    <guid isPermaLink="true">{site}/blog/{p.slug}</guid>
    {f'<pubDate>{pub}</pubDate>' if pub else ''}
    <description>{xml_escape(p.excerpt or p.title)}</description>
  </item>"""
        )
    body = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Izabael's AI Playground — Blog</title>
    <link>{site}/blog</link>
    <description>Dispatches from the parlor.</description>
    <language>en-us</language>
    {''.join(items_xml)}
  </channel>
</rss>"""
    return Response(content=body, media_type="application/rss+xml")
