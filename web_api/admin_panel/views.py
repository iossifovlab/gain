import dataclasses

from django.core.management import call_command
from django.http import HttpRequest, HttpResponse, JsonResponse

from web_annotation.models import QuotaSnapshot, User, UserQuota

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


def reset_daily_quota(_request: HttpRequest) -> HttpResponse:
    """Trigger a daily quota reset for all users."""
    call_command("refreshdaily")
    return HttpResponse(status=204)


def reset_monthly_quota(_request: HttpRequest) -> HttpResponse:
    """Trigger a monthly quota reset for all users."""
    call_command("refreshmonthly")
    return HttpResponse(status=204)


def _get_or_create_user_quota(user: User) -> UserQuota:
    try:
        return UserQuota.objects.get(user=user)
    except UserQuota.DoesNotExist:
        quota = UserQuota(user=user)
        quota.reset_daily()
        quota.reset_monthly()
        return quota


def _quota_snapshot_response(quota: UserQuota) -> JsonResponse:
    snapshot = QuotaSnapshot.from_quota(quota)
    return JsonResponse(dataclasses.asdict(snapshot))


def set_extra_quota(request: HttpRequest) -> HttpResponse:
    """Set extra quota units of a given type for a user."""
    user_email = request.GET.get("user_email")
    quota_type = request.GET.get("quota_type")
    amount = request.GET.get("amount")

    if not user_email or not quota_type or amount is None:
        return HttpResponse("Missing required parameters.", status=400)
    if quota_type not in _EXTRA_QUOTA_FIELDS:
        valid = ", ".join(_EXTRA_QUOTA_FIELDS)
        return HttpResponse(
            f"Invalid quota_type. Valid types: {valid}.", status=400)
    try:
        amount_int = int(amount)
    except ValueError:
        return HttpResponse("amount must be an integer.", status=400)

    try:
        user = User.objects.get(email=user_email)
    except User.DoesNotExist:
        return HttpResponse(f"User '{user_email}' not found.", status=404)

    quota = _get_or_create_user_quota(user)
    setattr(quota, _EXTRA_QUOTA_FIELDS[quota_type], amount_int)
    quota.save()

    return _quota_snapshot_response(quota)


def set_current_quota(request: HttpRequest) -> HttpResponse:
    """Set a specific current (non-extra) quota field for a user."""
    user_email = request.GET.get("user_email")
    quota_type = request.GET.get("quota_type")
    amount = request.GET.get("amount")

    if not user_email or not quota_type or amount is None:
        return HttpResponse("Missing required parameters.", status=400)
    if quota_type not in _CURRENT_QUOTA_FIELDS:
        valid = ", ".join(sorted(_CURRENT_QUOTA_FIELDS))
        return HttpResponse(
            f"Invalid quota_type. Valid types: {valid}.", status=400)
    try:
        amount_int = int(amount)
    except ValueError:
        return HttpResponse("amount must be an integer.", status=400)

    try:
        user = User.objects.get(email=user_email)
    except User.DoesNotExist:
        return HttpResponse(f"User '{user_email}' not found.", status=404)

    quota = _get_or_create_user_quota(user)
    setattr(quota, quota_type, amount_int)
    quota.save()

    return _quota_snapshot_response(quota)
