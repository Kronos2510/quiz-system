"""Quiz and attempt lifecycle logic."""

from __future__ import annotations

import json
import random
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import (
    ATTEMPT_GRADED,
    ATTEMPT_IN_PROGRESS,
    ATTEMPT_SUBMITTED,
    QUESTION_MCQ,
    QUESTION_WRITTEN,
    Answer,
    Attempt,
    Option,
    Question,
    Quiz,
    User,
    utcnow,
)
from ..security import generate_code


def generate_unique_code(db: Session) -> str:
    for _ in range(40):
        code = generate_code()
        exists = db.scalar(select(Quiz.id).where(Quiz.access_code == code))
        if not exists:
            return code
    raise RuntimeError("Could not generate a unique access code")


def attempt_count(db: Session, quiz_id: int, user_id: int) -> int:
    return (
        db.scalar(
            select(func.count(Attempt.id)).where(
                Attempt.quiz_id == quiz_id,
                Attempt.user_id == user_id,
                Attempt.status != ATTEMPT_IN_PROGRESS,
            )
        )
        or 0
    )


def active_attempt(db: Session, quiz_id: int, user_id: int) -> Attempt | None:
    return db.scalar(
        select(Attempt).where(
            Attempt.quiz_id == quiz_id,
            Attempt.user_id == user_id,
            Attempt.status == ATTEMPT_IN_PROGRESS,
        )
    )


def create_attempt(db: Session, quiz: Quiz, user: User) -> Attempt:
    questions = list(quiz.questions)
    question_ids = [q.id for q in questions]
    if quiz.shuffle_questions:
        random.SystemRandom().shuffle(question_ids)

    by_id = {q.id: q for q in questions}
    option_order: dict[str, list[int]] = {}
    for question_id in question_ids:
        question = by_id[question_id]
        if question.type != QUESTION_MCQ:
            continue
        option_ids = [o.id for o in question.options]
        if quiz.shuffle_options:
            random.SystemRandom().shuffle(option_ids)
        option_order[str(question_id)] = option_ids

    now = utcnow()
    deadline = None
    if quiz.time_limit_minutes:
        deadline = now + timedelta(minutes=quiz.time_limit_minutes)
        if quiz.closes_at and deadline > quiz.closes_at:
            deadline = quiz.closes_at

    attempt = Attempt(
        quiz_id=quiz.id,
        user_id=user.id,
        status=ATTEMPT_IN_PROGRESS,
        started_at=now,
        deadline_at=deadline,
        max_score=sum(q.points for q in questions),
        question_order=json.dumps(question_ids),
        option_order=json.dumps(option_order),
    )
    db.add(attempt)
    db.flush()

    for question in questions:
        db.add(Answer(attempt_id=attempt.id, question_id=question.id))
    db.flush()
    return attempt


def ordered_questions(attempt: Attempt) -> list[Question]:
    try:
        ids = json.loads(attempt.question_order or "[]")
    except (TypeError, ValueError):
        ids = []
    by_id = {q.id: q for q in attempt.quiz.questions}
    ordered = [by_id[qid] for qid in ids if qid in by_id]
    if not ordered:
        ordered = list(attempt.quiz.questions)
    return ordered


def ordered_options(attempt: Attempt, question: Question) -> list[Option]:
    try:
        mapping = json.loads(attempt.option_order or "{}")
    except (TypeError, ValueError):
        mapping = {}
    ids = mapping.get(str(question.id), [])
    by_id = {o.id: o for o in question.options}
    ordered = [by_id[oid] for oid in ids if oid in by_id]
    if not ordered:
        ordered = list(question.options)
    return ordered


def remaining_seconds(attempt: Attempt) -> int | None:
    if not attempt.deadline_at:
        return None
    left = (attempt.deadline_at - utcnow()).total_seconds()
    return max(0, int(left))


def is_expired(attempt: Attempt) -> bool:
    if not attempt.deadline_at:
        return False
    return utcnow() >= attempt.deadline_at


def ensure_answer_rows(db: Session, attempt: Attempt) -> None:
    existing = {a.question_id for a in attempt.answers}
    for question in ordered_questions(attempt):
        if question.id not in existing:
            db.add(Answer(attempt_id=attempt.id, question_id=question.id))
    db.flush()


def save_answer(
    db: Session,
    attempt: Attempt,
    question: Question,
    *,
    selected_option_id: int | None = None,
    text_answer: str | None = None,
) -> Answer:
    answer = attempt.answer_for(question.id)
    if answer is None:
        answer = Answer(attempt_id=attempt.id, question_id=question.id)
        db.add(answer)

    if question.type == QUESTION_MCQ:
        valid_ids = {o.id for o in question.options}
        answer.selected_option_id = selected_option_id if selected_option_id in valid_ids else None
    else:
        answer.text_answer = (text_answer or "")[:20000]

    attempt.last_saved_at = utcnow()
    db.flush()
    return answer


def score_mcq(attempt: Attempt) -> None:
    for answer in attempt.answers:
        question = answer.question
        if question.type != QUESTION_MCQ:
            answer.auto_points = None
            continue
        correct = question.correct_option
        if correct and answer.selected_option_id == correct.id:
            answer.auto_points = question.points
        else:
            answer.auto_points = 0


def submit_attempt(db: Session, attempt: Attempt) -> Attempt:
    if attempt.status != ATTEMPT_IN_PROGRESS:
        return attempt

    ensure_answer_rows(db, attempt)
    score_mcq(attempt)

    questions = ordered_questions(attempt)
    attempt.max_score = sum(q.points for q in questions)
    has_written = any(q.type == QUESTION_WRITTEN for q in questions)
    now = utcnow()
    attempt.submitted_at = now

    if has_written:
        attempt.status = ATTEMPT_SUBMITTED
        attempt.score = sum(a.auto_points or 0 for a in attempt.answers if a.question.type == QUESTION_MCQ)
    else:
        attempt.status = ATTEMPT_GRADED
        attempt.score = sum(a.auto_points or 0 for a in attempt.answers)
        attempt.graded_at = now

    db.flush()
    return attempt


def finalize_attempt(attempt: Attempt, grader: User) -> None:
    missing = []
    for answer in attempt.answers:
        if answer.question.type == QUESTION_WRITTEN and answer.manual_points is None:
            missing.append(answer.question)
    if missing:
        raise ValueError("Every written answer needs a score before the attempt can be finalized.")

    attempt.score = sum(answer.final_points for answer in attempt.answers)
    attempt.status = ATTEMPT_GRADED
    attempt.graded_by = grader.id
    attempt.graded_at = utcnow()


def quiz_stats(quiz: Quiz, attempts: list[Attempt]) -> dict:
    graded = [a for a in attempts if a.status == ATTEMPT_GRADED]
    submitted = [a for a in attempts if a.status != ATTEMPT_IN_PROGRESS]
    percentages = sorted(a.percentage for a in graded)
    average = round(sum(percentages) / len(percentages), 1) if percentages else 0
    median = 0
    if percentages:
        middle = len(percentages) // 2
        if len(percentages) % 2:
            median = percentages[middle]
        else:
            median = round((percentages[middle - 1] + percentages[middle]) / 2, 1)
    passed = sum(1 for p in percentages if p >= quiz.pass_percent)
    pass_rate = round(passed * 100 / len(percentages)) if percentages else 0

    distribution = [
        {"label": "0–20%", "count": sum(1 for p in percentages if p <= 20)},
        {"label": "21–40%", "count": sum(1 for p in percentages if 20 < p <= 40)},
        {"label": "41–60%", "count": sum(1 for p in percentages if 40 < p <= 60)},
        {"label": "61–80%", "count": sum(1 for p in percentages if 60 < p <= 80)},
        {"label": "81–100%", "count": sum(1 for p in percentages if p > 80)},
    ]
    max_bucket = max((bucket["count"] for bucket in distribution), default=0) or 1

    return {
        "graded_count": len(graded),
        "submitted_count": len(submitted),
        "in_progress_count": sum(1 for a in attempts if a.status == ATTEMPT_IN_PROGRESS),
        "average": average,
        "median": median,
        "highest": percentages[-1] if percentages else 0,
        "lowest": percentages[0] if percentages else 0,
        "pass_rate": pass_rate,
        "passed": passed,
        "distribution": distribution,
        "max_bucket": max_bucket,
    }


def question_stats(quiz: Quiz, attempts: list[Attempt]) -> list[dict]:
    stats = []
    for index, question in enumerate(quiz.questions, start=1):
        rows = [a.answer_for(question.id) for a in attempts]
        rows = [r for r in rows if r is not None]
        entry: dict = {
            "number": index,
            "question": question,
            "responses": sum(
                1
                for r in rows
                if r.selected_option_id is not None or (r.text_answer or "").strip()
            ),
        }
        if question.type == QUESTION_MCQ:
            correct = question.correct_option
            answered = [r for r in rows if r.selected_option_id]
            entry["correct"] = sum(1 for r in answered if correct and r.selected_option_id == correct.id)
            entry["accuracy"] = round(entry["correct"] * 100 / len(answered)) if answered else 0
            entry["option_counts"] = [
                {
                    "option": option,
                    "count": sum(1 for r in answered if r.selected_option_id == option.id),
                    "is_correct": option.is_correct,
                }
                for option in question.options
            ]
        else:
            manual = [r.manual_points for r in rows if r.manual_points is not None]
            entry["average_points"] = round(sum(manual) / len(manual), 1) if manual else 0
            entry["graded"] = len(manual)
        stats.append(entry)
    return stats
