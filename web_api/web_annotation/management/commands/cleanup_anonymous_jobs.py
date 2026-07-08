import datetime
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from web_annotation.models import AnonymousJob


class Command(BaseCommand):
    """Delete anonymous jobs whose files have outlived their TTL.

    Anonymous jobs are no longer reaped when the last WebSocket disconnects
    (iossifovlab/gain#216); this age-based janitor bounds their result-file
    lifetime instead. Schedule it periodically (cron) in a deployment.
    """

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--older-than-hours",
            type=int,
            default=None,
            help=(
                "Delete terminal anonymous jobs created more than this many "
                "hours ago. Defaults to the ANONYMOUS_JOB_TTL_HOURS setting."
            ),
        )

    def handle(self, *_args: Any, **options: Any) -> None:
        ttl_hours = options["older_than_hours"]
        if ttl_hours is None:
            ttl_hours = settings.ANONYMOUS_JOB_TTL_HOURS
        cutoff = timezone.now() - datetime.timedelta(hours=ttl_hours)

        # Never delete a job that is still WAITING or IN_PROGRESS regardless of
        # age: unlinking result-<name>.vcf out from under a running annotation
        # task makes its on_success stat fail with [Errno 2] (gain#147).
        jobs = AnonymousJob.objects.filter(
            created__lt=cutoff,
        ).exclude(
            status__in=AnonymousJob.ACTIVE_STATUSES,
        )

        deleted = 0
        for job in jobs:
            # job.delete() runs _cleanup_files() to remove the on-disk inputs,
            # config and result before the row is dropped.
            job.delete()
            deleted += 1

        self.stdout.write(
            f"Deleted {deleted} anonymous job(s) older than "
            f"{ttl_hours} hour(s).",
        )
