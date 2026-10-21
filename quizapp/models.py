"""SQLAlchemy models for the quiz platform."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base

ROLE_ADMIN = "admin"
ROLE_HOST = "host"
ROLE_PARTICIPANT = "participant"
ROLES = (ROLE_ADMIN, ROLE_HOST, ROLE_PARTICIPANT)

STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUS_DISABLED = "disabled"
USER_STATUSES = (STATUS_PENDING, STATUS_APPROVED, STATUS_REJECTED, STATUS_DISABLED)

QUIZ_DRAFT = "draft"
QUIZ_PUBLISHED = "published"
QUIZ_CLOSED = "closed"
QUIZ_STATUSES = (QUIZ_DRAFT, QUIZ_PUBLISHED, QUIZ_CLOSED)

QUESTION_MCQ = "mcq"
QUESTION_WRITTEN = "written"

ATTEMPT_IN_PROGRESS = "in_progress"
ATTEMPT_SUBMITTED = "submitted"
ATTEMPT_GRADED = "graded"


def utcnow() -> datetime:
    """Naive UTC timestamp, portable between SQLite and Postgres."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default=ROLE_PARTICIPANT, index=True)
    status: Mapped[str] = mapped_column(String(20), default=STATUS_PENDING, index=True)
    approved_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN

    @property
    def is_host(self) -> bool:
        return self.role == ROLE_HOST

    @property
    def is_participant(self) -> bool:
        return self.role == ROLE_PARTICIPANT


class Quiz(Base):
    __tablename__ = "quizzes"

    id: Mapped[int] = mapped_column(primary_key=True)
    host_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    instructions: Mapped[str] = mapped_column(Text, default="")
    access_code: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(20), default=QUIZ_DRAFT, index=True)

    time_limit_minutes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    max_attempts: Mapped[int] = mapped_column(Integer, default=1)
    pass_percent: Mapped[int] = mapped_column(Integer, default=50)
    shuffle_questions: Mapped[bool] = mapped_column(Boolean, default=False)
    shuffle_options: Mapped[bool] = mapped_column(Boolean, default=True)

    opens_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    closes_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    results_released: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    host: Mapped["User"] = relationship("User", foreign_keys=[host_id])
    questions: Mapped[List["Question"]] = relationship(
        "Question",
        back_populates="quiz",
        cascade="all, delete-orphan",
        order_by="Question.position",
    )
    enrollments: Mapped[List["Enrollment"]] = relationship(
        "Enrollment", back_populates="quiz", cascade="all, delete-orphan"
    )
    attempts: Mapped[List["Attempt"]] = relationship(
        "Attempt", back_populates="quiz", cascade="all, delete-orphan"
    )

    @property
    def total_points(self) -> int:
        return sum(q.points for q in self.questions)

    @property
    def is_open(self) -> bool:
        if self.status != QUIZ_PUBLISHED:
            return False
        now = utcnow()
        if self.opens_at and now < self.opens_at:
            return False
        if self.closes_at and now > self.closes_at:
            return False
        return True


class Question(Base):
    __tablename__ = "questions"

    id: Mapped[int] = mapped_column(primary_key=True)
    quiz_id: Mapped[int] = mapped_column(ForeignKey("quizzes.id", ondelete="CASCADE"), index=True)
    type: Mapped[str] = mapped_column(String(20), default=QUESTION_MCQ)
    prompt: Mapped[str] = mapped_column(Text)
    points: Mapped[int] = mapped_column(Integer, default=1)
    position: Mapped[int] = mapped_column(Integer, default=0)
    model_answer: Mapped[str] = mapped_column(Text, default="")
    explanation: Mapped[str] = mapped_column(Text, default="")

    quiz: Mapped["Quiz"] = relationship("Quiz", back_populates="questions")
    options: Mapped[List["Option"]] = relationship(
        "Option",
        back_populates="question",
        cascade="all, delete-orphan",
        order_by="Option.position",
    )

    @property
    def correct_option(self) -> Optional["Option"]:
        for option in self.options:
            if option.is_correct:
                return option
        return None

    @property
    def type_label(self) -> str:
        return "Multiple choice" if self.type == QUESTION_MCQ else "Written answer"


class Option(Base):
    __tablename__ = "options"

    id: Mapped[int] = mapped_column(primary_key=True)
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id", ondelete="CASCADE"), index=True)
    text: Mapped[str] = mapped_column(Text)
    is_correct: Mapped[bool] = mapped_column(Boolean, default=False)
    position: Mapped[int] = mapped_column(Integer, default=0)

    question: Mapped["Question"] = relationship("Question", back_populates="options")


class Enrollment(Base):
    __tablename__ = "enrollments"
    __table_args__ = (UniqueConstraint("quiz_id", "user_id", name="uq_enrollment_quiz_user"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    quiz_id: Mapped[int] = mapped_column(ForeignKey("quizzes.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    quiz: Mapped["Quiz"] = relationship("Quiz", back_populates="enrollments")
    user: Mapped["User"] = relationship("User")


class Attempt(Base):
    __tablename__ = "attempts"

    id: Mapped[int] = mapped_column(primary_key=True)
    quiz_id: Mapped[int] = mapped_column(ForeignKey("quizzes.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(20), default=ATTEMPT_IN_PROGRESS, index=True)

    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    deadline_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    submitted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_saved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    score: Mapped[int] = mapped_column(Integer, default=0)
    max_score: Mapped[int] = mapped_column(Integer, default=0)
    graded_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    graded_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Snapshots keep question/option order stable for the entire attempt.
    question_order: Mapped[str] = mapped_column(Text, default="[]")
    option_order: Mapped[str] = mapped_column(Text, default="{}")

    quiz: Mapped["Quiz"] = relationship("Quiz", back_populates="attempts")
    user: Mapped["User"] = relationship("User", foreign_keys=[user_id])
    answers: Mapped[List["Answer"]] = relationship(
        "Answer", back_populates="attempt", cascade="all, delete-orphan"
    )

    @property
    def percentage(self) -> int:
        if not self.max_score:
            return 0
        return round(self.score * 100 / self.max_score)

    @property
    def status_label(self) -> str:
        return {
            ATTEMPT_IN_PROGRESS: "In progress",
            ATTEMPT_SUBMITTED: "Awaiting grading",
            ATTEMPT_GRADED: "Graded",
        }.get(self.status, self.status)

    def answer_for(self, question_id: int) -> Optional["Answer"]:
        for answer in self.answers:
            if answer.question_id == question_id:
                return answer
        return None


class Answer(Base):
    __tablename__ = "answers"
    __table_args__ = (UniqueConstraint("attempt_id", "question_id", name="uq_answer_attempt_question"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    attempt_id: Mapped[int] = mapped_column(ForeignKey("attempts.id", ondelete="CASCADE"), index=True)
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id", ondelete="CASCADE"), index=True)

    selected_option_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("options.id", ondelete="SET NULL"), nullable=True
    )
    text_answer: Mapped[str] = mapped_column(Text, default="")

    auto_points: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    manual_points: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    feedback: Mapped[str] = mapped_column(Text, default="")
    graded_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    graded_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    attempt: Mapped["Attempt"] = relationship("Attempt", back_populates="answers")
    question: Mapped["Question"] = relationship("Question")
    selected_option: Mapped[Optional["Option"]] = relationship("Option")

    @property
    def final_points(self) -> int:
        if self.question.type == QUESTION_WRITTEN:
            return self.manual_points if self.manual_points is not None else 0
        return self.auto_points if self.auto_points is not None else 0

    @property
    def is_correct(self) -> Optional[bool]:
        if self.question.type != QUESTION_MCQ:
            return None
        correct = self.question.correct_option
        if not correct or not self.selected_option_id:
            return False
        return self.selected_option_id == correct.id


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    actor_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(80))
    detail: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
