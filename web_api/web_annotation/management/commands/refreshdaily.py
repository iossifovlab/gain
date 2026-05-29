from typing import Any

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from web_annotation.models import (
    AnonymousUserQuota,
    DailyQuotaRefreshLog,
    SessionQuota,
    UserQuota,
)


class Command(BaseCommand):
    """Management command to reset all daily quotas."""

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--force",
            action="store_true",
            help="Run even if already executed today.",
        )

    def handle(self, *_args: Any, **options: Any) -> None:
        today_start = timezone.now().replace(
            hour=0, minute=0, second=0, microsecond=0)
        already_ran = DailyQuotaRefreshLog.objects.filter(
            executed_at__gte=today_start).exists()

        if already_ran and not options["force"]:
            self.stdout.write(
                "Daily quota refresh already "
                "ran today. Use --force to override.",
            )
            return

        with transaction.atomic():
            for user_quota in UserQuota.objects.all():
                user_quota.reset_daily()
                user_quota.save()

            for anonymous_quota in AnonymousUserQuota.objects.all():
                anonymous_quota.reset_daily()
                anonymous_quota.save()

            for session_quota in SessionQuota.objects.all():
                session_quota.reset_daily()
                session_quota.save()

            DailyQuotaRefreshLog.objects.create()

        self.stdout.write("Daily quota refresh complete.")
