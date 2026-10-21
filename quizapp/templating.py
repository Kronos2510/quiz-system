"""Template rendering helpers and view filters."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

from .config import settings
from .security import ensure_csrf_token

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"

templates = Jinja2Templates(directory=str(TEMPLATE_DIR))


def format_dt(value: datetime | None, with_date: bool = True) -> str:
    if not value:
        return "—"
    if with_date:
        return value.strftime("%d %b %Y, %H:%M UTC")
    return value.strftime("%H:%M UTC")


def format_date(value: datetime | None) -> str:
    if not value:
        return "—"
    return value.strftime("%d %b %Y")


def iso_utc(value: datetime | None) -> str:
    if not value:
        return ""
    aware = value.replace(tzinfo=timezone.utc)
    return aware.isoformat().replace("+00:00", "Z")


def relative_time(value: datetime | None) -> str:
    if not value:
        return "—"
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    delta = now - value
    seconds = int(delta.total_seconds())
    if seconds < 0:
        return "just now"
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60} min ago"
    if seconds < 86400:
        return f"{seconds // 3600} h ago"
    if seconds < 604800:
        return f"{seconds // 86400} d ago"
    return format_date(value)


templates.env.filters["dt"] = format_dt
templates.env.filters["date"] = format_date
templates.env.filters["iso"] = iso_utc
templates.env.filters["ago"] = relative_time


def add_flash(request: Request, message: str, category: str = "success") -> None:
    flashes = request.session.get("flashes") or []
    flashes.append({"message": message, "category": category})
    request.session["flashes"] = flashes


def render(
    request: Request,
    template: str,
    *,
    status_code: int = 200,
    headers: dict[str, str] | None = None,
    **context,
):
    csrf_token = ensure_csrf_token(request)
    flashes = request.session.pop("flashes", []) or []
    ctx = {
        "request": request,
        "user": getattr(request.state, "user", None),
        "csrf_token": csrf_token,
        "flashes": flashes,
        "app_name": settings.app_name,
        "tagline": settings.tagline,
        **context,
    }
    return templates.TemplateResponse(
        request=request,
        name=template,
        context=ctx,
        status_code=status_code,
        headers=headers,
    )
