"""Authentication and approval flow tests."""

from __future__ import annotations

from sqlalchemy import select

from quizapp.models import STATUS_APPROVED, STATUS_PENDING, User

from .helpers import approve_user, login, login_ok, register, unique_email


def test_registration_is_pending_and_login_is_blocked(client, second_client, db, admin_email, admin_password):
    email = unique_email("pending")
    password = "Learner123!"

    response = register(client, name="Pending Person", email=email, password=password, role="participant")
    assert response.status_code == 200
    assert "You're in the queue" in response.text

    user = db.scalar(select(User).where(User.email == email))
    assert user is not None
    assert user.status == STATUS_PENDING
    assert user.role == "participant"

    blocked = login(client, email, password)
    assert blocked.status_code == 400
    assert "waiting for approval" in blocked.text.lower()

    login_ok(second_client, admin_email, admin_password, "/admin")
    approve_user(second_client, user.id)

    db.expire_all()
    approved = db.get(User, user.id)
    assert approved.status == STATUS_APPROVED

    login_ok(client, email, password, "/dashboard")
    dashboard = client.get("/dashboard")
    assert dashboard.status_code == 200
    assert "Participant dashboard" in dashboard.text


def test_wrong_password_and_unknown_email_are_rejected(client, db, admin_email):
    email = unique_email("wrongpass")
    password = "Learner123!"
    register(client, name="Wrong Pass", email=email, password=password, role="participant")

    response = login(client, email, "not-the-password")
    assert response.status_code == 400
    assert "combination doesn" in response.text

    response = login(client, "nobody@test.local", password)
    assert response.status_code == 400


def test_host_registration_needs_admin_approval(client, second_client, db, admin_email, admin_password):
    email = unique_email("host")
    password = "HostPass123!"

    response = register(client, name="Host Applicant", email=email, password=password, role="host")
    assert response.status_code == 200
    assert "platform admin" in response.text.lower()

    user = db.scalar(select(User).where(User.email == email))
    assert user.role == "host"
    assert user.status == STATUS_PENDING

    blocked = login(client, email, password)
    assert blocked.status_code == 400

    login_ok(second_client, admin_email, admin_password, "/admin")
    approve_user(second_client, user.id)

    login_ok(client, email, password, "/host")
    dashboard = client.get("/host")
    assert dashboard.status_code == 200
    assert "Host dashboard" in dashboard.text


def test_participant_cannot_open_host_area(client, db):
    email = unique_email("nothost")
    password = "Learner123!"
    register(client, name="Curious Participant", email=email, password=password, role="participant")

    user = db.scalar(select(User).where(User.email == email))
    from quizapp.models import utcnow

    user.status = STATUS_APPROVED
    user.approved_by = None
    user.last_login_at = utcnow()
    db.commit()

    login_ok(client, email, password, "/dashboard")
    forbidden = client.get("/host")
    assert forbidden.status_code == 403
    assert "Not your area" in forbidden.text
