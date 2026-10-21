"""Password hashing, CSRF protection and access-code helpers."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

from fastapi import HTTPException, Request

SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 64
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value.encode("ascii"))


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=SCRYPT_DKLEN,
    )
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt_b64, digest_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        digest = hashlib.scrypt(
            password.encode("utf-8"),
            salt=_unb64(salt_b64),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(_unb64(digest_b64)),
        )
        return hmac.compare_digest(digest, _unb64(digest_b64))
    except (ValueError, TypeError):
        return False


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def ensure_csrf_token(request: Request) -> str:
    token = request.session.get("csrf")
    if not token:
        token = new_csrf_token()
        request.session["csrf"] = token
    return token


async def csrf_protect(request: Request) -> None:
    """Global dependency: verify the CSRF token on every mutating request."""
    if request.method in {"GET", "HEAD", "OPTIONS", "TRACE"}:
        return

    expected = request.session.get("csrf")
    if not expected:
        raise HTTPException(status_code=403, detail="Your session expired. Please reload the page and try again.")

    supplied = request.headers.get("x-csrf-token") or ""
    if not supplied:
        content_type = request.headers.get("content-type", "")
        if "form" in content_type:
            form = await request.form()
            value = form.get("_csrf")
            if isinstance(value, str):
                supplied = value

    if not supplied or not hmac.compare_digest(str(supplied), str(expected)):
        raise HTTPException(status_code=403, detail="Invalid security token. Please reload the page and try again.")


def generate_code(length: int = 5) -> str:
    body = "".join(secrets.choice(CODE_ALPHABET) for _ in range(length))
    return f"QZ-{body}"


def normalize_code(value: str) -> str:
    cleaned = "".join(ch for ch in (value or "").upper() if ch.isalnum())
    if cleaned.startswith("QZ"):
        cleaned = cleaned[2:]
    return cleaned


def code_matches(stored: str, entered: str) -> bool:
    return hmac.compare_digest(normalize_code(stored), normalize_code(entered))
