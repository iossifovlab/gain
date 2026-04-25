import subprocess

from functools import reduce
from typing import Any

from django import forms
from django.conf import settings
from django.contrib.auth import password_validation
from django.contrib.sessions.models import Session
from django.core.exceptions import ValidationError
from django.db.models import ObjectDoesNotExist
from django.utils.translation import gettext_lazy
from django.views.decorators.debug import sensitive_variables
from rest_framework.request import Request

from web_annotation.models import (
    AccountConfirmationCode,
    BaseVerificationCode,
    ResetPasswordCode,
    User,
    WebAnnotationAnonymousUser,
)
from web_annotation.mail import send_email


EMAIL_ACCOUNT_CONFIRMATION_PATH = "/api/confirm_account?code={}"
EMAIL_VERIFICATION_RESET_PATH = "/api/reset_password?code={}"


def verify_user(user: User) -> None:
    verif_code = AccountConfirmationCode.create(user)
    send_confirmation_email(user, verif_code)


def send_confirmation_email(
    user: User, verif_path: BaseVerificationCode,
) -> None:
    """Return dict with subject and message of the email."""
    # pylint: disable=import-outside-toplevel
    email = _create_confirmation_email(
        settings.EMAIL_VERIFICATION_ENDPOINT,
        EMAIL_ACCOUNT_CONFIRMATION_PATH,
        str(verif_path.path),
    )
    send_email(email["subject"], email["message"], [user.email])


def _create_confirmation_email(
    endpoint: str, path: str, verification_path: str,
) -> dict[str, str]:
    message = (
        "Welcome to GPFWA: Genotype and Phenotype in Families Web Annotation! "
        "Click the link below to activate your new account:\n {link}"
    )

    email_settings = {
        "subject": "GPFWA: Registration validation",
        "initial_message": message,
        "endpoint": endpoint,
        "path": path,
        "verification_path": verification_path,
    }

    return _build_email_template(email_settings)


class PasswordForgottenForm(forms.Form):
    email = forms.EmailField(
        label="Email",
        max_length=254,
        widget=forms.EmailInput(attrs={"autocomplete": "email"}),
    )

def reset_password(user: User) -> None:
    verif_code = ResetPasswordCode.create(user)
    send_reset_email(user, verif_code)

def deauthenticate(user: User) -> None:
    all_sessions = Session.objects.all()
    for session in all_sessions:
        session_data = session.get_decoded()
        if user.pk == session_data.get("_auth_user_id"):
            session.delete()

def send_reset_email(
    user: User, verif_path: BaseVerificationCode,
) -> None:
    """Return dict with subject and message of the email."""
    # pylint: disable=import-outside-toplevel
    email = _create_reset_mail(
        settings.EMAIL_VERIFICATION_ENDPOINT,
        EMAIL_VERIFICATION_RESET_PATH,
        str(verif_path.path),
    )
    send_email(email["subject"], email["message"], [user.email])

def _create_reset_mail(
    endpoint: str, path: str, verification_path: str,
) -> dict[str, str]:
    """Create email template for password reset."""
    message = (
        "Hello. You have requested to reset your password for "
        "your GPF Web Annotation account. To do so, please "
        "follow the link below:\n {link}\n"
        "If you did not request for your GPF account password to be reset, "
        "please ignore this email."
    )
    email_settings = {
        "subject": "GPFWA: Password reset request",
        "initial_message": message,
        "endpoint": endpoint,
        "path": path,
        "verification_path": verification_path,
    }

    return _build_email_template(email_settings)

def _build_email_template(email_settings: dict[str, str]) -> dict[str, str]:
    subject = email_settings["subject"]
    message = email_settings["initial_message"]
    path = email_settings["path"].format(
        email_settings["verification_path"],
    )

    message = message.format(link=f"{email_settings['endpoint']}{path}")

    return {"subject": subject, "message": message}

def create_password_fields(
    label1: Any = gettext_lazy("Password"),
    label2: Any = gettext_lazy("Password confirmation"),
) -> tuple[forms.CharField, forms.CharField]:
    """Create two password fields."""
    password1 = forms.CharField(
        label=label1,
        required=True,
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        help_text=password_validation.password_validators_help_text_html(),
    )
    password2 = forms.CharField(
        label=label2,
        required=True,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        strip=False,
        help_text=gettext_lazy(
            "Enter the same password as before, for verification.",
        ),
    )
    return password1, password2


class SetPasswordForm(forms.Form):
    """
    A form that lets a user set their password without entering the old
    password
    """

    new_password1, new_password2 = create_password_fields(
        label1=gettext_lazy("New password"),
        label2=gettext_lazy("New password confirmation"),
    )
    error_messages = {
        "password_mismatch": gettext_lazy(
            "The two password fields didn’t match.",
        ),
    }

    def __init__(self, user: User, *args: Any, **kwargs: Any) -> None:
        self.user = user
        super().__init__(*args, **kwargs)

    def clean(self) -> dict[str, Any] | None:
        self.validate_passwords("new_password1", "new_password2")
        self.validate_password_for_user(self.user, "new_password2")
        return super().clean()

    def save(self, commit: bool = True) -> User:
        return self.set_password_and_save(
            self.user,
            "new_password1",
            commit=commit,
        )

    @sensitive_variables("password1", "password2")
    def validate_passwords(
        self,
        password1_field_name: str = "password1",
        password2_field_name: str = "password2",
    ) -> None:
        """Validate that the two password entries match."""
        password1 = self.cleaned_data.get(password1_field_name)
        password2 = self.cleaned_data.get(password2_field_name)

        if password1 and password2 and password1 != password2:
            error = ValidationError(
                self.error_messages["password_mismatch"],
                code="password_mismatch",
            )
            self.add_error(password2_field_name, error)

    @sensitive_variables("password")
    def validate_password_for_user(
        self,
        user: User,
        password_field_name: str = "password2",
    ) -> None:
        """Validate the password."""
        password = self.cleaned_data.get(password_field_name)
        if password:
            try:
                password_validation.validate_password(password, user)
            except ValidationError as error:
                self.add_error(password_field_name, error)

    def set_password_and_save(
        self,
        user: User,
        password_field_name: str = "password1",
        commit: bool = True,
    ) -> User:
        """Set the user's password and save the user."""
        user.set_password(self.cleaned_data[password_field_name])
        if commit:
            user.save()
        return user


class ResetPasswordForm(SetPasswordForm):
    """A form for users to reset their password when forgotten."""

    error_messages = {
        "password_invalid": gettext_lazy(
            "Your password is either too short "
            "(less than 10 symbols) or too weak.",
        ),
        "password_mismatch": gettext_lazy(
            "The two passwords do not match.",
        ),
    }


def check_request_verification_path(
   verification_path: str | None,
   request: Request,
   code_type: str,
   verification_code_model: BaseVerificationCode,
) -> tuple[BaseVerificationCode | None, str | None]:
    """
    Check, validate and return a verification path from a request.

    Returns a tuple of the model instance and the error message if any.
    When the instance is not found, None is returned.
    """

    if verification_path is None:
        verification_path = request.session.get(f"{code_type}_code")
    if verification_path is None:
        return None, f"No {code_type} code provided"
    try:
        assert verification_path is not None
        assert verification_code_model is not None
        verif_code = \
            verification_code_model.objects.get(  # type: ignore
                path=verification_path)
    except ObjectDoesNotExist:
        return None, f"Invalid {code_type} code"

    is_valid = verif_code.validate()  # pyright: ignore

    if not is_valid:
        return verif_code, f"Expired {code_type} code"

    return verif_code, None


def convert_size(filesize: str | int) -> int:
    """Convert a human readable filesize string to bytes."""
    if isinstance(filesize, int):
        return filesize
    filesize = filesize.upper()
    units: dict[str, int] = {
        "KB": 10**3, "MB": 10**6, "GB": 10**9, "TB": 10**12,
        "K": 10**3, "M": 10**6, "G": 10**9, "T": 10**12,
    }
    for unit, mult in units.items():
        if filesize.endswith(unit):
            return int(float(filesize.rstrip(f"{unit}").strip()) * mult)
    return int(filesize)


def calculate_used_disk_space(user: User | WebAnnotationAnonymousUser) -> int:
    """Calculate used job disk space for a user."""
    user_jobs = user.get_jobs()
    used_disk_space = reduce(
        lambda x, y: x + y,
        [int(job.disk_size) for job in user_jobs],
        0,
    )
    return used_disk_space


def bytes_to_readable(raw_bytes: int) -> str:
    """Convert a human readable filesize string to bytes."""
    if isinstance(raw_bytes, str):
        return raw_bytes
    units: dict[str, int] = {
        "KB": 10**3,
        "MB": 10**6,
        "GB": 10**9,
        "TB": 10**12,
    }
    result = "0.1 KB"
    for unit, mult in units.items():
        if raw_bytes > mult:
            result = f"{format(raw_bytes / mult, '.1f')} {unit}"
        else:
            break
    return result

def get_ip_from_request(request: Request) -> str:
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0]
    else:
        ip = request.META.get('REMOTE_ADDR')
    return str(ip)

def validate_vcf(
    file_path: str,
    limit: int | None = None,
) -> bool:
    """Check if a variants file is valid."""

    args = [
        "validate_vcf_file",
        file_path,
    ]
    if limit is not None:
        args.extend(["--limit", str(limit)])

    proc = subprocess.run(
        args,
        check=True,
        text=True,
        capture_output=True,
    )
    return proc.stdout.strip() == "valid"
