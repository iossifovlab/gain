import argparse
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from web_annotation.models import User


class Command(BaseCommand):
    """Management command to set or remove the unlimited flag on a user."""

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "email",
            type=str,
            help="Email of the user to update",
        )
        parser.add_argument(
            "--remove",
            action="store_true",
            help="Remove the unlimited flag instead of setting it.",
        )

    def handle(self, *_args: Any, **options: Any) -> None:
        email = options["email"]
        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist as ex:
            raise CommandError(
                f"User with email {email} does not exist") from ex

        user.is_unlimited = not options["remove"]
        user.save()
        state = "removed from" if options["remove"] else "set on"
        self.stdout.write(f"Unlimited flag {state} {email}.")
