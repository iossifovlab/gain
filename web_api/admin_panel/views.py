from django.core.management import call_command
from django.http import HttpRequest, HttpResponse


def reset_daily_quota(_request: HttpRequest) -> HttpResponse:
    """Trigger a daily quota reset for all users."""
    call_command("refreshdaily")
    return HttpResponse(status=204)


def reset_monthly_quota(_request: HttpRequest) -> HttpResponse:
    """Trigger a monthly quota reset for all users."""
    call_command("refreshmonthly")
    return HttpResponse(status=204)
