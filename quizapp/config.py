"""Application configuration loaded from environment variables."""

from __future__ import annotations

import os
import secrets
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Load a local .env file when present. On Vercel, variables come from the
# project environment and no .env file exists.
load_dotenv(PROJECT_ROOT / ".env", override=False)


def _bool(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


class Settings:
    app_name: str = os.getenv("APP_NAME", "Quiz System")
    tagline: str = os.getenv("APP_TAGLINE", "Host it. Take it. Grade it.")

    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./dev.db")
    direct_url: str = os.getenv("DIRECT_URL") or os.getenv("DATABASE_URL", "sqlite:///./dev.db")

    secret_key: str = os.getenv("SECRET_KEY", "")
    cookie_secure: bool = _bool("COOKIE_SECURE", "0")
    session_max_age: int = int(os.getenv("SESSION_MAX_AGE", str(14 * 24 * 3600)))

    admin_email: str = os.getenv("ADMIN_EMAIL", "admin@quizsystem.local").strip().lower()
    admin_password: str = os.getenv("ADMIN_PASSWORD", "Admin1234!")
    admin_name: str = os.getenv("ADMIN_NAME", "Platform Admin")

    seed_demo: bool = _bool("SEED_DEMO", "1")

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def is_production(self) -> bool:
        return self.cookie_secure


settings = Settings()

if not settings.secret_key:
    # Stable per-process fallback for local development only. Production must
    # set SECRET_KEY explicitly, otherwise sessions reset on every restart.
    settings.secret_key = "dev-only-insecure-secret-key"
