from typing import Any
from django.core.management.base import BaseCommand, CommandError
from web_annotation.models import User
import argparse


class Command(BaseCommand):
    """Management command to add units to a user's quota."""
    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "user_id",
            type=int,
            help="User to add units to",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        user_id = options["user_id"]
        try:
            user = User.objects.get(id=user_id)
        except User.DoesNotExist as ex:
            raise CommandError(
                f"User with id {user_id} does not exist") from ex

        user.get_quota().add_units()
