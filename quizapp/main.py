"""FastAPI application factory and top-level routes."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select
from starlette.middleware.sessions import SessionMiddleware

from .config import PROJECT_ROOT, settings
from .db import SessionLocal, create_schema, get_db
from .deps import AuthRequired, Forbidden, current_user
from .models import Quiz, User
from .routers import admin, api, auth, host, participant
from .security import csrf_protect
from .seed import ensure_admin, seed_demo
from .templating import render

logger = logging.getLogger("quizsystem")

PUBLIC_DIR = PROJECT_ROOT / "public"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    create_schema()
    with SessionLocal() as db:
        admin_user = ensure_admin(db)
        seed_demo(db, admin_user)
    yield


app = FastAPI(
    title=settings.app_name,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
    dependencies=[Depends(csrf_protect)],
)

app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    session_cookie="quiz_session",
    max_age=settings.session_max_age,
    same_site="lax",
    https_only=settings.cookie_secure,
)

# Vercel serves public/ from its CDN. Locally we mount the same folders so
# development matches production paths (/css/..., /js/...).
(PUBLIC_DIR / "css").mkdir(parents=True, exist_ok=True)
(PUBLIC_DIR / "js").mkdir(parents=True, exist_ok=True)
app.mount("/css", StaticFiles(directory=str(PUBLIC_DIR / "css")), name="css")
app.mount("/js", StaticFiles(directory=str(PUBLIC_DIR / "js")), name="js")


@app.get("/favicon.svg", include_in_schema=False)
def favicon_svg():
    return FileResponse(PUBLIC_DIR / "favicon.svg", media_type="image/svg+xml")


@app.get("/favicon.ico", include_in_schema=False)
def favicon_ico():
    return RedirectResponse("/favicon.svg", status_code=307)


@app.get("/health")
def health():
    return {"status": "ok", "app": settings.app_name}


@app.get("/")
def landing(
    request: Request,
    user: User | None = Depends(current_user),
    db=Depends(get_db),
):
    if user:
        from .deps import dashboard_url

        return RedirectResponse(dashboard_url(user), status_code=303)

    stats = {
        "quizzes": db.scalar(select(func.count(Quiz.id)).where(Quiz.status == "published")) or 0,
        "hosts": db.scalar(select(func.count(User.id)).where(User.status == "approved")) or 0,
    }
    return render(request, "index.html", stats=stats)


app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(host.router)
app.include_router(participant.router)
app.include_router(api.router)


@app.exception_handler(AuthRequired)
async def auth_required_handler(request: Request, _exc: AuthRequired):
    target = request.url.path
    if request.url.query:
        target = f"{target}?{request.url.query}"
    return RedirectResponse(f"/login?next={quote(target, safe='')}", status_code=303)


@app.exception_handler(Forbidden)
async def forbidden_handler(request: Request, exc: Forbidden):
    return render(
        request,
        "error.html",
        status_code=403,
        code="403",
        title="Not your area",
        message=str(exc) if str(exc) else "You don't have access to this page.",
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

    titles = {
        400: "Something wasn't right",
        403: "Not your area",
        404: "Page not found",
        405: "Method not allowed",
    }
    return render(
        request,
        "error.html",
        status_code=exc.status_code,
        code=str(exc.status_code),
        title=titles.get(exc.status_code, "Something went wrong"),
        message=str(exc.detail) if exc.detail else "Please head back and try again.",
    )
