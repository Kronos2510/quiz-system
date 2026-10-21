"""Participant dashboard, join flow, attempt taking and results."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import Forbidden, require_participant
from ..models import (
    ATTEMPT_GRADED,
    ATTEMPT_IN_PROGRESS,
    ATTEMPT_SUBMITTED,
    QUESTION_MCQ,
    STATUS_APPROVED,
    Enrollment,
    Quiz,
    User,
)
from ..security import normalize_code
from ..services.quiz_service import (
    active_attempt,
    attempt_count,
    create_attempt,
    is_expired,
    ordered_options,
    ordered_questions,
    remaining_seconds,
    save_answer,
    submit_attempt,
)
from ..templating import add_flash, render

router = APIRouter()


def get_enrollment(db: Session, quiz: Quiz, user: User) -> Enrollment | None:
    return db.scalar(
        select(Enrollment).where(Enrollment.quiz_id == quiz.id, Enrollment.user_id == user.id)
    )


def get_owned_attempt(db: Session, attempt_id: int, user: User):
    from ..models import Attempt

    attempt = db.get(Attempt, attempt_id)
    if not attempt:
        raise HTTPException(status_code=404, detail="Attempt not found.")
    if attempt.user_id != user.id:
        raise Forbidden("That attempt belongs to another participant.")
    return attempt


def _attempt_cards(db: Session, user: User) -> list[dict]:
    enrollments = db.scalars(
        select(Enrollment).where(Enrollment.user_id == user.id).order_by(Enrollment.created_at.desc())
    ).all()

    cards = []
    for enrollment in enrollments:
        quiz = enrollment.quiz
        attempts = sorted(
            [a for a in quiz.attempts if a.user_id == user.id],
            key=lambda a: a.started_at,
            reverse=True,
        )
        active = next((a for a in attempts if a.status == ATTEMPT_IN_PROGRESS), None)
        graded = [a for a in attempts if a.status == ATTEMPT_GRADED]
        best = max((a.percentage for a in graded), default=None) if quiz.results_released else None
        cards.append(
            {
                "quiz": quiz,
                "attempts": attempts,
                "active": active,
                "best": best,
                "used": len([a for a in attempts if a.status != ATTEMPT_IN_PROGRESS]),
                "remaining": max(0, quiz.max_attempts - len([a for a in attempts if a.status != ATTEMPT_IN_PROGRESS])),
                "can_start": quiz.is_open
                and len([a for a in attempts if a.status != ATTEMPT_IN_PROGRESS]) < quiz.max_attempts
                and not active,
            }
        )
    return cards


@router.get("/dashboard")
def participant_dashboard(
    request: Request,
    user: User = Depends(require_participant),
    db: Session = Depends(get_db),
):
    cards = _attempt_cards(db, user)
    graded = [card for card in cards if card["best"] is not None]
    average = round(sum(c["best"] for c in graded) / len(graded), 1) if graded else None
    pending_release = sum(
        1
        for card in cards
        for attempt in card["attempts"]
        if attempt.status == ATTEMPT_SUBMITTED
    )
    return render(
        request,
        "participant/dashboard.html",
        cards=cards,
        average=average,
        total_attempts=sum(len(card["attempts"]) for card in cards),
        open_count=sum(1 for card in cards if card["quiz"].is_open),
        pending_release=pending_release,
    )


@router.get("/join")
def join_page(request: Request, code: str = "", user: User = Depends(require_participant)):
    return render(request, "participant/join.html", code=code)


@router.post("/join")
def join_submit(
    request: Request,
    code: str = Form(""),
    user: User = Depends(require_participant),
    db: Session = Depends(get_db),
):
    normalized = normalize_code(code)
    if len(normalized) < 4:
        add_flash(request, "Enter the full access code from your host.", "error")
        return render(request, "participant/join.html", status_code=400, code=code)

    expected = f"QZ-{normalized}"
    quiz = db.scalar(select(Quiz).where(Quiz.access_code == expected))
    if not quiz:
        add_flash(request, "That access code doesn't match any quiz.", "error")
        return render(request, "participant/join.html", status_code=404, code=code)

    if quiz.status != "published":
        add_flash(request, "That quiz isn't accepting participants right now.", "error")
        return render(request, "participant/join.html", status_code=400, code=code)

    if not get_enrollment(db, quiz, user):
        db.add(Enrollment(quiz_id=quiz.id, user_id=user.id))
        db.commit()
        add_flash(request, f"You're in — welcome to {quiz.title}.", "success")
    else:
        add_flash(request, f"You're already enrolled in {quiz.title}.", "info")

    return RedirectResponse(f"/quiz/{quiz.id}", status_code=303)


@router.get("/quiz/{quiz_id}")
def quiz_intro(
    quiz_id: int,
    request: Request,
    user: User = Depends(require_participant),
    db: Session = Depends(get_db),
):
    quiz = db.get(Quiz, quiz_id)
    if not quiz:
        raise HTTPException(status_code=404, detail="Quiz not found.")
    if not get_enrollment(db, quiz, user):
        add_flash(request, "Join the quiz with an access code first.", "info")
        return RedirectResponse("/dashboard", status_code=303)

    attempts = sorted(
        [a for a in quiz.attempts if a.user_id == user.id],
        key=lambda a: a.started_at,
        reverse=True,
    )
    active = next((a for a in attempts if a.status == ATTEMPT_IN_PROGRESS), None)
    used = len([a for a in attempts if a.status != ATTEMPT_IN_PROGRESS])
    can_start = quiz.is_open and used < quiz.max_attempts and bool(quiz.questions) and not active

    return render(
        request,
        "participant/quiz_intro.html",
        quiz=quiz,
        attempts=attempts,
        active=active,
        used=used,
        remaining=max(0, quiz.max_attempts - used),
        can_start=can_start,
    )


@router.post("/quiz/{quiz_id}/start")
def start_attempt(
    quiz_id: int,
    request: Request,
    user: User = Depends(require_participant),
    db: Session = Depends(get_db),
):
    quiz = db.get(Quiz, quiz_id)
    if not quiz:
        raise HTTPException(status_code=404, detail="Quiz not found.")
    if not get_enrollment(db, quiz, user):
        add_flash(request, "Join the quiz with an access code first.", "error")
        return RedirectResponse("/dashboard", status_code=303)

    existing = active_attempt(db, quiz.id, user.id)
    if existing:
        return RedirectResponse(f"/attempt/{existing.id}", status_code=303)

    if not quiz.is_open:
        add_flash(request, "This quiz isn't open for attempts right now.", "error")
        return RedirectResponse(f"/quiz/{quiz.id}", status_code=303)
    if not quiz.questions:
        add_flash(request, "This quiz has no questions yet.", "error")
        return RedirectResponse(f"/quiz/{quiz.id}", status_code=303)
    if attempt_count(db, quiz.id, user.id) >= quiz.max_attempts:
        add_flash(request, "You've used all of your attempts for this quiz.", "error")
        return RedirectResponse(f"/quiz/{quiz.id}", status_code=303)

    attempt = create_attempt(db, quiz, user)
    db.commit()
    add_flash(request, "Timer started. Good luck!", "success")
    return RedirectResponse(f"/attempt/{attempt.id}", status_code=303)


@router.get("/attempt/{attempt_id}")
def attempt_page(
    attempt_id: int,
    request: Request,
    user: User = Depends(require_participant),
    db: Session = Depends(get_db),
):
    attempt = get_owned_attempt(db, attempt_id, user)
    quiz = attempt.quiz

    if attempt.status != ATTEMPT_IN_PROGRESS:
        return RedirectResponse(f"/attempt/{attempt.id}/result", status_code=303)
    if is_expired(attempt):
        submit_attempt(db, attempt)
        db.commit()
        add_flash(request, "Time ran out — your saved answers were submitted automatically.", "info")
        return RedirectResponse(f"/attempt/{attempt.id}/result", status_code=303)

    questions = ordered_questions(attempt)
    blocks = []
    for index, question in enumerate(questions, start=1):
        answer = attempt.answer_for(question.id)
        blocks.append(
            {
                "number": index,
                "question": question,
                "options": ordered_options(attempt, question) if question.type == QUESTION_MCQ else [],
                "selected_option_id": answer.selected_option_id if answer else None,
                "text_answer": answer.text_answer if answer else "",
            }
        )

    return render(
        request,
        "participant/attempt.html",
        quiz=quiz,
        attempt=attempt,
        blocks=blocks,
        remaining=remaining_seconds(attempt),
        answered=sum(
            1
            for block in blocks
            if block["selected_option_id"] or (block["text_answer"] or "").strip()
        ),
    )


@router.post("/attempt/{attempt_id}/submit")
async def submit_attempt_route(
    attempt_id: int,
    request: Request,
    user: User = Depends(require_participant),
    db: Session = Depends(get_db),
):
    attempt = get_owned_attempt(db, attempt_id, user)
    if attempt.status == ATTEMPT_IN_PROGRESS:
        # The submitted form is authoritative. Persist it before scoring so a
        # dropped autosave request can never lose the participant's last edits.
        form = await request.form()
        questions = {q.id: q for q in ordered_questions(attempt)}
        for key in form.keys():
            if not key.startswith("q_"):
                continue
            try:
                question_id = int(key[2:])
            except (TypeError, ValueError):
                continue
            question = questions.get(question_id)
            if question is None:
                continue
            if question.type == QUESTION_MCQ:
                raw = form.get(key)
                try:
                    option_id = int(raw) if raw not in (None, "") else None
                except (TypeError, ValueError):
                    option_id = None
                save_answer(db, attempt, question, selected_option_id=option_id)
            else:
                save_answer(db, attempt, question, text_answer=str(form.get(key) or ""))

        submit_attempt(db, attempt)
        db.commit()
        add_flash(request, "Answers submitted. Your host will take it from here.", "success")
    return RedirectResponse(f"/attempt/{attempt.id}/result", status_code=303)


@router.get("/attempt/{attempt_id}/result")
def result_page(
    attempt_id: int,
    request: Request,
    user: User = Depends(require_participant),
    db: Session = Depends(get_db),
):
    attempt = get_owned_attempt(db, attempt_id, user)
    quiz = attempt.quiz

    released = quiz.results_released and attempt.status == ATTEMPT_GRADED
    questions = ordered_questions(attempt)
    rows = []
    for index, question in enumerate(questions, start=1):
        answer = attempt.answer_for(question.id)
        rows.append(
            {
                "number": index,
                "question": question,
                "answer": answer,
                "selected_text": (
                    answer.selected_option.text if answer and answer.selected_option else None
                ),
                "points": answer.final_points if answer else 0,
                "correct": answer.is_correct if answer else None,
            }
        )

    passed = released and attempt.percentage >= quiz.pass_percent
    mcq_count = sum(1 for question in questions if question.type == QUESTION_MCQ)
    return render(
        request,
        "participant/result.html",
        quiz=quiz,
        attempt=attempt,
        rows=rows,
        released=released,
        passed=passed,
        awaiting_grade=attempt.status == ATTEMPT_SUBMITTED,
        not_released=quiz.results_released is False,
        mcq_count=mcq_count,
        written_count=len(questions) - mcq_count,
    )
