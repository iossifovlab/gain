# pylint: disable=C0114,C0116,W0212
from web_annotation.utils import (
    _create_confirmation_email,
    _create_reset_mail,
)


def test_confirmation_email_uses_gain_branding() -> None:
    email = _create_confirmation_email(
        "http://example.com", "/confirm/{}", "abc",
    )

    assert email["subject"] == "GAIn: Registration validation"
    assert email["message"].startswith(
        "Welcome to GAIn: Genomic Annotation Infrastructure! ",
    )
    assert "GPF" not in email["message"]
    assert "http://example.com/confirm/abc" in email["message"]


def test_reset_email_uses_gain_branding() -> None:
    email = _create_reset_mail("http://example.com", "/reset/{}", "abc")

    assert email["subject"] == "GAIn: Password reset request"
    assert "your GAIn account" in email["message"]
    assert "GPF" not in email["message"]
    assert "http://example.com/reset/abc" in email["message"]
