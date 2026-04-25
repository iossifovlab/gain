import argparse
import csv
from typing import Any

from django.core.management.base import BaseCommand

from web_annotation.models import AnonymousUserQuota, UserQuota


FIELDS = [
    "daily_jobs",
    "monthly_jobs",
    "extra_jobs",
    "daily_allele_queries",
    "monthly_allele_queries",
    "extra_allele_queries",
    "daily_variants",
    "monthly_variants",
    "extra_variants",
    "daily_attributes",
    "monthly_attributes",
    "extra_attributes",
    "last_daily_reset",
    "last_monthly_reset",
]

HEADER = ["type", "id", "email"] + FIELDS


class Command(BaseCommand):
    """Management command to export quota data for all users to CSV."""

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "output",
            nargs="?",
            default="-",
            help="Output file path. Defaults to stdout (-).",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        output_path = options["output"]
        if output_path == "-":
            self._write(self.stdout)
        else:
            with open(output_path, "w", newline="", encoding="utf-8") as f:
                self._write(f)

    def _write(self, stream: Any) -> None:
        writer = csv.writer(stream)
        writer.writerow(HEADER)

        for quota in UserQuota.objects.select_related("user").all():
            writer.writerow(
                ["user", quota.user.pk, quota.user.email]
                + [getattr(quota, f) for f in FIELDS],
            )

        for quota in AnonymousUserQuota.objects.all():
            writer.writerow(
                ["anonymous", quota.ip, ""]
                + [getattr(quota, f) for f in FIELDS],
            )
