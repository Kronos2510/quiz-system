"""JSON endpoints used by the quiz-taking interface."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import Forbidden, require_participant
from ..models import ATTEMPT_IN_PROGRESS, Question, User
from ..services.quiz_service import is_expired, remaining_seconds, save_answer, submit_attempt
from ..models import Attempt

router = APIRouter(prefix="/api")


class AnswerPayload(BaseModel):
    question_id: int
    option_id: int | None = None
    text: str | None = None


def _attempt_for(db: Session, attempt_id: int, user: User) -> Attempt:
    attempt = db.get(Attempt, attempt_id)
    if not attempt:
        raise HTTPException(status_code=404, detail="Attempt not found.")
    if attempt.user_id != user.id:
        raise Forbidden("That attempt belongs to another participant.")
    return attempt


@router.post("/attempts/{attempt_id}/answer")
def save_attempt_answer(
    attempt_id: int,
    payload: AnswerPayload,
    user: User = Depends(require_participant),
    db: Session = Depends(get_db),
):
    attempt = _attempt_for(db, attempt_id, user)

    if attempt.status != ATTEMPT_IN_PROGRESS:
        return JSONResponse(
            {"ok": False, "submitted": True, "redirect": f"/attempt/{attempt.id}/result"},
            status_code=409,
        )

    if is_expired(attempt):
        submit_attempt(db, attempt)
        db.commit()
        return JSONResponse(
            {"ok": False, "expired": True, "redirect": f"/attempt/{attempt.id}/result"},
            status_code=409,
        )

    question = db.get(Question, payload.question_id)
    if not question or question.quiz_id != attempt.quiz_id:
        raise HTTPException(status_code=404, detail="Question not found.")

    save_answer(
        db,
        attempt,
        question,
        selected_option_id=payload.option_id,
        text_answer=payload.text,
    )
    db.commit()

    return {
        "ok": True,
        "remaining": remaining_seconds(attempt),
        "answered": sum(
            1
            for answer in attempt.answers
            if answer.selected_option_id or (answer.text_answer or "").strip()
        ),
    }


@router.get("/attempts/{attempt_id}/state")
def attempt_state(
    attempt_id: int,
    user: User = Depends(require_participant),
    db: Session = Depends(get_db),
):
    attempt = _attempt_for(db, attempt_id, user)
    if attempt.status != ATTEMPT_IN_PROGRESS:
        return {
            "status": attempt.status,
            "redirect": f"/attempt/{attempt.id}/result",
        }
    if is_expired(attempt):
        submit_attempt(db, attempt)
        db.commit()
        return {
            "status": attempt.status,
            "expired": True,
            "redirect": f"/attempt/{attempt.id}/result",
        }
    return {
        "status": attempt.status,
        "remaining": remaining_seconds(attempt),
        "answered": sum(
            1
            for answer in attempt.answers
            if answer.selected_option_id or (answer.text_answer or "").strip()
        ),
    }
