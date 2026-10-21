"""Shared FastAPI dependencies: authentication and role guards."""

from __future__ import annotations

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from .db import get_db
from .models import (
    ROLE_ADMIN,
    ROLE_HOST,
    ROLE_PARTICIPANT,
    STATUS_APPROVED,
    User,
)


class AuthRequired(Exception):
    """Raised when an anonymous visitor hits a protected page."""


class Forbidden(Exception):
    """Raised when a signed-in user lacks the role for a page."""


def current_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    user_id = request.session.get("uid")
    if not user_id:
        request.state.user = None
        return None

    user = db.get(User, int(user_id))
    if not user or user.status != STATUS_APPROVED:
        request.session.clear()
        request.state.user = None
        return None

    request.state.user = user
    return user


def require_user(user: User | None = Depends(current_user)) -> User:
    if user is None:
        raise AuthRequired()
    return user


def require_host(user: User = Depends(require_user)) -> User:
    if user.role != ROLE_HOST:
        raise Forbidden("Only hosts can open this area.")
    return user


def require_admin(user: User = Depends(require_user)) -> User:
    if user.role != ROLE_ADMIN:
        raise Forbidden("Only the platform admin can open this area.")
    return user


def require_participant(user: User = Depends(require_user)) -> User:
    if user.role != ROLE_PARTICIPANT:
        raise Forbidden("This area is for participants.")
    return user


def dashboard_url(user: User) -> str:
    if user.role == ROLE_ADMIN:
        return "/admin"
    if user.role == ROLE_HOST:
        return "/host"
    if user.role == ROLE_PARTICIPANT:
        return "/dashboard"
    return "/"
