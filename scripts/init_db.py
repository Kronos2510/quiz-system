"""Create tables and seed the admin account (and optional demo content).

Usage (local):
    python scripts/init_db.py

For Supabase, run this once with DIRECT_URL set to the direct/session
connection string so DDL and seed queries do not travel through the pooler.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from quizapp import models  # noqa: E402,F401
from quizapp.config import settings  # noqa: E402
from quizapp.db import Base, build_engine  # noqa: E402
from quizapp.seed import ensure_admin, seed_demo  # noqa: E402


def mask(url: str) -> str:
    try:
        parts = urlsplit(url)
        if parts.password:
            netloc = parts.netloc.replace(parts.password, "********")
            return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
    except ValueError:
        pass
    return re.sub(r"//[^@]+@", "//********@", url)


def main() -> None:
    url = settings.direct_url
    print(f"Connecting to: {mask(url)}")
    engine = build_engine(url)

    print("Creating tables…")
    Base.metadata.create_all(bind=engine)

    with Session(engine) as db:
        admin = ensure_admin(db)
        print(f"Admin ready: {admin.email}")
        seed_demo(db, admin)

        from sqlalchemy import func, select

        quiz_count = db.scalar(select(func.count(models.Quiz.id))) or 0
        user_count = db.scalar(select(func.count(models.User.id))) or 0
        print(f"Done. {user_count} user(s), {quiz_count} quiz(zes).")


if __name__ == "__main__":
    main()
