"""Idempotent admin bootstrap and demo content seeding."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import settings
from .models import (
    ATTEMPT_GRADED,
    QUESTION_MCQ,
    QUESTION_WRITTEN,
    QUIZ_PUBLISHED,
    ROLE_ADMIN,
    ROLE_HOST,
    ROLE_PARTICIPANT,
    STATUS_APPROVED,
    Answer,
    Attempt,
    AuditLog,
    Enrollment,
    Question,
    Quiz,
    Option,
    User,
    utcnow,
)
from .security import hash_password
from .services.quiz_service import create_attempt, finalize_attempt, save_answer, submit_attempt

DEMO_PASSWORD = "Learner123!"
DEMO_HOST = ("host@quizsystem.dev", "Ava Bennett", "Host1234!")
DEMO_PARTICIPANTS = [
    ("maya@quizsystem.dev", "Maya Patel"),
    ("leo@quizsystem.dev", "Leo Fischer"),
    ("sam@quizsystem.dev", "Sam Rivera"),
]


def get_or_create_user(
    db: Session,
    *,
    email: str,
    name: str,
    password: str,
    role: str,
    status: str = STATUS_APPROVED,
    approved_by: int | None = None,
) -> User:
    user = db.scalar(select(User).where(User.email == email.lower()))
    if user:
        return user
    user = User(
        email=email.lower(),
        name=name,
        password_hash=hash_password(password),
        role=role,
        status=status,
        approved_by=approved_by,
    )
    db.add(user)
    db.flush()
    return user


def ensure_admin(db: Session) -> User:
    admin = db.scalar(select(User).where(User.email == settings.admin_email.lower()))
    if admin:
        if admin.role != ROLE_ADMIN:
            admin.role = ROLE_ADMIN
            admin.status = STATUS_APPROVED
            db.commit()
        return admin

    admin = User(
        email=settings.admin_email.lower(),
        name=settings.admin_name,
        password_hash=hash_password(settings.admin_password),
        role=ROLE_ADMIN,
        status=STATUS_APPROVED,
    )
    db.add(admin)
    db.commit()
    db.refresh(admin)
    return admin


def _add_mcq(quiz: Quiz, prompt: str, options: list[tuple[str, bool]], points: int, explanation: str = "") -> Question:
    question = Question(
        quiz=quiz,
        type=QUESTION_MCQ,
        prompt=prompt,
        points=points,
        position=len(quiz.questions),
        explanation=explanation,
    )
    for index, (text, is_correct) in enumerate(options):
        question.options.append(Option(text=text, is_correct=is_correct, position=index))
    return question


def _add_written(quiz: Quiz, prompt: str, model_answer: str, points: int) -> Question:
    return Question(
        quiz=quiz,
        type=QUESTION_WRITTEN,
        prompt=prompt,
        points=points,
        position=len(quiz.questions),
        model_answer=model_answer,
    )


def seed_demo(db: Session, admin: User) -> None:
    if not settings.seed_demo:
        return

    has_quiz = db.scalar(select(func.count(Quiz.id)))
    if has_quiz:
        return

    host = get_or_create_user(
        db,
        email=DEMO_HOST[0],
        name=DEMO_HOST[1],
        password=DEMO_HOST[2],
        role=ROLE_HOST,
        approved_by=admin.id,
    )

    participants = []
    for email, name in DEMO_PARTICIPANTS:
        participants.append(
            get_or_create_user(
                db,
                email=email,
                name=name,
                password=DEMO_PASSWORD,
                role=ROLE_PARTICIPANT,
                approved_by=host.id,
            )
        )

    now = utcnow()

    history = Quiz(
        host_id=host.id,
        title="World History Foundations",
        description="A short mixed-format check on empires, documents, and trade routes.",
        instructions=(
            "Answer every question. Multiple-choice questions are scored automatically. "
            "Written answers are read and scored by your host after the quiz closes."
        ),
        access_code="QZ-HIST1",
        status=QUIZ_PUBLISHED,
        time_limit_minutes=20,
        max_attempts=2,
        pass_percent=60,
        shuffle_questions=False,
        shuffle_options=True,
        results_released=True,
        created_at=now - timedelta(days=9),
        updated_at=now - timedelta(days=2),
    )
    _add_mcq(
        history,
        "Which civilization built the Colosseum in Rome?",
        [
            ("Roman Empire", True),
            ("Ancient Greece", False),
            ("Ptolemaic Egypt", False),
            ("Achaemenid Persia", False),
        ],
        2,
        "The Colosseum was completed in 80 CE under the Flavian dynasty of the Roman Empire.",
    )
    _add_mcq(
        history,
        "In which year was the Magna Carta sealed at Runnymede?",
        [("1215", True), ("1066", False), ("1492", False), ("1776", False)],
        2,
        "King John sealed the Magna Carta in 1215.",
    )
    _add_written(
        history,
        "Explain one long-term effect of the Silk Road on Eurasian societies.",
        "Trade moved goods, technologies, religions and ideas between China, Central Asia, "
        "the Middle East and Europe. Cities along the route grew wealthy, and cultural "
        "exchange spread Buddhism, papermaking and astronomy across regions.",
        4,
    )
    _add_written(
        history,
        "Describe a key difference between Athenian and Spartan governance.",
        "Athens developed a participatory democracy where eligible male citizens voted in "
        "an assembly, while Sparta was ruled by a dual monarchy supported by a council of "
        "elders and a militarised citizen class.",
        4,
    )
    db.add(history)

    python = Quiz(
        host_id=host.id,
        title="Python Basics Checkpoint",
        description="Four quick questions on core Python syntax and built-in types.",
        instructions="No notes needed. You have ten minutes and one attempt.",
        access_code="QZ-PY231",
        status=QUIZ_PUBLISHED,
        time_limit_minutes=10,
        max_attempts=1,
        pass_percent=50,
        shuffle_questions=True,
        shuffle_options=True,
        results_released=False,
        created_at=now - timedelta(days=4),
        updated_at=now - timedelta(days=1),
    )
    _add_mcq(python, "What does len([1, 2, 3]) return?", [("3", True), ("2", False), ("4", False), ("An error", False)], 1)
    _add_mcq(python, "Which of these types is mutable?", [("list", True), ("tuple", False), ("str", False), ("frozenset", False)], 1)
    _add_mcq(python, "Which keyword defines a function?", [("def", True), ("func", False), ("lambda only", False), ("function", False)], 1)
    _add_mcq(python, "What is the value of 7 // 2?", [("3", True), ("3.5", False), ("4", False), ("1", False)], 1)
    db.add(python)

    db.flush()

    # Everyone in the demo cohort is enrolled in the history quiz.
    for participant in participants:
        db.add(Enrollment(quiz_id=history.id, user_id=participant.id))

    maya, leo, sam = participants
    db.add(Enrollment(quiz_id=python.id, user_id=maya.id))
    db.add(Enrollment(quiz_id=python.id, user_id=sam.id))

    # Maya: graded history attempt with written feedback.
    maya_attempt = create_attempt(db, history, maya)
    maya_attempt.started_at = now - timedelta(days=2, minutes=19)
    maya_attempt.deadline_at = now - timedelta(days=2, minutes=-1)
    _answer(db, maya_attempt, history.questions[0], history.questions[0].options[0].id)
    _answer(db, maya_attempt, history.questions[1], history.questions[1].options[1].id)
    _answer(
        db,
        maya_attempt,
        history.questions[2],
        text=(
            "The Silk Road spread technologies like papermaking and printing, and it also "
            "carried religions such as Buddhism across Asia, changing how societies traded and believed."
        ),
    )
    _answer(
        db,
        maya_attempt,
        history.questions[3],
        text=(
            "Athens let citizens debate and vote in an assembly, while Sparta concentrated power "
            "in two kings and a council of elders and organised society around its army."
        ),
    )
    submit_attempt(db, maya_attempt)
    _grade_written(db, maya_attempt, host, {2: (3, "Good, you connected technology and religion."), 3: (3, "Clear contrast between the two systems.")})
    finalize_attempt(maya_attempt, host)
    maya_attempt.submitted_at = now - timedelta(days=2, minutes=6)
    maya_attempt.graded_at = now - timedelta(days=1, hours=3)

    # Leo: submitted and still waiting for the host to grade the written answers.
    leo_attempt = create_attempt(db, history, leo)
    leo_attempt.started_at = now - timedelta(hours=20)
    leo_attempt.deadline_at = now - timedelta(hours=20) + timedelta(minutes=20)
    _answer(db, leo_attempt, history.questions[0], history.questions[0].options[0].id)
    _answer(db, leo_attempt, history.questions[1], history.questions[1].options[0].id)
    _answer(
        db,
        leo_attempt,
        history.questions[2],
        text="Trade routes moved ideas as well as goods, so religions and inventions spread between regions.",
    )
    _answer(
        db,
        leo_attempt,
        history.questions[3],
        text="Sparta was a military society ruled by kings, while Athens had a democratic assembly.",
    )
    submit_attempt(db, leo_attempt)
    leo_attempt.submitted_at = now - timedelta(hours=19, minutes=52)

    db.add(
        AuditLog(
            actor_id=admin.id,
            action="demo.seed",
            detail="Seeded demo host, participants and two quizzes.",
            created_at=now,
        )
    )
    db.add(
        AuditLog(
            actor_id=host.id,
            action="quiz.results_released",
            detail="Results released for World History Foundations.",
            created_at=now - timedelta(days=1),
        )
    )
    db.commit()


def _answer(db: Session, attempt: Attempt, question: Question, option_id: int | None = None, text: str | None = None) -> None:
    save_answer(db, attempt, question, selected_option_id=option_id, text_answer=text)


def _grade_written(db: Session, attempt: Attempt, grader: User, scores: dict[int, tuple[int, str]]) -> None:
    for index, (points, feedback) in scores.items():
        question = ordered_question(attempt, index)
        answer = attempt.answer_for(question.id)
        if answer:
            answer.manual_points = points
            answer.feedback = feedback
            answer.graded_by = grader.id
            answer.graded_at = utcnow()
    db.flush()


def ordered_question(attempt: Attempt, index: int) -> Question:
    from .services.quiz_service import ordered_questions

    return ordered_questions(attempt)[index]
