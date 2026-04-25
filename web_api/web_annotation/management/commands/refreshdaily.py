from typing import Any

from django.core.management.base import BaseCommand

from web_annotation.models import AnonymousUserQuota, UserQuota


class Command(BaseCommand):
    """Management command to reset all daily quotas."""
    def handle(self, *_args: Any, **_options: Any) -> None:
        for user_quota in UserQuota.objects.all():
            user_quota.reset_daily()
            user_quota.save()

        for anonymous_quota in AnonymousUserQuota.objects.all():
            anonymous_quota.reset_daily()
            anonymous_quota.save()
