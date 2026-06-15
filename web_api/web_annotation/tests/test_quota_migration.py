# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""Regression test for the 0042 quota-dedup migration.

The pre-fix ``get_or_create`` race could leave duplicate ``UserQuota`` /
``AnonymousUserQuota`` rows in existing deployments. Migration 0042 runs a
``RunPython`` dedup step (``_dedup_user_quotas``) that keeps the newest row
per user / per ip and deletes the rest before the unique constraints land.

This test drives the migration with Django's ``MigrationExecutor``: it
rewinds the schema to 0041 (pre-fix), inserts duplicates using the
historical models from that state, migrates forward to 0042, and asserts
that exactly one (the newest) row survives per user / per ip and that the
unique constraints are now enforced.
"""
from collections.abc import Generator

import pytest
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.db.models import Max

APP = "web_annotation"
BEFORE = "0041_add_session_quota"
AFTER = "0042_alter_anonymoususerquota_ip_alter_userquota_user"


def _migrate(target: str) -> MigrationExecutor:
    """Migrate the schema to ``target`` and return a fresh executor."""
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate([(APP, target)])
    # Rebuild so a follow-up migrate() sees the new applied-state.
    executor.loader.build_graph()
    return executor


@pytest.fixture
def _reset_schema() -> Generator[None, None, None]:
    """Leave the DB on the latest migration after the test runs."""
    yield
    _migrate(AFTER)


@pytest.mark.usefixtures("_reset_schema")
@pytest.mark.django_db(transaction=True)
def test_0042_dedups_duplicate_quota_rows() -> None:
    # Rewind to the pre-fix state (no unique constraints).
    executor = _migrate(BEFORE)
    state = executor.loader.project_state((APP, BEFORE))
    user_model = state.apps.get_model(APP, "User")
    user_quota = state.apps.get_model(APP, "UserQuota")
    anon_quota = state.apps.get_model(APP, "AnonymousUserQuota")

    # One user with three duplicate quota rows; daily_jobs differs so we can
    # identify which one is meant to survive (the newest id).
    user = user_model.objects.create(username="dup-user", email="d@e.com")
    user_quota.objects.create(user_id=user.pk, daily_jobs=1)
    user_quota.objects.create(user_id=user.pk, daily_jobs=2)
    newest_user_quota = user_quota.objects.create(
        user_id=user.pk, daily_jobs=3,
    )

    # Sanity: duplicates really do exist in the 0041 state.
    assert user_quota.objects.filter(user_id=user.pk).count() == 3

    # One ip with three duplicate anonymous-quota rows.
    anon_quota.objects.create(ip="10.0.0.99", daily_jobs=1)
    anon_quota.objects.create(ip="10.0.0.99", daily_jobs=2)
    newest_anon_quota = anon_quota.objects.create(
        ip="10.0.0.99", daily_jobs=3,
    )
    assert anon_quota.objects.filter(ip="10.0.0.99").count() == 3

    # Migrate forward across the dedup + AlterField operations.
    after = _migrate(AFTER)
    after_state = after.loader.project_state((APP, AFTER))
    user_quota = after_state.apps.get_model(APP, "UserQuota")
    anon_quota = after_state.apps.get_model(APP, "AnonymousUserQuota")

    # Exactly one row survives per user / per ip ...
    user_rows = user_quota.objects.filter(user_id=user.pk)
    assert user_rows.count() == 1
    anon_rows = anon_quota.objects.filter(ip="10.0.0.99")
    assert anon_rows.count() == 1

    # ... and it is the newest one (Max id).
    assert user_rows.first().pk == newest_user_quota.pk
    assert user_quota.objects.aggregate(m=Max("id"))["m"] \
        == newest_user_quota.pk
    assert user_rows.first().daily_jobs == 3

    assert anon_rows.first().pk == newest_anon_quota.pk
    assert anon_rows.first().daily_jobs == 3

    # The unique constraints are now enforced.
    with pytest.raises(IntegrityError), transaction.atomic():
        user_quota.objects.create(user_id=user.pk, daily_jobs=4)
    with pytest.raises(IntegrityError), transaction.atomic():
        anon_quota.objects.create(ip="10.0.0.99", daily_jobs=4)
