import dataclasses
from typing import ClassVar

from django.core.management import call_command
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView
from web_annotation.authentication import WebAnnotationAuthentication
from web_annotation.models import (
    AnonymousJob,
    AnonymousUserQuota,
    Quota,
    QuotaSnapshot,
    SessionQuota,
    User,
    UserQuota,
)

_EXTRA_QUOTA_FIELDS = {
    "jobs": "extra_jobs",
    "variants": "extra_variants",
    "attributes": "extra_attributes",
}

_CURRENT_QUOTA_FIELDS = {
    "daily_jobs", "monthly_jobs",
    "daily_variants", "monthly_variants",
    "daily_attributes", "monthly_attributes",
}


def _get_or_create_user_quota(user: User) -> UserQuota:
    quota, created = UserQuota.objects.get_or_create(user=user)
    if created:
        quota.reset_daily()
        quota.reset_monthly()
    return quota


def _quota_snapshot_response(quota: Quota) -> Response:
    snapshot = QuotaSnapshot.from_quota(quota)
    return Response(dataclasses.asdict(snapshot))


def _get_or_create_session_quota(session_id: str) -> SessionQuota:
    quota, created = SessionQuota.objects.get_or_create(
        session_id=session_id)
    if created:
        quota.reset_daily()
        quota.reset_monthly()
    return quota


def _get_or_create_ip_quota(ip: str) -> AnonymousUserQuota:
    quota, created = AnonymousUserQuota.objects.get_or_create(ip=ip)
    if created:
        quota.reset_daily()
        quota.reset_monthly()
    return quota


class AdminPanelView(APIView):
    authentication_classes: ClassVar[list] = [WebAnnotationAuthentication]


class ResetDailyQuotaView(AdminPanelView):
    def get(self, _request: Request) -> Response:
        call_command("refreshdaily")
        return Response(status=status.HTTP_204_NO_CONTENT)


class ResetMonthlyQuotaView(AdminPanelView):
    def get(self, _request: Request) -> Response:
        call_command("refreshmonthly")
        return Response(status=status.HTTP_204_NO_CONTENT)


class SetExtraQuotaView(AdminPanelView):
    """View for setting extra quotas (jobs/variants/attributes)."""

    def get(self, request: Request) -> Response:
        """Get request for setting extra quota."""
        user_email = request.query_params.get("user_email")
        quota_type = request.query_params.get("quota_type")
        amount = request.query_params.get("amount")

        if not user_email or not quota_type or amount is None:
            return Response(
                "Missing required parameters.",
                status=status.HTTP_400_BAD_REQUEST)
        if quota_type not in _EXTRA_QUOTA_FIELDS:
            valid = ", ".join(_EXTRA_QUOTA_FIELDS)
            return Response(
                f"Invalid quota_type. Valid types: {valid}.",
                status=status.HTTP_400_BAD_REQUEST)
        try:
            amount_int = int(amount)
        except ValueError:
            return Response(
                "amount must be an integer.",
                status=status.HTTP_400_BAD_REQUEST)

        try:
            user = User.objects.get(email=user_email)
        except User.DoesNotExist:
            return Response(
                f"User '{user_email}' not found.",
                status=status.HTTP_404_NOT_FOUND)

        quota = _get_or_create_user_quota(user)
        setattr(quota, _EXTRA_QUOTA_FIELDS[quota_type], amount_int)
        quota.save()

        return _quota_snapshot_response(quota)


class SetCurrentQuotaView(AdminPanelView):
    """View for setting current quotas (daily/monthly)."""

    def get(self, request: Request) -> Response:
        """Get request for setting current quota."""
        user_email = request.query_params.get("user_email")
        quota_type = request.query_params.get("quota_type")
        amount = request.query_params.get("amount")

        if not user_email or not quota_type or amount is None:
            return Response(
                "Missing required parameters.",
                status=status.HTTP_400_BAD_REQUEST)
        if quota_type not in _CURRENT_QUOTA_FIELDS:
            valid = ", ".join(sorted(_CURRENT_QUOTA_FIELDS))
            return Response(
                f"Invalid quota_type. Valid types: {valid}.",
                status=status.HTTP_400_BAD_REQUEST)
        try:
            amount_int = int(amount)
        except ValueError:
            return Response(
                "amount must be an integer.",
                status=status.HTTP_400_BAD_REQUEST)

        try:
            user = User.objects.get(email=user_email)
        except User.DoesNotExist:
            return Response(
                f"User '{user_email}' not found.",
                status=status.HTTP_404_NOT_FOUND)

        quota = _get_or_create_user_quota(user)
        setattr(quota, quota_type, amount_int)
        quota.save()

        return _quota_snapshot_response(quota)


class SetSessionQuotaView(AdminPanelView):
    """View for session-based quotas."""

    def get(self, request: Request) -> Response:
        """Get request for setting session quota."""
        session_id = request.query_params.get("session_id")
        if not session_id:
            session_id = request.session.session_key
        quota_type = request.query_params.get("quota_type")
        amount = request.query_params.get("amount")

        if not session_id:
            return Response(
                "Missing session_id.", status=status.HTTP_400_BAD_REQUEST)
        if not quota_type or amount is None:
            return Response(
                "Missing required parameters.",
                status=status.HTTP_400_BAD_REQUEST)
        if quota_type not in _CURRENT_QUOTA_FIELDS:
            valid = ", ".join(sorted(_CURRENT_QUOTA_FIELDS))
            return Response(
                f"Invalid quota_type. Valid types: {valid}.",
                status=status.HTTP_400_BAD_REQUEST)
        try:
            amount_int = int(amount)
        except ValueError:
            return Response(
                "amount must be an integer.",
                status=status.HTTP_400_BAD_REQUEST)

        quota = _get_or_create_session_quota(session_id)
        setattr(quota, quota_type, amount_int)
        quota.save()

        return _quota_snapshot_response(quota)


class SetIpQuotaView(AdminPanelView):
    """View for IP-based quotas."""

    def get(self, request: Request) -> Response:
        """
        Get request for setting IP quota.

        Without an IP provided, uses the IP of the request.
        """
        ip = request.query_params.get("ip")
        if not ip:
            if request.user.is_authenticated:
                return Response(
                    "ip parameter is required for authenticated users.",
                    status=status.HTTP_400_BAD_REQUEST)
            ip = request.user.ip
        quota_type = request.query_params.get("quota_type")
        amount = request.query_params.get("amount")

        if not ip:
            return Response("Missing ip.", status=status.HTTP_400_BAD_REQUEST)
        if not quota_type or amount is None:
            return Response(
                "Missing required parameters.",
                status=status.HTTP_400_BAD_REQUEST)
        if quota_type not in _CURRENT_QUOTA_FIELDS:
            valid = ", ".join(sorted(_CURRENT_QUOTA_FIELDS))
            return Response(
                f"Invalid quota_type. Valid types: {valid}.",
                status=status.HTTP_400_BAD_REQUEST)
        try:
            amount_int = int(amount)
        except ValueError:
            return Response(
                "amount must be an integer.",
                status=status.HTTP_400_BAD_REQUEST)

        quota = _get_or_create_ip_quota(ip)
        setattr(quota, quota_type, amount_int)
        quota.save()

        return _quota_snapshot_response(quota)


class DeleteAnonymousJobsView(AdminPanelView):
    """E2E-only reset endpoint: delete all anonymous jobs for an IP.

    #216 stopped reaping completed anonymous jobs on WebSocket disconnect, so
    ``AnonymousJob`` rows now accumulate across tests on the shared CI IP and
    trip ``can_create()``'s hard per-IP daily-jobs cap. This lets each e2e
    test reset its IP's rows back to zero. It is a full test reset: ALL rows
    for the IP are deleted, including active (WAITING/IN_PROGRESS) ones -- the
    production janitor spares active jobs, but a test reset wants a clean
    slate. Only reachable in e2e (admin_panel is in INSTALLED_APPS only via
    settings_e2e), never in production.
    """

    def get(self, request: Request) -> Response:
        """Delete the caller's anonymous jobs, resolving the IP like SetIp."""
        ip = request.query_params.get("ip")
        if not ip:
            if request.user.is_authenticated:
                return Response(
                    "ip parameter is required for authenticated users.",
                    status=status.HTTP_400_BAD_REQUEST)
            ip = request.user.ip
        if not ip:
            return Response("Missing ip.", status=status.HTTP_400_BAD_REQUEST)

        # Per-row delete() so each job's _cleanup_files() runs (it is now
        # idempotent to already-missing files, #216).
        for job in AnonymousJob.objects.filter(ip=ip):
            job.delete()

        return Response(status=status.HTTP_204_NO_CONTENT)
