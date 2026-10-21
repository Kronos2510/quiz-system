"""Shared HTTP helpers for the test suite."""

from __future__ import annotations

import re
import uuid
from urllib.parse import urlencode

CSRF_RE = re.compile(r'name="_csrf" value="([^"]+)"')


def csrf_from(html: str) -> str:
    match = CSRF_RE.search(html)
    assert match, "No CSRF token found in page"
    return match.group(1)


def token_for(client, path: str) -> str:
    response = client.get(path)
    assert response.status_code == 200, (path, response.status_code, response.text[:400])
    return csrf_from(response.text)


def unique_email(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}@test.local"


def register(client, *, name: str, email: str, password: str, role: str):
    token = token_for(client, "/register")
    return client.post(
        "/register",
        data={
            "_csrf": token,
            "name": name,
            "email": email,
            "password": password,
            "confirm": password,
            "role": role,
        },
    )


def login(client, email: str, password: str):
    token = token_for(client, "/login")
    return client.post(
        "/login",
        data={"_csrf": token, "email": email, "password": password, "next": ""},
    )


def login_ok(client, email: str, password: str, destination: str):
    response = login(client, email, password)
    assert response.status_code == 303, response.text[:500]
    assert response.headers["location"] == destination


def approve_user(client, user_id: int, path: str = "/admin") -> None:
    token = token_for(client, path)
    response = client.post(
        f"/admin/users/{user_id}/approve",
        data={"_csrf": token},
    )
    assert response.status_code == 303, response.text[:500]


def post_form(client, path: str, data: list[tuple[str, str]] | dict, *, token_path: str | None = None):
    """POST urlencoded fields, preserving repeated keys (e.g. option_text)."""
    token = token_for(client, token_path or path)
    pairs = list(data.items()) if isinstance(data, dict) else list(data)
    pairs.insert(0, ("_csrf", token))
    return client.post(
        path,
        content=urlencode(pairs).encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
