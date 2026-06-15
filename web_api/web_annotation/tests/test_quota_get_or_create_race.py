# pylint: disable=W0621,C0114,C0116,W0212,W0613
import pytest
from django.db import IntegrityError, transaction

from web_annotation.models import (
    AnonymousUserQuota,
    User,
    UserQuota,
    WebAnnotationAnonymousUser,
)


def test_user_quota_user_is_unique() -> None:
    """A second UserQuota for the same user must be rejected by the DB.

    Without a uniqueness guarantee on ``UserQuota.user`` the
    ``get_or_create`` in ``User._get_or_create_user_quota`` races and
    inserts duplicate rows, which later makes ``get_quota`` raise
    ``MultipleObjectsReturned``.
    """
    user = User.objects.get(email="user@example.com")
    UserQuota.objects.create(user=user)
    with pytest.raises(IntegrityError), transaction.atomic():
        UserQuota.objects.create(user=user)


def test_get_quota_resilient_to_existing_quota_rows() -> None:
    """``get_quota`` must not raise even if a quota row already exists.

    Simulates the outcome of a race that already happened: a quota row is
    present, and a subsequent request must reuse it rather than blow up.
    """
    user = User.objects.get(email="user@example.com")
    UserQuota.objects.create(user=user)

    # Must not raise MultipleObjectsReturned (or anything else).
    snapshot = user.get_quota()
    assert snapshot is not None
    assert UserQuota.objects.filter(user=user).count() == 1


def test_anonymous_ip_quota_is_unique() -> None:
    """A second AnonymousUserQuota for the same IP must be DB-rejected."""
    AnonymousUserQuota.objects.create(ip="10.0.0.1")
    with pytest.raises(IntegrityError), transaction.atomic():
        AnonymousUserQuota.objects.create(ip="10.0.0.1")


def test_anonymous_get_quota_resilient_to_existing_rows() -> None:
    """Anonymous ``get_quota`` must not raise when quota rows exist."""
    anon = WebAnnotationAnonymousUser(session_id="sess-1", ip="10.0.0.2")
    AnonymousUserQuota.objects.create(ip="10.0.0.2")

    snapshot = anon.get_quota()
    assert snapshot is not None
    assert AnonymousUserQuota.objects.filter(ip="10.0.0.2").count() == 1
