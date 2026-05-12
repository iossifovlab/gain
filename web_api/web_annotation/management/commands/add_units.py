import argparse
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from web_annotation.models import User


class Command(BaseCommand):
    """Management command to add units to a user's quota."""
    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "email",
            type=str,
            help="Email of the user to add units to",
        )

    def handle(self, *_args: Any, **options: Any) -> None:
        email = options["email"]
        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist as ex:
            raise CommandError(
                f"User with email {email} does not exist") from ex

        user.quota_add_units()
