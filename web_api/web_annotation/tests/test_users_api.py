# pylint: disable=W0621,C0114,C0116,W0212,W0613
import re
from collections.abc import Iterator
from importlib.metadata import version
from smtplib import SMTPException
from unittest import mock

import pytest
from django.core import mail
from django.test import Client

from web_annotation.models import User


def test_get_users(admin_client: Client) -> None:
    response = admin_client.get("/api/users")
    assert response.status_code == 200
    assert response.json() == [
        {"email": "user@example.com", "jobs": [1]},
        {"email": "admin@example.com", "jobs": [2]},
    ]


def test_get_users_unauthorized(user_client: Client) -> None:
    response = user_client.get("/api/users")
    assert response.status_code == 403


def test_get_user_details(admin_client: Client) -> None:
    response = admin_client.get("/api/users/1")
    assert response.status_code == 200
    assert response.json() == {"email": "user@example.com", "jobs": [1]}


def test_get_user_details_unauthorized(user_client: Client) -> None:
    response = user_client.get("/api/users/1")
    assert response.status_code == 403


def test_register(client: Client) -> None:
    response = client.post(
        "/api/register",
        {
            "email": "gosho@example.com",
            "password": "secret",
        },
        content_type="application/json",
    )
    assert response.status_code == 200
    assert User.objects.filter(email="gosho@example.com").exists()


def test_register_and_activate_account(
    client: Client,
) -> None:
    mail.outbox.clear()

    response = client.post(
        "/api/register",
        {
            "email": "temp@example.com",
            "password": "secret",
        },
        content_type="application/json",
    )
    assert response.status_code == 200
    assert User.objects.filter(email="temp@example.com").exists()

    message_body = mail.outbox[-1].message().get_payload()
    assert "/confirm_account?code=" \
        in message_body

    confirmation_link_search = re.search(
        r"new account:\n (.*)",
        message_body,
    )
    assert confirmation_link_search is not None

    confirmation_link = confirmation_link_search.group(1)
    response = client.get(confirmation_link)
    assert response.status_code == 302
    assert response["Location"] == (
        "http://testserver//login?activation_successful=True"
    )

    assert client.login(
        email="temp@example.com",
        password="secret",
    ) is True


def test_register_email_taken(client: Client) -> None:
    response = client.post(
        "/api/register",
        {"email": "user@example.com",
         "password": "secret"},
        content_type="application/json",
    )
    assert response.status_code == 400
    assert response.json() == {
        "error": "This email is already in use",
    }


def test_register_bad_requests(client: Client) -> None:
    response = client.post(
        "/api/register",
        {"email": "gosho@example.com"},
        content_type="application/json",
    )
    assert response.status_code == 400
    assert response.json() == {
        "error": "A password is required to register",
    }

    response = client.post(
        "/api/register",
        {"password": "secret"},
        content_type="application/json",
    )
    assert response.status_code == 400
    assert response.json() == {
        "error": "An email is required to register",
    }


def test_login(client: Client) -> None:
    response = client.post(
        "/api/login",
        {"email": "user@example.com",
         "password": "secret"},
        content_type="application/json",
    )
    assert response.status_code == 200
    assert response.json() == {
        "email": "user@example.com",
        "isAdmin": False,
    }

    assert "sessionid" in response.cookies
    assert response.cookies["sessionid"]

    assert "csrftoken" in response.cookies
    assert response.cookies["csrftoken"]


def test_login_admin(client: Client) -> None:
    response = client.post(
        "/api/login",
        {"email": "admin@example.com",
         "password": "secret"},
        content_type="application/json",
    )
    assert response.status_code == 200
    assert response.json() == {
        "email": "admin@example.com",
        "isAdmin": True,
    }

    assert "sessionid" in response.cookies
    assert response.cookies["sessionid"]

    assert "csrftoken" in response.cookies
    assert response.cookies["csrftoken"]


def test_login_user_wrong_password(client: Client) -> None:
    response = client.post(
        "/api/login",
        {"email": "user@example.com",
         "password": "alabala"},
        content_type="application/json",
    )
    assert response.status_code == 400
    assert response.json() == {
        "error": "Invalid login credentials",
    }


def test_login_user_does_not_exist(client: Client) -> None:
    response = client.post(
        "/api/login",
        {"email": "user-two@example.com",
         "password": "secret"},
        content_type="application/json",
    )
    assert response.status_code == 400
    assert response.json() == {
        "error": "Invalid login credentials",
    }


def test_login_bad_requests(client: Client) -> None:
    response = client.post(
        "/api/login",
        {"email": "user@example.com"},
        content_type="application/json",
    )
    assert response.status_code == 400
    assert response.json() == {
        "error": "A password is required to log in",
    }

    response = client.post(
        "/api/login",
        {"password": "secret"},
        content_type="application/json",
    )
    assert response.status_code == 400
    assert response.json() == {
        "error": "An email is required to log in",
    }


def test_load_of_reset_password_form(
    user_client: Client,
) -> None:
    response = user_client.post(
        "/api/forgotten_password",
        {"email": "user@example.com"},
    )
    assert response.status_code == 200
    assert b"An e-mail has been sent to user@example.com" in response.content

    response = user_client.post(
        "/api/forgotten_password",
        {"email": "random@example.com"},
    )
    assert response.status_code == 200
    assert b"An e-mail has been sent to random@example.com" in response.content


def test_forgotten_password_with_malformed_email(
    user_client: Client,
) -> None:
    response = user_client.post(
        "/api/forgotten_password",
        {"email": "not-an-email"},
    )
    assert response.status_code == 400
    assert b"Invalid email" in response.content


@pytest.mark.django_db
def test_forgotten_password_looks_up_the_cleaned_address(
    client: Client,
) -> None:
    """A padded address must still reach its account.

    The form's EmailField strips surrounding whitespace, so validation
    passes but the raw value matches no user. Since every valid address
    now answers with the success page, looking the raw value up would
    tell a registered caller their mail was sent and send nothing.
    """
    mail.outbox.clear()
    response = client.post(
        "/api/forgotten_password",
        {"email": "user@example.com "},
    )

    assert response.status_code == 200
    assert len(mail.outbox) == 1


@pytest.mark.django_db
def test_forgotten_password_response_does_not_reveal_registration(
    client: Client,
) -> None:
    """One address must answer identically once its account is gone."""
    mail.outbox.clear()
    registered_response = client.post(
        "/api/forgotten_password",
        {"email": "user@example.com"},
    )
    assert len(mail.outbox) == 1

    User.objects.filter(email="user@example.com").delete()

    mail.outbox.clear()
    unregistered_response = client.post(
        "/api/forgotten_password",
        {"email": "user@example.com"},
    )
    assert len(mail.outbox) == 0

    assert unregistered_response.status_code == registered_response.status_code
    assert unregistered_response.content == registered_response.content


@pytest.fixture
def failing_send_email() -> Iterator[None]:
    """Make every mail send raise, the shape of a real SMTP outage.

    send_email is patched where utils resolves it, so the verification
    code -- reset or account confirmation -- is still created and only
    the SMTP-level send raises.
    """
    with mock.patch(
        "web_annotation.utils.send_email",
        side_effect=SMTPException("connection refused"),
    ):
        yield


@pytest.mark.django_db
def test_forgotten_password_mail_failure_does_not_reveal_registration(
    client: Client,
    failing_send_email: None,
) -> None:
    """A mail outage must not reopen the oracle the uniform response closes."""
    registered_response = client.post(
        "/api/forgotten_password",
        {"email": "user@example.com"},
    )

    User.objects.filter(email="user@example.com").delete()

    unregistered_response = client.post(
        "/api/forgotten_password",
        {"email": "user@example.com"},
    )

    assert registered_response.status_code == unregistered_response.status_code
    assert registered_response.content == unregistered_response.content


@pytest.mark.django_db
def test_forgotten_password_mail_failure_is_logged(
    client: Client,
    failing_send_email: None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The swallowed mail error is how operators learn of the outage."""
    with caplog.at_level("ERROR", logger="web_annotation.views"):
        client.post(
            "/api/forgotten_password",
            {"email": "user@example.com"},
        )

    [record] = [
        r for r in caplog.records
        if r.message == "failed to create or send the reset mail"
    ]
    assert record.exc_info is not None
    assert record.exc_info[0] is SMTPException


def _assert_code_not_logged(
    caplog: pytest.LogCaptureFixture, link_path: str,
) -> None:
    """The single-use code from the sent mail is absent from every record.

    The strict 36-char match pins the entire code: if the body format
    ever wraps or re-encodes the link, this fails loudly instead of
    silently weakening the absence check to a truncated prefix.
    """
    message = mail.outbox[0].message().get_payload()
    code_match = re.search(rf"{link_path}\?code=([0-9a-f-]{{36}})", message)
    assert code_match is not None

    code = code_match.group(1)
    leaked = [r for r in caplog.records if code in r.getMessage()]
    assert leaked == []


@pytest.mark.django_db
def test_mail_send_still_logs_recipient_and_subject(
    client: Client,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Dropping the body from the logs must not drop the audit trail."""
    with caplog.at_level("INFO", logger="web_annotation.mail"):
        response = client.post(
            "/api/forgotten_password",
            {"email": "user@example.com"},
        )
    assert response.status_code == 200

    messages = [r.getMessage() for r in caplog.records]
    assert any("user@example.com" in m for m in messages)
    assert any("Password reset request" in m for m in messages)


@pytest.mark.django_db
def test_register_confirmation_code_is_not_logged(
    client: Client,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The single-use confirmation code must never reach the log stream.

    Captured unscoped at DEBUG on purpose: the code must be absent from
    every logger at every level, not just the mail logger.
    """
    mail.outbox.clear()

    with caplog.at_level("DEBUG"):
        response = client.post(
            "/api/register",
            {
                "email": "temp@example.com",
                "password": "secret",
            },
            content_type="application/json",
        )
    assert response.status_code == 200

    _assert_code_not_logged(caplog, "confirm_account")


@pytest.mark.django_db
def test_forgotten_password_reset_code_is_not_logged(
    client: Client,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The single-use reset code must never reach the log stream.

    Captured unscoped at DEBUG on purpose: the code must be absent from
    every logger at every level, not just the mail logger.
    """
    mail.outbox.clear()

    with caplog.at_level("DEBUG"):
        response = client.post(
            "/api/forgotten_password",
            {"email": "user@example.com"},
        )
    assert response.status_code == 200

    _assert_code_not_logged(caplog, "reset_password")


@pytest.mark.django_db
def test_register_succeeds_when_the_confirmation_mail_cannot_be_sent(
    client: Client,
    failing_send_email: None,
) -> None:
    """A mail outage answers exactly as a successful registration does."""
    response = client.post(
        "/api/register",
        {
            "email": "outage@example.com",
            "password": "secret",
        },
        content_type="application/json",
    )

    assert response.status_code == 200
    assert response.content == b""


@pytest.mark.django_db
def test_account_registered_during_a_mail_outage_can_log_in(
    client: Client,
    failing_send_email: None,
) -> None:
    """Answering 200 is only honest if the account behind it works.

    USERS_ACTIVATED_BY_DEFAULT leaves the new user active, so the
    confirmation mail is informational and its loss must not keep the
    owner out of the account they just created.
    """
    client.post(
        "/api/register",
        {
            "email": "outage@example.com",
            "password": "secret",
        },
        content_type="application/json",
    )

    response = client.post(
        "/api/login",
        {
            "email": "outage@example.com",
            "password": "secret",
        },
        content_type="application/json",
    )

    assert response.status_code == 200
    assert response.json() == {
        "email": "outage@example.com",
        "isAdmin": False,
    }


@pytest.mark.django_db
def test_register_mail_failure_is_logged(
    client: Client,
    failing_send_email: None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The log is how operators learn the confirmation mail was lost."""
    with caplog.at_level("ERROR", logger="web_annotation.views"):
        client.post(
            "/api/register",
            {
                "email": "outage@example.com",
                "password": "secret",
            },
            content_type="application/json",
        )

    [record] = [
        r for r in caplog.records
        if r.message == "failed to create or send the confirmation mail"
    ]
    assert record.name == "web_annotation.views"
    assert record.exc_info is not None
    assert record.exc_info[0] is SMTPException


@pytest.mark.django_db
def test_reset_password_email(
    client: Client,
) -> None:
    mail.outbox.clear()

    response = client.post(
        "/api/forgotten_password",
        {"email": "user@example.com"},
    )
    assert response.status_code == 200

    assert len(mail.outbox) == 1

    message = mail.outbox[0].message().get_payload()
    assert "/reset_password?code=" in message


@pytest.mark.django_db
def test_load_reset_password_form(
    client: Client,
) -> None:
    mail.outbox.clear()

    response = client.post(
        "/api/forgotten_password",
        {"email": "user@example.com"},
    )
    assert response.status_code == 200

    message = mail.outbox[0].message().get_payload()
    link_search = re.search(
        r":8000(.*)\n",
        message,
    )
    assert link_search is not None

    reset_password_form_link = link_search.group(1)

    response = client.get(reset_password_form_link)

    assert response.status_code == 200

    template_html = response.content.decode("utf-8")

    form_action_search = re.search(
        r'<form method="post" action="(.*)">\n',
        template_html,
    )
    assert form_action_search is not None
    assert form_action_search.group(1) == "/api/reset_password"


@pytest.mark.django_db
def test_reset_password_form(
    client: Client,
) -> None:
    mail.outbox.clear()
    user = User.objects.create_user(
        "temp-user",
        "temp@example.com",
        "secret",
    )
    user.save()

    response = client.post(
        "/api/forgotten_password",
        {"email": "temp@example.com"},
    )
    assert response.status_code == 200

    message = mail.outbox[0].message().get_payload()
    code_search = re.search(
        r"code=(.*)\n",
        message,
    )
    assert code_search is not None

    code = code_search.group(1)

    response = client.post(
        "/api/reset_password",
        data={
            "code": code,
            "new_password1": "newsecret",
            "new_password2": "newsecret",
        },
    )
    assert response.status_code == 302
    assert response["Location"] == "http://testserver//login"
    assert client.login(
        email="temp@example.com",
        password="newsecret",
    ) is True


@pytest.mark.django_db
def test_reset_password_form_with_invalid_code(
    user_client: Client,
    client: Client,
) -> None:
    mail.outbox.clear()

    user = User.objects.create_user(
        "temp-user",
        "temp@example.com",
        "secret",
    )
    user.save()

    response = user_client.post(
        "/api/forgotten_password",
        {"email": "temp@example.com"},
    )
    assert response.status_code == 200

    message = mail.outbox[0].message().get_payload()
    code_search = re.search(
        r"code=(.*)\n",
        message,
    )
    assert code_search is not None

    code = code_search.group(1)

    first_reset_response = client.post(
        "/api/reset_password",
        data={
            "code": code,
            "new_password1": "newsecret",
            "new_password2": "newsecret",
        },
    )
    assert first_reset_response.status_code == 302

    second__reset_response = client.post(
        "/api/reset_password",
        data={
            "code": code,
            "new_password1": "newsupersecret",
            "new_password2": "newsupersecret",
        },
    )
    assert second__reset_response.status_code == 400
    template_html = second__reset_response.content.decode("utf-8")
    assert "Invalid reset code" in template_html


def test_activation_of_account_through_reset_password(
    client: Client,
) -> None:
    mail.outbox.clear()

    response = client.post(
        "/api/register",
        {
            "email": "temp@example.com",
            "password": "secret",
        },
        content_type="application/json",
    )

    assert response.status_code == 200
    assert User.objects.filter(email="temp@example.com").exists()

    mail.outbox.clear()

    response = client.post(
        "/api/forgotten_password",
        {"email": "temp@example.com"},
    )
    assert response.status_code == 200

    message = mail.outbox[0].message().get_payload()
    code_search = re.search(
        r"code=(.*)\n",
        message,
    )
    assert code_search is not None

    code = code_search.group(1)

    response = client.post(
        "/api/reset_password",
        data={
            "code": code,
            "new_password1": "newsecret",
            "new_password2": "newsecret",
        },
    )
    assert response.status_code == 302
    assert client.login(
        email="temp@example.com",
        password="newsecret",
    ) is True


@pytest.mark.django_db
def test_get_user_info(user_client: Client) -> None:
    response = user_client.get("/api/user_info")
    assert response.status_code == 200
    assert response.json() == {
        "loggedIn": True,
        "email": "user@example.com",
        "limitations": {
            "dailyJobs": 5,
            "filesize": "64M",
            "todayJobsCount": 1,
            "diskSpace": "10.0 MB / 2.0 GB",
        },
    }


@pytest.mark.django_db
def test_get_user_info_unauthorized(anonymous_client: Client) -> None:
    response = anonymous_client.get("/api/user_info")
    assert response.status_code == 200
    assert response.json() == {
        "loggedIn": False,
        "email": None,
        "limitations": {
            "dailyJobs": 5,
            "diskSpace": "0.1 KB / 2.0 GB",
            "filesize": "64M",
            "todayJobsCount": 0,
        },
    }


@pytest.mark.django_db
def test_get_user_info_cookie() -> None:
    client = Client()
    response = client.get("/api/user_info")
    assert response.status_code == 200
    assert "sessionid" in response.cookies
    first_cookie = response.cookies["sessionid"].value
    assert first_cookie is not None
    client.login(email="user@example.com", password="secret")
    assert response.status_code == 200
    assert "sessionid" in response.cookies
    second_cookie = response.cookies["sessionid"].value
    assert second_cookie is not None
    assert first_cookie != second_cookie
    response = client.get("/api/logout")
    assert response.status_code == 200
    assert "sessionid" in response.cookies
    third_cookie = response.cookies["sessionid"].value
    assert third_cookie is not None
    assert second_cookie != third_cookie


def test_about_page(clients: dict[str, Client]) -> None:

    for client_type, client in clients.items():
        response = client.get("/api/about")
        assert response.status_code == 200, client_type
        assert response.headers["Content-Type"] == "text/markdown", client_type
        assert len(response.content) > 0, client_type
        assert response.content.decode().find(
            "Genomic Annotation Infrastructure (GAIn) "
            "is an open-source platform") != -1, client_type


def test_version(clients: dict[str, Client]) -> None:
    for client_type, client in clients.items():
        response = client.get("/api/version")
        assert response.status_code == 200, client_type
        assert response.json()["version"] == version(
            "gain-core"), client_type
