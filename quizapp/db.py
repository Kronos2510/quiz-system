"""Database engine, session factory and schema creation helpers."""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


def normalize_database_url(url: str) -> str:
    """Convert common Postgres URL schemes to the psycopg 3 SQLAlchemy driver."""
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://") :]
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://") :]
    return url


def build_engine(url: str, *, pooled: bool = False) -> Engine:
    """Create an engine suited to either SQLite or serverless Postgres.

    Vercel Functions are suspended between requests, so Supabase's transaction
    pooler must not inherit stale client connections. ``NullPool`` keeps the
    client side stateless, and ``prepare_threshold=None`` disables server-side
    prepared statements, which PgBouncer-style poolers do not support.
    """
    url = normalize_database_url(url)

    kwargs: dict = {"future": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    else:
        from sqlalchemy.pool import NullPool

        kwargs["poolclass"] = NullPool
        kwargs["connect_args"] = {"prepare_threshold": None}

    engine = create_engine(url, **kwargs)

    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_connection, _record):  # pragma: no cover - driver hook
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


engine = build_engine(settings.database_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def create_schema(bind: Engine | None = None) -> None:
    from . import models  # noqa: F401  (register mappings)

    Base.metadata.create_all(bind=bind or engine)
