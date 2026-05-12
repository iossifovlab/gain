import argparse
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from web_annotation.models import User


class Command(BaseCommand):
    """Management command to create a regular user."""
    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "email",
            type=str,
            help="Email address for the new user",
        )
        parser.add_argument(
            "password",
            type=str,
            help="Password for the new user",
        )

    def handle(self, *_args: Any, **options: Any) -> None:
        email = options["email"]
        password = options["password"]
        if User.objects.filter(email=email).exists():
            raise CommandError(
                f"User with email {email} already exists")
        User.objects.create_user(email, email, password)
