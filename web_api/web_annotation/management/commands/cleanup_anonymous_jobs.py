import datetime
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from web_annotation.models import AnonymousJob

#: Fallback TTL (hours) when neither the CLI arg nor a parseable setting is
#: given. Mirrors the ANONYMOUS_JOB_TTL_HOURS default in settings_default.py.
DEFAULT_TTL_HOURS = 24


def resolve_ttl_hours(raw: object) -> int:
    """Coerce a TTL-hours value to an int, falling back to the default.

    A garbage ``GPFWA_ANONYMOUS_JOB_TTL_HOURS`` env var is already coerced to
    a safe int at settings-import time so app boot survives it. This second
    guard keeps the janitor robust even if a non-integer setting reaches it
    (e.g. a settings override in a test or a future config path): a bad value
    falls back to ``DEFAULT_TTL_HOURS`` instead of crashing the command.
    """
    try:
        return int(str(raw))
    except (TypeError, ValueError):
        return DEFAULT_TTL_HOURS


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
            ttl_hours = resolve_ttl_hours(settings.ANONYMOUS_JOB_TTL_HOURS)

        # Reject a negative TTL loudly, before any deletion. cutoff would be
        # now - (-N) = now + N, so created__lt=cutoff would match every
        # terminal job regardless of age -- a catastrophic operator footgun.
        # 0 is allowed and means "flush all currently-terminal jobs now".
        if ttl_hours < 0:
            raise CommandError(
                f"--older-than-hours must be >= 0, got {ttl_hours}.",
            )

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
        failed = 0
        for job in jobs:
            # job.delete() runs _cleanup_files() to remove the on-disk inputs,
            # config and result before the row is dropped. Isolate each row:
            # one job that raises (e.g. a permission error on its file) must
            # never abort the loop and block reaping the rest -- that would
            # wedge the janitor on every subsequent run (#216).
            try:
                job.delete()
                deleted += 1
            except Exception as exc:  # noqa: BLE001  pylint: disable=broad-except
                failed += 1
                self.stderr.write(
                    f"Failed to delete anonymous job {job.pk}: {exc}",
                )

        self.stdout.write(
            f"Deleted {deleted} anonymous job(s) older than "
            f"{ttl_hours} hour(s); {failed} failed.",
        )

        # Signal failure only after every selected row has been attempted, so
        # a poison row never blocks reaping the healthy ones but cron alerting
        # keyed on exit code still fires. Django turns a CommandError into a
        # non-zero exit written to stderr.
        if failed:
            raise CommandError(
                f"{failed} anonymous job(s) failed to delete "
                f"(deleted {deleted}).",
            )
