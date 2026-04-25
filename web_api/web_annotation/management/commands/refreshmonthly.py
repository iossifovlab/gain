from typing import Any
from django.core.management.base import BaseCommand
from web_annotation.models import UserQuota, AnonymousUserQuota


class Command(BaseCommand):
    """Management command to reset all monthly quotas."""
    def handle(self, *args: Any, **options: Any) -> None:
        for user_quota in UserQuota.objects.all():
            user_quota.reset_monthly()
            user_quota.save()

        for anonymous_quota in AnonymousUserQuota.objects.all():
            anonymous_quota.reset_monthly()
            anonymous_quota.save()
