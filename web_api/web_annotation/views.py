# pylint: disable=too-many-lines
"""View classes for web annotation."""
import logging
import pathlib
from typing import cast

from django import forms
from django.conf import settings
from django.contrib.auth import (
    authenticate,
    get_user_model,
    login,
    logout,
)
from django.http import HttpRequest
from django.http.response import (
    HttpResponse,
    HttpResponseRedirect,
)
from django.shortcuts import render
from django.utils import timezone
from rest_framework import generics, permissions, views
from rest_framework.parsers import JSONParser
from rest_framework.request import QueryDict, Request
from rest_framework.response import Response
from web_annotation.serializers import UserSerializer
from web_annotation.authentication import WebAnnotationAuthentication
from web_annotation.utils import (
    PasswordForgottenForm,
    ResetPasswordForm,
    bytes_to_readable,
    calculate_used_disk_space,
    check_request_verification_path,
    convert_size,
    deauthenticate,
    reset_password,
    verify_user,
)


from .models import (
    AccountConfirmationCode,
    BaseVerificationCode,
    ResetPasswordCode,
    User,
)

logger = logging.getLogger(__name__)


class UserList(generics.ListAPIView):
    """Generic view for listing users."""
    queryset = User.objects.all()
    serializer_class = UserSerializer
    authentication_classes = [WebAnnotationAuthentication]
    permission_classes = [permissions.IsAdminUser]


class UserDetail(generics.RetrieveAPIView):
    """Generic view for listing a user's details"""
    queryset = User.objects.all()
    serializer_class = UserSerializer
    authentication_classes = [WebAnnotationAuthentication]
    permission_classes = [permissions.IsAdminUser]


class UserInfo(views.APIView):
    """View that returns the request session's user information."""

    authentication_classes = [WebAnnotationAuthentication]

    def get_user_daily_limit(self, user: User | None = None) -> int | None:
        """Return the daily job limit for a user."""
        if user is not None and user.is_superuser:
            return None
        return cast(int, settings.QUOTAS["daily_jobs"])

    def get_user_filesize_limit(self, user: User | None = None) -> str | None:
        """Return the file size limit for a user."""
        if user is not None and user.is_superuser:
            return None
        return cast(str, settings.QUOTAS["filesize"])

    def get_user_variant_limit(self, user: User | None = None) -> int | None:
        """Return the variant count limit for a user."""
        if user is not None and user.is_superuser:
            return None
        return cast(int, settings.QUOTAS["variant_count"])

    def get_today_jobs_count(self, user: User) -> int:
        """Return the number of jobs a user created today."""
        today = timezone.now().replace(
            hour=0, minute=0, second=0, microsecond=0)
        jobs_count = user.job_class.objects.filter(
            created__gte=today,
            owner__exact=user.pk,
        ).count()
        return jobs_count

    def get(self, request: Request) -> Response:
        """Get a user's info and limitations."""
        user = request.user
        info = {
            "loggedIn": False,
            "email": None,
            "limitations": {
                "dailyJobs": self.get_user_daily_limit(user),
                "filesize": self.get_user_filesize_limit(user),
                "variantCount": self.get_user_variant_limit(user),
                "todayJobsCount": self.get_today_jobs_count(user),
                "diskSpace": (
                    f"{bytes_to_readable(
                        calculate_used_disk_space(user)
                    )} "
                    f"/ {bytes_to_readable(
                        convert_size(str(settings.QUOTAS["disk_space"])))}"
                ),
            }
        }
        if user.is_authenticated:
            info["loggedIn"] = True
            info["email"] = user.email

        return Response(info, views.status.HTTP_200_OK)


class Logout(views.APIView):
    """View for logging out."""

    authentication_classes = [WebAnnotationAuthentication]

    def get(self, request: Request) -> Response:
        logout(cast(HttpRequest, request))
        request.session.flush()
        request.session.save()
        return Response(views.status.HTTP_204_NO_CONTENT)


class Login(views.APIView):
    """View for logging in."""
    parser_classes = [JSONParser]

    authentication_classes = [WebAnnotationAuthentication]

    def post(self, request: Request) -> Response:
        """Log in a user."""
        assert isinstance(request.data, dict)
        if "email" not in request.data:
            return Response(
                {"error": "An email is required to log in"},
                status=views.status.HTTP_400_BAD_REQUEST)
        if "password" not in request.data:
            return Response(
                {"error": "A password is required to log in"},
                status=views.status.HTTP_400_BAD_REQUEST)

        email = request.data["email"]
        password = request.data["password"]
        assert isinstance(email, str)
        assert isinstance(password, str)

        user = authenticate(
            cast(HttpRequest, request), email=email, password=password)
        if user is None:
            return Response(
                {"error": "Invalid login credentials"},
                status=views.status.HTTP_400_BAD_REQUEST)

        request.session.flush()
        request.session.save()

        login(cast(HttpRequest, request), user)

        umodel = User.objects.get(email=email)

        return Response(
            {"email": umodel.email,
             "isAdmin": umodel.is_superuser},
            status=views.status.HTTP_200_OK)


class Registration(views.APIView):
    """Registration related view."""
    parser_classes = [JSONParser]
    authentication_classes = [WebAnnotationAuthentication]

    def post(self, request: Request) -> Response:
        """Register a new user."""
        assert isinstance(request.data, dict)
        if "email" not in request.data:
            return Response(
                {"error": "An email is required to register"},
                status=views.status.HTTP_400_BAD_REQUEST)
        if "password" not in request.data:
            return Response(
                {"error": "A password is required to register"},
                status=views.status.HTTP_400_BAD_REQUEST)

        email = str(request.data["email"])
        password = str(request.data["password"])

        if User.objects.filter(email=email).exists():
            return Response(
                {"error": "This email is already in use"},
                status=views.status.HTTP_400_BAD_REQUEST)

        user = User.objects.create_user(
            email,
            email,
            password,
            is_active=settings.USERS_ACTIVATED_BY_DEFAULT,
        )
        user.save()
        verify_user(user)
        return Response(status=views.status.HTTP_200_OK)


class ConfirmAccount(views.APIView):  # USE
    """View for forgotten password."""
    verification_code_model = cast(
        BaseVerificationCode, AccountConfirmationCode,
    )
    code_type = "confirmation"

    def get(self, request: Request) -> HttpResponse:
        """Render the password reset form."""

        verif_code, msg = \
            check_request_verification_path(
                request.GET.get("code"),
                request,
                self.code_type,
                self.verification_code_model,
            )

        if msg is not None:
            if verif_code is not None:
                verif_code.delete()

        activated = False
        if verif_code is not None:
            user: User = verif_code.user
            verif_code.delete()
            user.activate()
            activated = True

        redirect_uri = (
            f"{settings.EMAIL_REDIRECT_ENDPOINT}"
            f"/login?activation_successful={activated}"
        )
        return HttpResponseRedirect(redirect_uri)


class ForgotPassword(views.APIView):
    """View for forgotten password."""

    authentication_classes = [WebAnnotationAuthentication]

    def get(self, request: Request) -> HttpResponse:
        form = PasswordForgottenForm()

        return render(
            cast(HttpRequest, request),
            "forgotten-password.html",
            {"form": form, "show_form": True},
        )

    def post(self, request: Request) -> HttpResponse:
        """Send a reset password email to the user."""
        data = request.data
        form = PasswordForgottenForm(data)  # pyright: ignore
        is_valid = form.is_valid()
        if not is_valid:
            return render(
                cast(HttpRequest, request),
                "forgotten-password.html",
                {
                    "form": form,
                    "message": "Invalid email",
                    "message_type": "warn",
                    "show_form": True,
                },
                status=views.status.HTTP_400_BAD_REQUEST,
            )
        email = form.data["email"]
        user_model = get_user_model()
        message = (
            f"An e-mail has been sent to {email}"
            " containing the reset link"
        )
        try:
            user = user_model.objects.filter(email=email).first()
            if user is None or not isinstance(user, User):
                return render(
                    request,  # pyright: ignore
                    "forgotten-password.html",
                    {
                        "form": form,
                        "message": "User is not a GPFWA User",
                        "message_type": "warn",
                        "show_form": True,
                    },
                    status=views.status.HTTP_400_BAD_REQUEST,
                )

            reset_password(user)

            return render(
                request,  # pyright: ignore
                "forgotten-password.html",
                {
                    "form": form,
                    "message": message,
                    "message_type": "success",
                    "show_form": False,
                },
            )
        except user_model.DoesNotExist:
            return render(
                request,  # pyright: ignore
                "forgotten-password.html",
                {
                    "form": form,
                    "message": message,
                    "message_type": "success",
                    "show_form": False,
                },
            )


class PasswordReset(views.APIView):
    """Reset password view."""

    verification_code_model = cast(BaseVerificationCode, ResetPasswordCode)

    template = "reset-password.html"
    form = cast(forms.Form, ResetPasswordForm)
    code_type = "reset"

    def get(self, request: Request) -> HttpResponse:
        """Render the password reset form."""

        verif_code, msg = \
            check_request_verification_path(
                request.GET.get("code"),
                request,
                self.code_type,
                self.verification_code_model,
            )

        if msg is not None:
            if verif_code is not None:
                verif_code.delete()
            assert self.template is not None
            return render(
                request,  # pyright: ignore
                self.template,
                {"message": msg},
                status=views.status.HTTP_400_BAD_REQUEST,
            )
        assert verif_code is not None
        user = verif_code.user

        assert self.form is not None
        # pylint: disable=not-callable
        form = self.form(user)  # type: ignore
        request.session[f"{self.code_type}_code"] = verif_code.path
        request.path = request.path[:request.path.find("?")]  # pyright: ignore
        assert self.template is not None
        return render(
            request,  # pyright: ignore
            self.template,
            {"form": form},
        )

    def post(self, request: Request) -> HttpResponse:
        """Handle the password reset form."""
        assert isinstance(request.POST, QueryDict)
        verif_code, msg = \
            check_request_verification_path(
                request.POST.get("code"),
                request,
                self.code_type,
                self.verification_code_model,
            )
        assert self.template is not None
        if msg is not None:
            if verif_code is not None:
                verif_code.delete()
            return render(
                request,  # pyright: ignore
                self.template,
                {"message": msg},
                status=views.status.HTTP_400_BAD_REQUEST,
            )
        assert verif_code is not None
        user: User = verif_code.user
        # pylint: disable=not-callable
        form = self.form(user, data=request.data)  # type: ignore
        is_valid = form.is_valid()
        if not is_valid:
            return render(
                request,  # pyright: ignore
                self.template,
                {
                    "form": form,
                },
                status=views.status.HTTP_400_BAD_REQUEST,
            )

        new_password = form.cleaned_data["new_password1"]
        if not user.is_active:
            user.activate()
        else:
            deauthenticate(user)
        user.change_password(new_password)

        if verif_code is not None:
            verif_code.delete()

        redirect_uri = settings.EMAIL_REDIRECT_ENDPOINT
        return HttpResponseRedirect(f"{redirect_uri}/login")


class AboutPage(views.APIView):
    """View for the about page."""

    def get(self, _: Request) -> Response:
        """Return the about page content."""
        about_path = pathlib.Path(__file__).parent / "about.md"
        about = about_path.read_text().strip()
        return Response(
            about,
            content_type="text/markdown",
            status=views.status.HTTP_200_OK,
        )
