import zoneinfo
from typing import Any

from django.conf import settings
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
        tz = zoneinfo.ZoneInfo(settings.QUOTA_RESET_TIMEZONE)
        today_start = timezone.now().astimezone(tz).replace(
            hour=0, minute=0, second=0, microsecond=0)
        already_ran = DailyQuotaRefreshLog.objects.filter(
            executed_at__gte=today_start).exists()

        if already_ran and not options["force"]:
            self.stdout.write(
                "Daily quota refresh already "
                "ran today. Use --force to override.",
            )
            return

        # One transaction for the whole refresh, deliberately (gain#768).
        #
        # The table updates and the log row below must commit together: were
        # they to commit separately, a crash partway through would leave
        # tables refreshed with no log row, so the next run -- cron, or an
        # admin on the reset-quota page -- refreshes them a second time and
        # discards everything consumed in between.
        # `rolls_back_all_changes_on_failure` pins that. Each table is
        # refreshed by a single UPDATE (gain#807), so the rows stay locked
        # only for the remaining tables' UPDATEs and the log insert, not for
        # a walk over every row.
        #
        # Note the already-ran guard above is read outside this transaction,
        # so it is not serialised by it.
        with transaction.atomic():
            UserQuota.refresh_all_daily()
            AnonymousUserQuota.refresh_all_daily()
            SessionQuota.refresh_all_daily()
            DailyQuotaRefreshLog.objects.create()

        self.stdout.write("Daily quota refresh complete.")
