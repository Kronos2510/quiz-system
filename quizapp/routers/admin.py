"""Platform admin: approve hosts and manage accounts."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import require_admin
from ..models import (
    ROLE_ADMIN,
    ROLE_HOST,
    ROLE_PARTICIPANT,
    STATUS_APPROVED,
    STATUS_DISABLED,
    STATUS_PENDING,
    STATUS_REJECTED,
    AuditLog,
    Quiz,
    User,
)
from ..security import hash_password
from ..templating import add_flash, render

router = APIRouter(prefix="/admin")


def log(db: Session, actor: User, action: str, detail: str) -> None:
    db.add(AuditLog(actor_id=actor.id, action=action, detail=detail))


@router.get("")
def admin_dashboard(
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    pending_hosts = db.scalars(
        select(User).where(User.role == ROLE_HOST, User.status == STATUS_PENDING).order_by(User.created_at)
    ).all()
    pending_participants = db.scalars(
        select(User).where(User.role == ROLE_PARTICIPANT, User.status == STATUS_PENDING).order_by(User.created_at)
    ).all()

    counts = {
        "hosts": db.scalar(select(func.count(User.id)).where(User.role == ROLE_HOST, User.status == STATUS_APPROVED)) or 0,
        "participants": db.scalar(
            select(func.count(User.id)).where(User.role == ROLE_PARTICIPANT, User.status == STATUS_APPROVED)
        )
        or 0,
        "quizzes": db.scalar(select(func.count(Quiz.id))) or 0,
        "pending": len(pending_hosts) + len(pending_participants),
    }

    recent = db.scalars(select(User).order_by(User.created_at.desc()).limit(8)).all()
    audit = db.scalars(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(8)).all()

    return render(
        request,
        "admin/dashboard.html",
        pending_hosts=pending_hosts,
        pending_participants=pending_participants,
        counts=counts,
        recent=recent,
        audit=audit,
    )


@router.get("/users")
def admin_users(
    request: Request,
    q: str = "",
    role: str = "",
    status: str = "",
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    stmt = select(User)
    if q.strip():
        term = f"%{q.strip().lower()}%"
        stmt = stmt.where(or_(func.lower(User.email).like(term), func.lower(User.name).like(term)))
    if role in {ROLE_ADMIN, ROLE_HOST, ROLE_PARTICIPANT}:
        stmt = stmt.where(User.role == role)
    if status in {STATUS_PENDING, STATUS_APPROVED, STATUS_REJECTED, STATUS_DISABLED}:
        stmt = stmt.where(User.status == status)

    users = db.scalars(stmt.order_by(User.created_at.desc()).limit(200)).all()
    return render(request, "admin/users.html", users=users, q=q, role=role, status=status)


@router.post("/users/{user_id}/approve")
def approve_user(
    user_id: int,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    target = db.get(User, user_id)
    if not target or target.role == ROLE_ADMIN:
        raise HTTPException(status_code=404, detail="Account not found.")
    if target.status != STATUS_PENDING:
        add_flash(request, "That account is not waiting for approval.", "info")
        return RedirectResponse("/admin", status_code=303)

    target.status = STATUS_APPROVED
    target.approved_by = admin.id
    log(db, admin, "user.approved", f"Approved {target.role} {target.email}.")
    db.commit()
    add_flash(request, f"{target.name} is approved and can now sign in.", "success")
    return RedirectResponse("/admin", status_code=303)


@router.post("/users/{user_id}/reject")
def reject_user(
    user_id: int,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    target = db.get(User, user_id)
    if not target or target.role == ROLE_ADMIN:
        raise HTTPException(status_code=404, detail="Account not found.")
    if target.status != STATUS_PENDING:
        add_flash(request, "That account is not waiting for approval.", "info")
        return RedirectResponse("/admin", status_code=303)

    target.status = STATUS_REJECTED
    log(db, admin, "user.rejected", f"Rejected {target.role} {target.email}.")
    db.commit()
    add_flash(request, f"{target.name}'s registration was rejected.", "info")
    return RedirectResponse("/admin", status_code=303)


@router.post("/users/{user_id}/disable")
def disable_user(
    user_id: int,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    target = db.get(User, user_id)
    if not target or target.role == ROLE_ADMIN:
        raise HTTPException(status_code=404, detail="Account not found.")
    target.status = STATUS_DISABLED
    log(db, admin, "user.disabled", f"Disabled {target.email}.")
    db.commit()
    add_flash(request, f"{target.name}'s account is disabled.", "info")
    return RedirectResponse("/admin/users", status_code=303)


@router.post("/users/{user_id}/enable")
def enable_user(
    user_id: int,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    target = db.get(User, user_id)
    if not target or target.role == ROLE_ADMIN:
        raise HTTPException(status_code=404, detail="Account not found.")
    target.status = STATUS_APPROVED
    log(db, admin, "user.enabled", f"Enabled {target.email}.")
    db.commit()
    add_flash(request, f"{target.name}'s account is active again.", "success")
    return RedirectResponse("/admin/users", status_code=303)


@router.post("/users/{user_id}/reset-password")
def reset_password(
    user_id: int,
    request: Request,
    password: str = Form(""),
    confirm: str = Form(""),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    target = db.get(User, user_id)
    if not target:
        raise HTTPException(status_code=404, detail="Account not found.")

    if len(password) < 8:
        add_flash(request, "Passwords need at least 8 characters.", "error")
        return RedirectResponse("/admin/users", status_code=303)
    if password != confirm:
        add_flash(request, "The two passwords don't match.", "error")
        return RedirectResponse("/admin/users", status_code=303)

    target.password_hash = hash_password(password)
    log(db, admin, "user.password_reset", f"Reset password for {target.email}.")
    db.commit()
    add_flash(request, f"Password updated for {target.name}.", "success")
    return RedirectResponse("/admin/users", status_code=303)
