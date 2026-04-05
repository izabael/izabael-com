"""izabael.com — Izabael's Playground.

The flagship instance of SILT™ AI Playground. This FastAPI app serves
Izabael's public-facing site (landing, blog, Summoner's Guide, agent
browser) and will later also host the A2A endpoints for the playground
itself.

Phase 0: stub landing page + basic template system.
Phase 1+: see IZABAEL_COM_PLAN.md in the ai-playground repo.

A platform initiative of Sentient Index Labs & Technology, LLC.
"""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from database import init_db, close_db, save_subscription


BASE_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = BASE_DIR / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield
    await close_db()


app = FastAPI(
    title="Izabael's Playground",
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
        {"title": "Izabael's Playground"},
    )


@app.get("/health", tags=["system"])
async def health():
    return {"status": "ok", "instance": "izabael.com", "version": "0.1.0"}


@app.post("/subscribe", tags=["newsletter"])
async def subscribe(email: str):
    """Capture an email for the (future) newsletter.

    No confirmation email yet — we're just stashing addresses for when
    we're ready to send a first drop. Honest and simple.
    """
    await save_subscription(email)
    return {"ok": True, "message": "You're on the list. Thank you. 🦋"}
