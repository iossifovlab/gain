import zoneinfo
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from web_annotation.models import (
    AnonymousUserQuota,
    MonthlyQuotaRefreshLog,
    SessionQuota,
    UserQuota,
)


class Command(BaseCommand):
    """Management command to reset all monthly quotas."""

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--force",
            action="store_true",
            help="Run even if already executed this month.",
        )

    def handle(self, *_args: Any, **options: Any) -> None:
        tz = zoneinfo.ZoneInfo(settings.QUOTA_RESET_TIMEZONE)
        month_start = timezone.now().astimezone(tz).replace(
            day=1, hour=0, minute=0, second=0, microsecond=0)
        already_ran = MonthlyQuotaRefreshLog.objects.filter(
            executed_at__gte=month_start).exists()

        if already_ran and not options["force"]:
            self.stdout.write(
                "Monthly quota refresh already ran this month. "
                "Use --force to override.")
            return

        # One transaction for the whole refresh, deliberately -- see the note
        # in ``refreshdaily`` (gain#768, gain#807).
        with transaction.atomic():
            UserQuota.refresh_all_monthly()
            AnonymousUserQuota.refresh_all_monthly()
            SessionQuota.refresh_all_monthly()
            MonthlyQuotaRefreshLog.objects.create()

        self.stdout.write("Monthly quota refresh complete.")
