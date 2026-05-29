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

        with transaction.atomic():
            for user_quota in UserQuota.objects.all():
                user_quota.reset_monthly()
                user_quota.save()

            for anonymous_quota in AnonymousUserQuota.objects.all():
                anonymous_quota.reset_monthly()
                anonymous_quota.save()

            for session_quota in SessionQuota.objects.all():
                session_quota.reset_monthly()
                session_quota.save()

            MonthlyQuotaRefreshLog.objects.create()

        self.stdout.write("Monthly quota refresh complete.")
