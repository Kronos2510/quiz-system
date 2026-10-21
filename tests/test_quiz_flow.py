"""End-to-end quiz lifecycle tests."""

from __future__ import annotations

from sqlalchemy import select

from quizapp.models import (
    ATTEMPT_GRADED,
    ATTEMPT_SUBMITTED,
    QUESTION_MCQ,
    QUIZ_PUBLISHED,
    Attempt,
    Quiz,
    User,
)
from quizapp.security import hash_password

from .helpers import (
    approve_user,
    csrf_from,
    login_ok,
    post_form,
    register,
    token_for,
    unique_email,
)


def _create_quiz(host_client, title: str) -> int:
    response = post_form(
        host_client,
        "/host/quizzes/new",
        [
            ("title", title),
            ("description", "A test quiz covering the full lifecycle."),
            ("instructions", "Answer everything, then submit."),
            ("time_limit_minutes", "10"),
            ("max_attempts", "1"),
            ("pass_percent", "50"),
            ("shuffle_options", "on"),
            ("shuffle_questions", "on"),
            ("opens_at", ""),
            ("closes_at", ""),
        ],
    )
    assert response.status_code == 303, response.text[:500]
    location = response.headers["location"]
    assert location.startswith("/host/quizzes/")
    return int(location.split("/")[3])


def _add_mcq(host_client, quiz_id: int, prompt: str = "Which answer is correct?", points: int = 2) -> None:
    response = post_form(
        host_client,
        f"/host/quizzes/{quiz_id}/questions/new",
        [
            ("type", QUESTION_MCQ),
            ("prompt", prompt),
            ("points", str(points)),
            ("explanation", "Because the first option is correct."),
            ("model_answer", ""),
            ("option_text", "Correct option"),
            ("option_text", "Wrong option"),
            ("option_text", "Another wrong option"),
            ("option_text", "One more wrong option"),
            ("correct_option", "0"),
        ],
    )
    assert response.status_code == 303, response.text[:500]


def _add_written(host_client, quiz_id: int, prompt: str = "Explain your reasoning.", points: int = 4) -> None:
    response = post_form(
        host_client,
        f"/host/quizzes/{quiz_id}/questions/new",
        [
            ("type", "written"),
            ("prompt", prompt),
            ("points", str(points)),
            ("model_answer", "A strong answer mentions the key idea."),
            ("explanation", ""),
        ],
    )
    assert response.status_code == 303, response.text[:500]


def _make_host(client, admin_client, db, admin_email, admin_password) -> User:
    email = unique_email("host")
    password = "HostPass123!"
    response = register(client, name="Quiz Host", email=email, password=password, role="host")
    assert response.status_code == 200
    host = db.scalar(select(User).where(User.email == email))
    login_ok(admin_client, admin_email, admin_password, "/admin")
    approve_user(admin_client, host.id)
    login_ok(client, email, password, "/host")
    return host


def _make_participant(client, host_client, db) -> tuple[User, str, str]:
    email = unique_email("learner")
    password = "Learner123!"
    response = register(client, name="Quiz Learner", email=email, password=password, role="participant")
    assert response.status_code == 200
    participant = db.scalar(select(User).where(User.email == email))

    token = token_for(host_client, "/host/participants")
    approved = host_client.post(
        f"/host/participants/{participant.id}/approve",
        data={"_csrf": token},
    )
    assert approved.status_code == 303
    login_ok(client, email, password, "/dashboard")
    return participant, email, password


def test_full_quiz_lifecycle(client, second_client, db, admin_email, admin_password):
    host = _make_host(client, second_client, db, admin_email, admin_password)

    quiz_id = _create_quiz(client, "Lifecycle Quiz")
    _add_mcq(client, quiz_id)
    _add_written(client, quiz_id)

    token = token_for(client, f"/host/quizzes/{quiz_id}/edit")
    published = client.post(f"/host/quizzes/{quiz_id}/publish", data={"_csrf": token})
    assert published.status_code == 303

    db.expire_all()
    quiz = db.get(Quiz, quiz_id)
    assert quiz is not None
    assert quiz.status == QUIZ_PUBLISHED
    assert quiz.access_code.startswith("QZ-")
    assert len(quiz.questions) == 2
    mcq = next(q for q in quiz.questions if q.type == QUESTION_MCQ)
    written = next(q for q in quiz.questions if q.type == "written")
    correct = mcq.correct_option

    # The admin client is done; reuse its cookie jar for the participant.
    token = token_for(second_client, "/admin")
    second_client.post("/logout", data={"_csrf": token})

    participant, email, password = _make_participant(second_client, client, db)

    # A participant cannot open a quiz without joining.
    blocked = second_client.get(f"/quiz/{quiz_id}")
    assert blocked.status_code == 303
    assert blocked.headers["location"] == "/dashboard"

    # Wrong access code is a friendly 404.
    bad_code = second_client.post(
        "/join",
        data={"_csrf": token_for(second_client, "/join"), "code": "QZ-NOPE1"},
    )
    assert bad_code.status_code == 404
    assert "match any quiz" in bad_code.text

    joined = second_client.post(
        "/join",
        data={"_csrf": token_for(second_client, "/join"), "code": quiz.access_code},
    )
    assert joined.status_code == 303
    assert joined.headers["location"] == f"/quiz/{quiz_id}"

    intro = second_client.get(f"/quiz/{quiz_id}")
    assert intro.status_code == 200
    assert "Lifecycle Quiz" in intro.text
    token = csrf_from(intro.text)

    started = second_client.post(f"/quiz/{quiz_id}/start", data={"_csrf": token})
    assert started.status_code == 303
    attempt_path = started.headers["location"]
    assert attempt_path.startswith("/attempt/")
    attempt_id = int(attempt_path.split("/")[2])

    attempt_page = second_client.get(attempt_path)
    assert attempt_page.status_code == 200
    assert "Time left" in attempt_page.text
    token = csrf_from(attempt_page.text)

    # Autosave via the JSON API.
    saved = second_client.post(
        f"/api/attempts/{attempt_id}/answer",
        json={"question_id": mcq.id, "option_id": correct.id},
        headers={"X-CSRF-Token": token},
    )
    assert saved.status_code == 200
    assert saved.json()["ok"] is True

    # The final submitted form is authoritative.
    submitted = post_form(
        second_client,
        f"/attempt/{attempt_id}/submit",
        [
            (f"q_{mcq.id}", str(correct.id)),
            (f"q_{written.id}", "The key idea is explained with a concrete example."),
        ],
        token_path=attempt_path,
    )
    assert submitted.status_code == 303

    db.expire_all()
    attempt = db.get(Attempt, attempt_id)
    assert attempt.status == ATTEMPT_SUBMITTED
    assert attempt.score == 2
    assert attempt.max_score == 6

    awaiting = second_client.get(f"/attempt/{attempt_id}/result")
    assert awaiting.status_code == 200
    assert "grading" in awaiting.text.lower()

    # Host grades the written answer and finalizes.
    grade_page = client.get(f"/host/attempts/{attempt_id}/grade")
    assert grade_page.status_code == 200
    assert "Model answer" in grade_page.text
    token = csrf_from(grade_page.text)

    graded = post_form(
        client,
        f"/host/attempts/{attempt_id}/grade",
        [
            (f"points_{written.id}", "3"),
            (f"feedback_{written.id}", "Clear and specific."),
            ("action", "finalize"),
        ],
    )
    assert graded.status_code == 303

    db.expire_all()
    attempt = db.get(Attempt, attempt_id)
    assert attempt.status == ATTEMPT_GRADED
    assert attempt.score == 5
    assert attempt.graded_by == host.id

    # Results are sealed until the host releases them.
    hidden = second_client.get(f"/attempt/{attempt_id}/result")
    assert hidden.status_code == 200
    assert "sealed" in hidden.text.lower()

    results_page = client.get(f"/host/quizzes/{quiz_id}/results")
    assert results_page.status_code == 200
    assert "Release results" in results_page.text
    token = csrf_from(results_page.text)

    released = client.post(
        f"/host/quizzes/{quiz_id}/release",
        data={"_csrf": token, "release": "1"},
    )
    assert released.status_code == 303

    visible = second_client.get(f"/attempt/{attempt_id}/result")
    assert visible.status_code == 200
    assert "Passed" in visible.text
    assert "Clear and specific." in visible.text
    assert "83%" in visible.text

    # CSV export includes the participant and question columns.
    export = client.get(f"/host/quizzes/{quiz_id}/export.csv")
    assert export.status_code == 200
    assert "text/csv" in export.headers["content-type"]
    assert "Quiz Learner" in export.text
    assert "Q1:" in export.text


def test_questions_lock_after_first_attempt(client, second_client, db, admin_email, admin_password):
    _make_host(client, second_client, db, admin_email, admin_password)
    quiz_id = _create_quiz(client, "Locked Quiz")
    _add_mcq(client, quiz_id)

    token = token_for(client, f"/host/quizzes/{quiz_id}/edit")
    client.post(f"/host/quizzes/{quiz_id}/publish", data={"_csrf": token})

    # Reuse the admin jar for the participant.
    token = token_for(second_client, "/admin")
    second_client.post("/logout", data={"_csrf": token})
    _make_participant(second_client, client, db)

    db.expire_all()
    quiz = db.get(Quiz, quiz_id)
    second_client.post(
        "/join",
        data={"_csrf": token_for(second_client, "/join"), "code": quiz.access_code},
    )
    token = token_for(second_client, f"/quiz/{quiz_id}")
    started = second_client.post(f"/quiz/{quiz_id}/start", data={"_csrf": token})
    assert started.status_code == 303

    locked = client.get(f"/host/quizzes/{quiz_id}/questions/new?type=mcq")
    assert locked.status_code == 303
    assert locked.headers["location"] == f"/host/quizzes/{quiz_id}/edit"

    edit_page = client.get(f"/host/quizzes/{quiz_id}/edit")
    assert edit_page.status_code == 200
    assert "Questions are frozen" in edit_page.text
