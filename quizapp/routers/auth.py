"""Registration, login and logout."""

from __future__ import annotations

import re
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import dashboard_url
from ..models import (
    ROLE_HOST,
    ROLE_PARTICIPANT,
    STATUS_APPROVED,
    STATUS_DISABLED,
    STATUS_PENDING,
    STATUS_REJECTED,
    User,
    utcnow,
)
from ..security import hash_password, new_csrf_token, verify_password
from ..templating import add_flash, render

router = APIRouter()

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def safe_next(value: str | None) -> str | None:
    if not value:
        return None
    parsed = urlparse(value)
    if parsed.scheme or parsed.netloc:
        return None
    if not value.startswith("/") or value.startswith("//"):
        return None
    return value


@router.get("/login")
def login_page(request: Request, next: str | None = None):
    user = getattr(request.state, "user", None)
    if user:
        return RedirectResponse(dashboard_url(user), status_code=303)
    return render(request, "auth/login.html", next_url=safe_next(next) or "", email="")


@router.post("/login")
def login_submit(
    request: Request,
    email: str = Form(""),
    password: str = Form(""),
    next: str = Form(""),
    db: Session = Depends(get_db),
):
    email = email.strip().lower()
    user = db.scalar(select(User).where(User.email == email))

    def fail(message: str, status_code: int = 400):
        return render(
            request,
            "auth/login.html",
            status_code=status_code,
            error=message,
            email=email,
            next_url=safe_next(next) or "",
        )

    if not user or not verify_password(password, user.password_hash):
        return fail("That email and password combination doesn't match our records.")

    if user.status == STATUS_PENDING:
        return fail("Your account is still waiting for approval. You can sign in once you're approved.")
    if user.status == STATUS_REJECTED:
        return fail("This registration was not approved. Contact the host or platform admin.")
    if user.status == STATUS_DISABLED:
        return fail("This account has been disabled. Contact the platform admin.")
    if user.status != STATUS_APPROVED:
        return fail("This account is not available right now.")

    user.last_login_at = utcnow()
    db.commit()

    request.session.clear()
    request.session["uid"] = user.id
    request.session["csrf"] = new_csrf_token()

    destination = safe_next(next) or dashboard_url(user)
    add_flash(request, f"Welcome back, {user.name.split(' ')[0]}.", "success")
    return RedirectResponse(destination, status_code=303)


@router.get("/register")
def register_page(request: Request, role: str = "participant"):
    user = getattr(request.state, "user", None)
    if user:
        return RedirectResponse(dashboard_url(user), status_code=303)
    selected = ROLE_HOST if role == ROLE_HOST else ROLE_PARTICIPANT
    return render(request, "auth/register.html", form={}, role=selected)


@router.post("/register")
def register_submit(
    request: Request,
    name: str = Form(""),
    email: str = Form(""),
    password: str = Form(""),
    confirm: str = Form(""),
    role: str = Form(ROLE_PARTICIPANT),
    db: Session = Depends(get_db),
):
    name = name.strip()
    email = email.strip().lower()
    role = role if role in {ROLE_HOST, ROLE_PARTICIPANT} else ROLE_PARTICIPANT

    def fail(message: str):
        return render(
            request,
            "auth/register.html",
            status_code=400,
            error=message,
            form={"name": name, "email": email},
            role=role,
        )

    if len(name) < 2 or len(name) > 80:
        return fail("Enter your full name (between 2 and 80 characters).")
    if not EMAIL_RE.match(email):
        return fail("Enter a valid email address.")
    if len(password) < 8:
        return fail("Use at least 8 characters for your password.")
    if password != confirm:
        return fail("The two passwords don't match.")
    if db.scalar(select(User.id).where(User.email == email)):
        return fail("An account with that email already exists. Try signing in instead.")

    user = User(
        email=email,
        name=name,
        password_hash=hash_password(password),
        role=role,
        status=STATUS_PENDING,
    )
    db.add(user)
    db.commit()

    return render(
        request,
        "auth/pending.html",
        name=name.split(" ")[0],
        role=role,
    )


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    response = RedirectResponse("/login", status_code=303)
    return response


@router.get("/pending")
def pending_page(request: Request):
    return render(request, "auth/pending.html", name=None, role=None)
