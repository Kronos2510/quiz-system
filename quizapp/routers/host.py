"""Host workspace: quiz builder, submissions, grading and results."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import Forbidden, require_host
from ..models import (
    ATTEMPT_GRADED,
    ATTEMPT_IN_PROGRESS,
    ATTEMPT_SUBMITTED,
    QUESTION_MCQ,
    QUESTION_WRITTEN,
    QUIZ_CLOSED,
    QUIZ_DRAFT,
    QUIZ_PUBLISHED,
    ROLE_PARTICIPANT,
    STATUS_APPROVED,
    STATUS_PENDING,
    STATUS_REJECTED,
    Answer,
    Attempt,
    AuditLog,
    Enrollment,
    Option,
    Question,
    Quiz,
    User,
    utcnow,
)
from ..security import generate_code, normalize_code
from ..services.export import results_csv
from ..services.quiz_service import (
    finalize_attempt,
    ordered_questions,
    question_stats,
    quiz_stats,
    submit_attempt,
)
from ..templating import add_flash, render

router = APIRouter(prefix="/host")

STATUS_FILTERS = {"all", "submitted", "graded", "in_progress"}


def get_owned_quiz(db: Session, quiz_id: int, host: User) -> Quiz:
    quiz = db.get(Quiz, quiz_id)
    if not quiz:
        raise HTTPException(status_code=404, detail="Quiz not found.")
    if quiz.host_id != host.id:
        raise Forbidden("That quiz belongs to another host.")
    return quiz


def attempts_locked(db: Session, quiz_id: int) -> bool:
    return bool(db.scalar(select(func.count(Attempt.id)).where(Attempt.quiz_id == quiz_id)))


def log(db: Session, actor: User, action: str, detail: str) -> None:
    db.add(AuditLog(actor_id=actor.id, action=action, detail=detail))


def parse_int(value: str, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


def parse_optional_int(value: str, minimum: int, maximum: int) -> int | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        parsed = int(text)
    except ValueError:
        return None
    return max(minimum, min(maximum, parsed))


def parse_datetime_local(value: str) -> datetime | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def quiz_settings_from_form(form: Any, quiz: Quiz | None = None) -> tuple[dict, list[str]]:
    errors: list[str] = []
    title = (form.get("title") or "").strip()
    if len(title) < 3:
        errors.append("Give the quiz a title with at least 3 characters.")
    if len(title) > 200:
        errors.append("Keep the title under 200 characters.")

    description = (form.get("description") or "").strip()[:5000]
    instructions = (form.get("instructions") or "").strip()[:5000]
    max_attempts = parse_int(form.get("max_attempts", "1"), 1, 1, 10)
    pass_percent = parse_int(form.get("pass_percent", "50"), 50, 0, 100)
    time_limit = parse_optional_int(form.get("time_limit_minutes", ""), 1, 600)

    opens_raw = form.get("opens_at", "")
    closes_raw = form.get("closes_at", "")
    opens_at = parse_datetime_local(opens_raw)
    closes_at = parse_datetime_local(closes_raw)
    if (opens_raw or "").strip() and opens_at is None:
        errors.append("The opening date is not valid.")
    if (closes_raw or "").strip() and closes_at is None:
        errors.append("The closing date is not valid.")
    if opens_at and closes_at and closes_at <= opens_at:
        errors.append("The closing time must be after the opening time.")

    data = {
        "title": title,
        "description": description,
        "instructions": instructions,
        "max_attempts": max_attempts,
        "pass_percent": pass_percent,
        "time_limit_minutes": time_limit,
        "opens_at": opens_at,
        "closes_at": closes_at,
        "shuffle_questions": form.get("shuffle_questions") is not None,
        "shuffle_options": form.get("shuffle_options") is not None,
    }
    return data, errors


@router.get("")
def host_dashboard(
    request: Request,
    host: User = Depends(require_host),
    db: Session = Depends(get_db),
):
    quizzes = db.scalars(
        select(Quiz).where(Quiz.host_id == host.id).order_by(Quiz.updated_at.desc())
    ).all()

    cards = []
    for quiz in quizzes:
        attempts = quiz.attempts
        cards.append(
            {
                "quiz": quiz,
                "enrolled": len(quiz.enrollments),
                "submitted": sum(1 for a in attempts if a.status != ATTEMPT_IN_PROGRESS),
                "to_grade": sum(1 for a in attempts if a.status == ATTEMPT_SUBMITTED),
                "graded": sum(1 for a in attempts if a.status == ATTEMPT_GRADED),
            }
        )

    pending_participants = db.scalars(
        select(User)
        .where(User.role == ROLE_PARTICIPANT, User.status == STATUS_PENDING)
        .order_by(User.created_at)
    ).all()

    approved_participants = (
        db.scalar(
            select(func.count(User.id)).where(
                User.role == ROLE_PARTICIPANT, User.status == STATUS_APPROVED
            )
        )
        or 0
    )

    recent_attempts = db.scalars(
        select(Attempt)
        .join(Quiz, Attempt.quiz_id == Quiz.id)
        .where(Quiz.host_id == host.id, Attempt.status != ATTEMPT_IN_PROGRESS)
        .order_by(Attempt.submitted_at.desc())
        .limit(8)
    ).all()

    totals = {
        "quizzes": len(quizzes),
        "participants": approved_participants,
        "to_grade": sum(card["to_grade"] for card in cards),
        "submitted": sum(card["submitted"] for card in cards),
    }

    return render(
        request,
        "host/dashboard.html",
        cards=cards,
        pending_participants=pending_participants,
        recent_attempts=recent_attempts,
        totals=totals,
    )


@router.get("/quizzes/new")
def new_quiz_page(request: Request, host: User = Depends(require_host)):
    return render(request, "host/quiz_new.html", form={})


@router.post("/quizzes/new")
async def create_quiz(
    request: Request,
    host: User = Depends(require_host),
    db: Session = Depends(get_db),
):
    form = await request.form()
    data, errors = quiz_settings_from_form(form)
    if errors:
        return render(
            request,
            "host/quiz_new.html",
            status_code=400,
            form=form,
            errors=errors,
        )

    quiz = Quiz(host_id=host.id, access_code=generate_code(), status=QUIZ_DRAFT, **data)
    db.add(quiz)
    db.flush()
    log(db, host, "quiz.created", f"Created quiz '{quiz.title}' (#{quiz.id}).")
    db.commit()

    add_flash(request, "Draft created. Add your first question next.", "success")
    return RedirectResponse(f"/host/quizzes/{quiz.id}/edit", status_code=303)


@router.get("/quizzes/{quiz_id}/edit")
def edit_quiz_page(
    quiz_id: int,
    request: Request,
    host: User = Depends(require_host),
    db: Session = Depends(get_db),
):
    quiz = get_owned_quiz(db, quiz_id, host)
    locked = attempts_locked(db, quiz.id)
    submissions = sum(1 for a in quiz.attempts if a.status != ATTEMPT_IN_PROGRESS)
    return render(
        request,
        "host/quiz_edit.html",
        quiz=quiz,
        locked=locked,
        submissions=submissions,
        enrolled=len(quiz.enrollments),
    )


@router.post("/quizzes/{quiz_id}")
async def update_quiz(
    quiz_id: int,
    request: Request,
    host: User = Depends(require_host),
    db: Session = Depends(get_db),
):
    quiz = get_owned_quiz(db, quiz_id, host)
    form = await request.form()
    data, errors = quiz_settings_from_form(form, quiz)
    if errors:
        add_flash(request, " ".join(errors), "error")
        return RedirectResponse(f"/host/quizzes/{quiz.id}/edit", status_code=303)

    for key, value in data.items():
        setattr(quiz, key, value)
    quiz.updated_at = utcnow()
    log(db, host, "quiz.updated", f"Updated settings for '{quiz.title}'.")
    db.commit()
    add_flash(request, "Quiz settings saved.", "success")
    return RedirectResponse(f"/host/quizzes/{quiz.id}/edit", status_code=303)


@router.post("/quizzes/{quiz_id}/delete")
def delete_quiz(
    quiz_id: int,
    request: Request,
    host: User = Depends(require_host),
    db: Session = Depends(get_db),
):
    quiz = get_owned_quiz(db, quiz_id, host)
    if attempts_locked(db, quiz.id):
        add_flash(request, "This quiz has attempts, so it can't be deleted. Close it instead.", "error")
        return RedirectResponse(f"/host/quizzes/{quiz.id}/edit", status_code=303)

    db.delete(quiz)
    log(db, host, "quiz.deleted", f"Deleted '{quiz.title}' (#{quiz.id}).")
    db.commit()
    add_flash(request, "Quiz deleted.", "info")
    return RedirectResponse("/host", status_code=303)


@router.post("/quizzes/{quiz_id}/publish")
def publish_quiz(
    quiz_id: int,
    request: Request,
    host: User = Depends(require_host),
    db: Session = Depends(get_db),
):
    quiz = get_owned_quiz(db, quiz_id, host)
    if not quiz.questions:
        add_flash(request, "Add at least one question before publishing.", "error")
        return RedirectResponse(f"/host/quizzes/{quiz.id}/edit", status_code=303)
    if any(q.type == QUESTION_MCQ and not q.correct_option for q in quiz.questions):
        add_flash(request, "Every multiple-choice question needs a correct option.", "error")
        return RedirectResponse(f"/host/quizzes/{quiz.id}/edit", status_code=303)

    quiz.status = QUIZ_PUBLISHED
    quiz.updated_at = utcnow()
    log(db, host, "quiz.published", f"Published '{quiz.title}'.")
    db.commit()
    add_flash(request, f"Published. Share code {quiz.access_code} with your participants.", "success")
    return RedirectResponse(f"/host/quizzes/{quiz.id}/edit", status_code=303)


@router.post("/quizzes/{quiz_id}/close")
def close_quiz(
    quiz_id: int,
    request: Request,
    host: User = Depends(require_host),
    db: Session = Depends(get_db),
):
    quiz = get_owned_quiz(db, quiz_id, host)
    quiz.status = QUIZ_CLOSED
    quiz.updated_at = utcnow()
    log(db, host, "quiz.closed", f"Closed '{quiz.title}'.")
    db.commit()
    add_flash(request, "Quiz closed. No new attempts can start.", "info")
    return RedirectResponse(f"/host/quizzes/{quiz.id}/edit", status_code=303)


@router.post("/quizzes/{quiz_id}/reopen")
def reopen_quiz(
    quiz_id: int,
    request: Request,
    host: User = Depends(require_host),
    db: Session = Depends(get_db),
):
    quiz = get_owned_quiz(db, quiz_id, host)
    quiz.status = QUIZ_PUBLISHED
    quiz.updated_at = utcnow()
    log(db, host, "quiz.reopened", f"Reopened '{quiz.title}'.")
    db.commit()
    add_flash(request, "Quiz reopened.", "success")
    return RedirectResponse(f"/host/quizzes/{quiz.id}/edit", status_code=303)


@router.post("/quizzes/{quiz_id}/rotate-code")
def rotate_code(
    quiz_id: int,
    request: Request,
    host: User = Depends(require_host),
    db: Session = Depends(get_db),
):
    quiz = get_owned_quiz(db, quiz_id, host)
    while True:
        code = generate_code()
        if not db.scalar(select(Quiz.id).where(Quiz.access_code == code)):
            break
    quiz.access_code = code
    log(db, host, "quiz.code_rotated", f"Rotated access code for '{quiz.title}'.")
    db.commit()
    add_flash(request, f"New access code: {code}", "success")
    return RedirectResponse(f"/host/quizzes/{quiz.id}/edit", status_code=303)


@router.post("/quizzes/{quiz_id}/release")
def release_results(
    quiz_id: int,
    request: Request,
    release: str = "1",
    host: User = Depends(require_host),
    db: Session = Depends(get_db),
):
    quiz = get_owned_quiz(db, quiz_id, host)
    quiz.results_released = release == "1"
    action = "quiz.results_released" if quiz.results_released else "quiz.results_hidden"
    log(db, host, action, f"Results visibility for '{quiz.title}' set to {quiz.results_released}.")
    db.commit()
    add_flash(
        request,
        "Results are now visible to participants." if quiz.results_released else "Results hidden from participants.",
        "success" if quiz.results_released else "info",
    )
    return RedirectResponse(f"/host/quizzes/{quiz.id}/results", status_code=303)


# ---------------------------------------------------------------------------
# Questions
# ---------------------------------------------------------------------------


def _question_form_context(quiz: Quiz, question: Question | None, qtype: str) -> dict:
    if question is not None:
        qtype = question.type
    return {"quiz": quiz, "question": question, "qtype": qtype}


@router.get("/quizzes/{quiz_id}/questions/new")
def new_question_page(
    quiz_id: int,
    request: Request,
    type: str = QUESTION_MCQ,
    host: User = Depends(require_host),
    db: Session = Depends(get_db),
):
    quiz = get_owned_quiz(db, quiz_id, host)
    if attempts_locked(db, quiz.id):
        add_flash(request, "Questions are locked because attempts already exist.", "error")
        return RedirectResponse(f"/host/quizzes/{quiz.id}/edit", status_code=303)
    qtype = QUESTION_WRITTEN if type == QUESTION_WRITTEN else QUESTION_MCQ
    return render(request, "host/question_form.html", **_question_form_context(quiz, None, qtype))


@router.post("/quizzes/{quiz_id}/questions/new")
async def create_question(
    quiz_id: int,
    request: Request,
    host: User = Depends(require_host),
    db: Session = Depends(get_db),
):
    quiz = get_owned_quiz(db, quiz_id, host)
    if attempts_locked(db, quiz.id):
        add_flash(request, "Questions are locked because attempts already exist.", "error")
        return RedirectResponse(f"/host/quizzes/{quiz.id}/edit", status_code=303)

    form = await request.form()
    qtype = QUESTION_WRITTEN if form.get("type") == QUESTION_WRITTEN else QUESTION_MCQ
    prompt = (form.get("prompt") or "").strip()
    points = parse_int(form.get("points", "1"), 1, 1, 100)
    explanation = (form.get("explanation") or "").strip()[:5000]
    model_answer = (form.get("model_answer") or "").strip()[:5000]

    errors: list[str] = []
    if len(prompt) < 3:
        errors.append("Write a question prompt.")

    option_rows: list[tuple[str, bool]] = []
    if qtype == QUESTION_MCQ:
        texts = [t.strip() for t in form.getlist("option_text")]
        correct_raw = form.get("correct_option", "")
        try:
            correct_index = int(correct_raw)
        except (TypeError, ValueError):
            correct_index = -1

        non_empty = [(index, text) for index, text in enumerate(texts) if text]
        if len(non_empty) < 2:
            errors.append("Add at least two answer options.")
        if correct_index < 0 or correct_index >= len(texts) or not texts[correct_index]:
            errors.append("Mark exactly one option as correct.")
        option_rows = [
            (text, index == correct_index) for index, text in non_empty
        ]

    if errors:
        return render(
            request,
            "host/question_form.html",
            status_code=400,
            errors=errors,
            posted=form,
            **_question_form_context(quiz, None, qtype),
        )

    position = max((q.position for q in quiz.questions), default=-1) + 1
    question = Question(
        quiz_id=quiz.id,
        type=qtype,
        prompt=prompt,
        points=points,
        position=position,
        model_answer=model_answer,
        explanation=explanation,
    )
    if qtype == QUESTION_MCQ:
        for index, (text, is_correct) in enumerate(option_rows):
            question.options.append(Option(text=text, is_correct=is_correct, position=index))

    db.add(question)
    quiz.updated_at = utcnow()
    log(db, host, "question.created", f"Added a question to '{quiz.title}'.")
    db.commit()
    add_flash(request, "Question added.", "success")
    return RedirectResponse(f"/host/quizzes/{quiz.id}/edit", status_code=303)


@router.get("/quizzes/{quiz_id}/questions/{question_id}/edit")
def edit_question_page(
    quiz_id: int,
    question_id: int,
    request: Request,
    host: User = Depends(require_host),
    db: Session = Depends(get_db),
):
    quiz = get_owned_quiz(db, quiz_id, host)
    question = db.get(Question, question_id)
    if not question or question.quiz_id != quiz.id:
        raise HTTPException(status_code=404, detail="Question not found.")
    if attempts_locked(db, quiz.id):
        add_flash(request, "Questions are locked because attempts already exist.", "error")
        return RedirectResponse(f"/host/quizzes/{quiz.id}/edit", status_code=303)
    return render(request, "host/question_form.html", **_question_form_context(quiz, question, question.type))


@router.post("/quizzes/{quiz_id}/questions/{question_id}/edit")
async def update_question(
    quiz_id: int,
    question_id: int,
    request: Request,
    host: User = Depends(require_host),
    db: Session = Depends(get_db),
):
    quiz = get_owned_quiz(db, quiz_id, host)
    question = db.get(Question, question_id)
    if not question or question.quiz_id != quiz.id:
        raise HTTPException(status_code=404, detail="Question not found.")
    if attempts_locked(db, quiz.id):
        add_flash(request, "Questions are locked because attempts already exist.", "error")
        return RedirectResponse(f"/host/quizzes/{quiz.id}/edit", status_code=303)

    form = await request.form()
    qtype = QUESTION_WRITTEN if form.get("type") == QUESTION_WRITTEN else QUESTION_MCQ
    prompt = (form.get("prompt") or "").strip()
    points = parse_int(form.get("points", "1"), 1, 1, 100)
    explanation = (form.get("explanation") or "").strip()[:5000]
    model_answer = (form.get("model_answer") or "").strip()[:5000]

    errors: list[str] = []
    if len(prompt) < 3:
        errors.append("Write a question prompt.")

    option_rows: list[tuple[str, bool]] = []
    if qtype == QUESTION_MCQ:
        texts = [t.strip() for t in form.getlist("option_text")]
        correct_raw = form.get("correct_option", "")
        try:
            correct_index = int(correct_raw)
        except (TypeError, ValueError):
            correct_index = -1
        non_empty = [(index, text) for index, text in enumerate(texts) if text]
        if len(non_empty) < 2:
            errors.append("Add at least two answer options.")
        if correct_index < 0 or correct_index >= len(texts) or not texts[correct_index]:
            errors.append("Mark exactly one option as correct.")
        option_rows = [(text, index == correct_index) for index, text in non_empty]

    if errors:
        return render(
            request,
            "host/question_form.html",
            status_code=400,
            errors=errors,
            posted=form,
            **_question_form_context(quiz, question, qtype),
        )

    question.type = qtype
    question.prompt = prompt
    question.points = points
    question.explanation = explanation
    question.model_answer = model_answer
    question.options.clear()
    if qtype == QUESTION_MCQ:
        for index, (text, is_correct) in enumerate(option_rows):
            question.options.append(Option(text=text, is_correct=is_correct, position=index))

    quiz.updated_at = utcnow()
    log(db, host, "question.updated", f"Edited a question in '{quiz.title}'.")
    db.commit()
    add_flash(request, "Question updated.", "success")
    return RedirectResponse(f"/host/quizzes/{quiz.id}/edit", status_code=303)


@router.post("/quizzes/{quiz_id}/questions/{question_id}/delete")
def delete_question(
    quiz_id: int,
    question_id: int,
    request: Request,
    host: User = Depends(require_host),
    db: Session = Depends(get_db),
):
    quiz = get_owned_quiz(db, quiz_id, host)
    question = db.get(Question, question_id)
    if not question or question.quiz_id != quiz.id:
        raise HTTPException(status_code=404, detail="Question not found.")
    if attempts_locked(db, quiz.id):
        add_flash(request, "Questions are locked because attempts already exist.", "error")
        return RedirectResponse(f"/host/quizzes/{quiz.id}/edit", status_code=303)

    db.delete(question)
    db.flush()
    remaining = sorted(quiz.questions, key=lambda q: q.position)
    for index, item in enumerate(remaining):
        item.position = index
    quiz.updated_at = utcnow()
    log(db, host, "question.deleted", f"Deleted a question from '{quiz.title}'.")
    db.commit()
    add_flash(request, "Question deleted.", "info")
    return RedirectResponse(f"/host/quizzes/{quiz.id}/edit", status_code=303)


@router.post("/quizzes/{quiz_id}/questions/{question_id}/move")
def move_question(
    quiz_id: int,
    question_id: int,
    request: Request,
    direction: str = "up",
    host: User = Depends(require_host),
    db: Session = Depends(get_db),
):
    quiz = get_owned_quiz(db, quiz_id, host)
    if attempts_locked(db, quiz.id):
        add_flash(request, "Questions are locked because attempts already exist.", "error")
        return RedirectResponse(f"/host/quizzes/{quiz.id}/edit", status_code=303)

    questions = sorted(quiz.questions, key=lambda q: q.position)
    index = next((i for i, q in enumerate(questions) if q.id == question_id), None)
    if index is None:
        raise HTTPException(status_code=404, detail="Question not found.")

    target = index - 1 if direction == "up" else index + 1
    if 0 <= target < len(questions):
        questions[index].position, questions[target].position = target, index
        db.commit()
    return RedirectResponse(f"/host/quizzes/{quiz.id}/edit", status_code=303)


# ---------------------------------------------------------------------------
# Submissions and grading
# ---------------------------------------------------------------------------


@router.get("/quizzes/{quiz_id}/submissions")
def submissions_page(
    quiz_id: int,
    request: Request,
    status: str = "all",
    host: User = Depends(require_host),
    db: Session = Depends(get_db),
):
    quiz = get_owned_quiz(db, quiz_id, host)
    if status not in STATUS_FILTERS:
        status = "all"

    attempts = list(quiz.attempts)
    if status != "all":
        wanted = {
            "submitted": ATTEMPT_SUBMITTED,
            "graded": ATTEMPT_GRADED,
            "in_progress": ATTEMPT_IN_PROGRESS,
        }[status]
        attempts = [a for a in attempts if a.status == wanted]

    attempts.sort(
        key=lambda a: a.submitted_at or a.started_at,
        reverse=True,
    )
    stats = quiz_stats(quiz, quiz.attempts)
    return render(
        request,
        "host/submissions.html",
        quiz=quiz,
        attempts=attempts,
        status=status,
        stats=stats,
    )


@router.get("/attempts/{attempt_id}/grade")
def grade_page(
    attempt_id: int,
    request: Request,
    host: User = Depends(require_host),
    db: Session = Depends(get_db),
):
    attempt = db.get(Attempt, attempt_id)
    if not attempt:
        raise HTTPException(status_code=404, detail="Attempt not found.")
    quiz = get_owned_quiz(db, attempt.quiz_id, host)
    if attempt.status == ATTEMPT_IN_PROGRESS:
        add_flash(request, "That attempt is still in progress.", "info")
        return RedirectResponse(f"/host/quizzes/{quiz.id}/submissions", status_code=303)

    questions = ordered_questions(attempt)
    answers = {q.id: attempt.answer_for(q.id) for q in questions}
    return render(
        request,
        "host/grade.html",
        quiz=quiz,
        attempt=attempt,
        questions=questions,
        answers=answers,
        posted={},
    )


@router.post("/attempts/{attempt_id}/grade")
async def grade_submit(
    attempt_id: int,
    request: Request,
    host: User = Depends(require_host),
    db: Session = Depends(get_db),
):
    attempt = db.get(Attempt, attempt_id)
    if not attempt:
        raise HTTPException(status_code=404, detail="Attempt not found.")
    quiz = get_owned_quiz(db, attempt.quiz_id, host)
    if attempt.status == ATTEMPT_IN_PROGRESS:
        add_flash(request, "That attempt is still in progress.", "info")
        return RedirectResponse(f"/host/quizzes/{quiz.id}/submissions", status_code=303)

    form = await request.form()
    questions = ordered_questions(attempt)
    errors: list[str] = []

    parsed: dict[int, tuple[int | None, str]] = {}
    for question in questions:
        if question.type != QUESTION_WRITTEN:
            continue
        raw = (form.get(f"points_{question.id}") or "").strip()
        feedback = (form.get(f"feedback_{question.id}") or "").strip()[:5000]
        if raw == "":
            parsed[question.id] = (None, feedback)
            continue
        try:
            value = int(raw)
        except ValueError:
            errors.append(f"Q{questions.index(question) + 1}: enter a whole number of points.")
            continue
        if value < 0 or value > question.points:
            errors.append(f"Q{questions.index(question) + 1}: score must be between 0 and {question.points}.")
            continue
        parsed[question.id] = (value, feedback)

    action = form.get("action", "save")
    if errors:
        answers = {q.id: attempt.answer_for(q.id) for q in questions}
        return render(
            request,
            "host/grade.html",
            status_code=400,
            quiz=quiz,
            attempt=attempt,
            questions=questions,
            answers=answers,
            posted=form,
            errors=errors,
        )

    for question_id, (points, feedback) in parsed.items():
        answer = attempt.answer_for(question_id)
        if answer is None:
            answer = Answer(attempt_id=attempt.id, question_id=question_id)
            db.add(answer)
        answer.manual_points = points
        answer.feedback = feedback
        answer.graded_by = host.id
        answer.graded_at = utcnow()

    if action == "finalize":
        try:
            finalize_attempt(attempt, host)
        except ValueError as exc:
            db.commit()
            add_flash(request, str(exc), "error")
            return RedirectResponse(f"/host/attempts/{attempt.id}/grade", status_code=303)
        add_flash(request, f"Attempt finalized with {attempt.score}/{attempt.max_score}.", "success")
    else:
        if attempt.status == ATTEMPT_GRADED:
            attempt.score = sum(a.final_points for a in attempt.answers)
        add_flash(request, "Grading progress saved.", "success")

    log(db, host, "attempt.graded", f"Grading saved for attempt #{attempt.id} in '{quiz.title}'.")
    db.commit()
    return RedirectResponse(f"/host/quizzes/{quiz.id}/submissions", status_code=303)


# ---------------------------------------------------------------------------
# Results and export
# ---------------------------------------------------------------------------


@router.get("/quizzes/{quiz_id}/results")
def results_page(
    quiz_id: int,
    request: Request,
    host: User = Depends(require_host),
    db: Session = Depends(get_db),
):
    quiz = get_owned_quiz(db, quiz_id, host)
    attempts = [a for a in quiz.attempts if a.status != ATTEMPT_IN_PROGRESS]
    attempts.sort(key=lambda a: a.percentage, reverse=True)
    stats = quiz_stats(quiz, quiz.attempts)
    breakdown = question_stats(quiz, [a for a in quiz.attempts if a.status == ATTEMPT_GRADED])

    leaderboard = sorted(
        [a for a in attempts if a.status == ATTEMPT_GRADED],
        key=lambda a: (-a.score, a.submitted_at or a.started_at),
    )

    return render(
        request,
        "host/results.html",
        quiz=quiz,
        attempts=attempts,
        stats=stats,
        breakdown=breakdown,
        leaderboard=leaderboard,
    )


@router.get("/quizzes/{quiz_id}/export.csv")
def export_results(
    quiz_id: int,
    host: User = Depends(require_host),
    db: Session = Depends(get_db),
):
    quiz = get_owned_quiz(db, quiz_id, host)
    attempts = [a for a in quiz.attempts if a.status != ATTEMPT_IN_PROGRESS]
    attempts.sort(key=lambda a: a.percentage, reverse=True)
    payload = results_csv(quiz, attempts)
    slug = "".join(ch if ch.isalnum() else "-" for ch in quiz.title.lower()).strip("-")[:40] or f"quiz-{quiz.id}"
    return Response(
        content=payload,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{slug}-results.csv"'},
    )


# ---------------------------------------------------------------------------
# Participant approvals
# ---------------------------------------------------------------------------


@router.get("/participants")
def participants_page(
    request: Request,
    host: User = Depends(require_host),
    db: Session = Depends(get_db),
):
    pending = db.scalars(
        select(User)
        .where(User.role == ROLE_PARTICIPANT, User.status == STATUS_PENDING)
        .order_by(User.created_at)
    ).all()
    approved = db.scalars(
        select(User)
        .where(User.role == ROLE_PARTICIPANT, User.status == STATUS_APPROVED)
        .order_by(User.name)
    ).all()

    # Attempt counts across this host's quizzes.
    rows = db.execute(
        select(Attempt.user_id, func.count(Attempt.id))
        .join(Quiz, Attempt.quiz_id == Quiz.id)
        .where(Quiz.host_id == host.id)
        .group_by(Attempt.user_id)
    ).all()
    attempt_counts = {user_id: count for user_id, count in rows}

    return render(
        request,
        "host/participants.html",
        pending=pending,
        approved=approved,
        attempt_counts=attempt_counts,
    )


@router.post("/participants/{user_id}/approve")
def approve_participant(
    user_id: int,
    request: Request,
    host: User = Depends(require_host),
    db: Session = Depends(get_db),
):
    target = db.get(User, user_id)
    if not target or target.role != ROLE_PARTICIPANT:
        raise HTTPException(status_code=404, detail="Participant not found.")
    if target.status != STATUS_PENDING:
        add_flash(request, "That participant is not waiting for approval.", "info")
        return RedirectResponse("/host/participants", status_code=303)

    target.status = STATUS_APPROVED
    target.approved_by = host.id
    log(db, host, "participant.approved", f"Approved participant {target.email}.")
    db.commit()
    add_flash(request, f"{target.name} can now sign in and join quizzes.", "success")
    return RedirectResponse("/host/participants", status_code=303)


@router.post("/participants/{user_id}/reject")
def reject_participant(
    user_id: int,
    request: Request,
    host: User = Depends(require_host),
    db: Session = Depends(get_db),
):
    target = db.get(User, user_id)
    if not target or target.role != ROLE_PARTICIPANT:
        raise HTTPException(status_code=404, detail="Participant not found.")
    if target.status != STATUS_PENDING:
        add_flash(request, "That participant is not waiting for approval.", "info")
        return RedirectResponse("/host/participants", status_code=303)

    target.status = STATUS_REJECTED
    log(db, host, "participant.rejected", f"Rejected participant {target.email}.")
    db.commit()
    add_flash(request, f"{target.name}'s registration was rejected.", "info")
    return RedirectResponse("/host/participants", status_code=303)
