# pylint: disable=W0621,C0114,C0116
"""Requesting a verification code rotates any code the user already has.

A second request invalidates the link mailed by the first one and starts
a fresh validity window.
"""
import re
from datetime import timedelta
from typing import Any

import pytest
from django.core import mail
from django.test import Client
from django.utils import timezone
from pytest_django.fixtures import SettingsWrapper

from web_annotation.models import (
    AccountConfirmationCode,
    BaseVerificationCode,
    ResetPasswordCode,
    User,
)


@pytest.fixture
def user() -> User:
    return User.objects.get(email="user@example.com")


@pytest.mark.django_db
@pytest.mark.parametrize(
    "code_model", [ResetPasswordCode, AccountConfirmationCode])
def test_create_rotates_an_existing_code(
    user: User,
    code_model: type[BaseVerificationCode],
) -> None:
    first = code_model.create(user)

    second = code_model.create(user)

    assert str(second.path) != str(first.path)
    assert not code_model.objects.filter(  # type: ignore[attr-defined]
        path=first.path).exists()
    assert list(code_model.objects.filter(  # type: ignore[attr-defined]
        user=user)) == [second]


@pytest.mark.django_db
def test_rotation_restarts_the_reset_code_validity_window(
    user: User,
    settings: SettingsWrapper,
) -> None:
    stale = ResetPasswordCode.create(user)
    ResetPasswordCode.objects.filter(pk=stale.pk).update(
        created_at=timezone.now() - timedelta(
            hours=settings.RESET_PASSWORD_TIMEOUT_HOURS + 1))

    fresh = ResetPasswordCode.create(user)

    assert fresh.validate() is True


def _request_reset_code(client: Client, email: str) -> str:
    mail.outbox.clear()
    response = client.post("/api/forgotten_password", {"email": email})
    assert response.status_code == 200
    code_search = re.search(
        r"code=(.*)\n", mail.outbox[-1].message().get_payload())
    assert code_search is not None
    return code_search.group(1)


def _submit_reset(client: Client, code: str) -> Any:
    return client.post(
        "/api/reset_password",
        data={
            "code": code,
            "new_password1": "newsecret",
            "new_password2": "newsecret",
        },
    )


@pytest.mark.django_db
def test_second_reset_request_invalidates_the_first_mailed_code(
    client: Client,
    user: User,
) -> None:
    first_code = _request_reset_code(client, user.email)
    second_code = _request_reset_code(client, user.email)

    first_response = _submit_reset(client, first_code)
    second_response = _submit_reset(client, second_code)

    assert first_response.status_code == 400
    assert "Invalid reset code" in first_response.content.decode("utf-8")
    assert second_response.status_code == 302
