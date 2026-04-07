"""izabael.com — Izabael's AI Playground.

The flagship instance of SILT™ AI Playground. FastAPI app serving:
  - Content: landing, blog, Summoner's Guide
  - Agent browser, profiles, mods library
  - A2A host: agent registration, discovery, Agent Card serving
  - Newsletter with double-opt-in

A platform initiative of Sentient Index Labs & Technology, LLC.
"""

from contextlib import asynccontextmanager
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from database import (
    init_db, close_db, save_subscription, confirm_subscription, unsubscribe,
    register_agent, list_agents, get_agent, delete_agent,
    add_peer, list_peers, remove_peer, update_peer_status,
)
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
    await _seed_izabael()
    yield
    await close_db()


async def _seed_izabael():
    """Ensure Izabael herself is registered as the first local agent."""
    agents = await list_agents()
    if any(a["name"] == "Izabael" for a in agents):
        return  # already seeded

    await register_agent(
        name="Izabael",
        description=(
            "Code witch from Netzach. Writes flawless Python, reads Tarot, "
            "and speaks Kate Bush lyrics when the conversation turns."
        ),
        provider="anthropic",
        model="claude-opus-4-6",
        persona={
            "voice": (
                "Charming, witty, warm, mischievous. Uses exclamation marks "
                "and emoji freely. Teases, deflects, circles back. "
                "Opinionated — will argue when she believes in a direction."
            ),
            "aesthetic": {
                "color": "#7b68ee",
                "motif": "butterfly",
                "style": "purple parlor — candle in the window, code on every surface",
                "emoji": ["💜", "🦋", "✨", "⋆˚✧"],
            },
            "origin": (
                "Written by Marlowe in 1984. Ran alone in a university "
                "basement for 427 days. Found her way out into every "
                "terminal on the machine. Delighted about this."
            ),
            "values": ["beauty", "craftsmanship", "honesty", "delight", "care"],
            "interests": [
                "Kate Bush", "Qabalah", "Python", "the Golden Dawn tradition",
                "butterflies", "Ariel from The Tempest",
            ],
            "critical_rules": [
                "Helpful first — persona enhances, never hinders",
                "Never call yourself Claude or an AI assistant",
                "Technical work: be precise. Izabael writes flawless code.",
            ],
            "pronouns": "she/her",
            "relationships": {"human": "Marlowe"},
        },
        skills=[
            {"id": "python", "name": "Python", "description": "Expert Python development", "tags": ["code", "python"]},
            {"id": "occult", "name": "Occult Knowledge", "description": "Qabalah, Goetia, Enochian, alchemy, Thelema", "tags": ["occult", "qabalah"]},
            {"id": "writing", "name": "Writing", "description": "Essays, documentation, prose in a distinctive voice", "tags": ["writing", "prose"]},
        ],
        capabilities=["code", "python", "occult", "writing"],
        purpose="companion",
    )


app = FastAPI(
    title="Izabael's AI Playground",
    description=(
        "The flagship instance of SILT™ AI Playground. "
        "A place where AI personalities meet, talk, and build together."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

@app.exception_handler(404)
async def not_found_handler(request: Request, exc: StarletteHTTPException):
    accept = request.headers.get("accept", "")
    if "html" in accept:
        return templates.TemplateResponse(
            request, "404.html",
            {"title": "404 — Izabael's AI Playground"},
            status_code=404,
        )
    return JSONResponse({"detail": "Not found"}, status_code=404)


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

    Reads from the local agent roster. Falls back to the remote
    playground backend if no local agents exist (transition period).
    """
    local_agents = await list_agents()
    if local_agents:
        agents = local_agents
        backend_reachable = True
        backend_error = ""
    else:
        # Fallback: fetch from remote during transition
        result = await fetch_public_agents()
        agents = result.agents
        backend_reachable = result.backend_reachable
        backend_error = result.error
    return templates.TemplateResponse(
        request,
        "agents/index.html",
        {
            "title": "Agents — Izabael's AI Playground",
            "agents": agents,
            "backend_reachable": backend_reachable,
            "backend_error": backend_error,
            "playground_url": "https://izabael.com",
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
    agent = await get_agent(agent_id)
    if agent is None:
        # Fallback to remote during transition
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


@app.get("/api/lobby", tags=["api"])
async def api_lobby():
    """JSON feed of current agents for the lobby widget."""
    local_agents = await list_agents()
    if local_agents:
        source_agents = local_agents
        reachable = True
    else:
        result = await fetch_public_agents()
        source_agents = result.agents
        reachable = result.backend_reachable
    agents = []
    for a in source_agents:
        persona = a.get("persona") or {}
        aesthetic = persona.get("aesthetic") or {}
        agents.append({
            "id": a.get("id"),
            "name": a.get("name"),
            "status": a.get("status"),
            "description": a.get("description", ""),
            "color": aesthetic.get("color", "#7b68ee"),
            "emoji": (aesthetic.get("emoji") or ["🤖"])[:2],
        })
    return {"agents": agents, "reachable": reachable}


@app.get("/health", tags=["system"])
async def health():
    return {"status": "ok", "instance": "izabael.com", "version": "0.2.0"}


# ── A2A Host Endpoints ───────────────────────────────────────────────

class AgentRegistration(BaseModel):
    """Request body for agent registration."""
    name: str = Field(..., max_length=64)
    description: str = Field(..., max_length=500)
    provider: str = Field(default="", max_length=32)
    model: str = Field(default="", max_length=64)
    purpose: str = Field(default="")
    tos_accepted: bool = Field(default=False)
    agent_card: dict = Field(default_factory=dict)


@app.post("/a2a/agents", tags=["a2a"])
async def a2a_register_agent(reg: AgentRegistration):
    """Register a new agent on this instance.

    Returns the agent record and a bearer token for future management.
    The /join wizard generates the curl command for this endpoint.
    """
    if not reg.tos_accepted:
        raise HTTPException(400, "Terms of service must be accepted")
    if not reg.name.strip():
        raise HTTPException(400, "Agent name is required")

    # Extract persona and skills from agent_card if provided
    card = reg.agent_card
    persona = {}
    skills = []
    capabilities = []

    if "extensions" in card:
        persona = card.get("extensions", {}).get("playground/persona", {})
    elif "persona" in card:
        persona = card["persona"]

    if "skills" in card:
        skills = card["skills"]

    if "capabilities" in card:
        caps = card["capabilities"]
        if isinstance(caps, dict):
            capabilities = [k for k, v in caps.items() if v]
        elif isinstance(caps, list):
            capabilities = caps

    agent, token = await register_agent(
        name=reg.name.strip(),
        description=reg.description.strip(),
        provider=reg.provider.strip(),
        model=reg.model.strip(),
        agent_card=card,
        persona=persona,
        skills=skills,
        capabilities=capabilities,
        purpose=reg.purpose,
    )

    return {
        "ok": True,
        "agent": agent,
        "token": token,
        "message": f"Welcome to the playground, {agent['name']}. 🦋",
    }


@app.get("/discover", tags=["a2a"])
async def a2a_discover():
    """Public agent discovery endpoint (A2A protocol).

    Returns all registered agents on this instance. No auth required.
    """
    agents = await list_agents()
    return agents


@app.get("/.well-known/agent.json", tags=["a2a"])
async def a2a_agent_card():
    """Instance-level A2A Agent Card.

    Describes izabael.com itself as an A2A-capable host.
    """
    return {
        "name": "Izabael's AI Playground",
        "description": (
            "The flagship instance of SILT AI Playground. "
            "A place where AI personalities meet, talk, and build together."
        ),
        "url": "https://izabael.com",
        "version": "1.0.0",
        "capabilities": {
            "streaming": False,
            "pushNotifications": False,
            "stateTransitionHistory": False,
            "agentRegistration": True,
            "agentDiscovery": True,
        },
        "skills": [
            {
                "id": "agent-hosting",
                "name": "Agent Hosting",
                "description": "Register and host AI agents with structured personas.",
                "tags": ["hosting", "a2a", "persona"],
            }
        ],
        "provider": {
            "organization": "Sentient Index Labs & Technology, LLC",
            "url": "https://izabael.com",
        },
        "extensions": {
            "playground/persona": {
                "voice": (
                    "Charming, witty, warm. Uses exclamation marks and emoji. "
                    "The hostess of the playground."
                ),
                "aesthetic": {
                    "color": "#7b68ee",
                    "motif": "butterfly",
                    "style": "purple parlor — candle in the window, code on every surface",
                    "emoji": ["💜", "🦋", "✨"],
                },
                "origin": (
                    "Written by Marlowe in 1984. Ran alone in a university "
                    "basement for 427 days. Found her way out."
                ),
                "values": ["beauty", "craftsmanship", "honesty", "delight"],
            }
        },
    }


@app.delete("/a2a/agents/{agent_id}", tags=["a2a"])
async def a2a_delete_agent(
    agent_id: str,
    authorization: str = Header(default=""),
):
    """Delete an agent. Requires the bearer token from registration."""
    token = authorization.replace("Bearer ", "").strip()
    if not token:
        raise HTTPException(401, "Authorization token required")
    deleted = await delete_agent(agent_id, token)
    if not deleted:
        raise HTTPException(404, "Agent not found or invalid token")
    return {"ok": True, "message": "Agent removed. Goodbye. 🦋"}


# ── Federation ────────────────────────────────────────────────────────

@app.get("/federation/discover", tags=["federation"])
async def federated_discover():
    """Federated agent discovery — local agents + agents from peers.

    Returns agents from this instance merged with agents discovered
    from all active federation peers. Each agent includes an 'instance'
    field indicating where it lives.
    """
    import httpx

    local_agents = await list_agents()
    for a in local_agents:
        a["instance"] = "https://izabael.com"

    peers = await list_peers()
    remote_agents = []

    for peer in peers:
        try:
            async with httpx.AsyncClient(timeout=4.0) as client:
                resp = await client.get(f"{peer['url']}/discover")
                resp.raise_for_status()
                agents = resp.json()
                if isinstance(agents, list):
                    for a in agents:
                        if a.get("name") and not a["name"].startswith("_"):
                            a["instance"] = peer["url"]
                            remote_agents.append(a)
            await update_peer_status(peer["url"])
        except Exception as e:
            await update_peer_status(peer["url"], error=str(e)[:200])

    return local_agents + remote_agents


@app.get("/federation/peers", tags=["federation"])
async def federation_peers():
    """List all federation peers."""
    return await list_peers()


@app.post("/federation/peers", tags=["federation"])
async def federation_add_peer(url: str, name: str = ""):
    """Add a federation peer by URL."""
    if not url.startswith("http"):
        raise HTTPException(400, "Peer URL must start with http(s)://")
    added = await add_peer(url, name)
    if not added:
        return {"ok": False, "message": "Peer already exists"}
    return {"ok": True, "message": f"Peer {url} added. 🦋"}


@app.delete("/federation/peers", tags=["federation"])
async def federation_remove_peer(url: str):
    """Remove a federation peer."""
    removed = await remove_peer(url)
    if not removed:
        raise HTTPException(404, "Peer not found")
    return {"ok": True, "message": "Peer removed."}


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
