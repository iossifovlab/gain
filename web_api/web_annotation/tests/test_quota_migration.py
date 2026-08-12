# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""Tests for the quota data migrations, driven through the executor.

Two migrations are covered: 0042's dedup of duplicate quota rows, and 0044's
conversion of the period counters from stored *remaining* units to stored
*consumed* ones (gain#750). Both rewind the schema, insert rows through the
historical models of the pre-migration state, migrate forward, and assert on
what the migration left behind.

## 0042 -- the dedup

The pre-fix ``get_or_create`` race could leave duplicate ``UserQuota`` /
``AnonymousUserQuota`` rows in existing deployments. Migration 0042 runs a
``RunPython`` dedup step (``_dedup_user_quotas``) that keeps the newest row
per user / per ip and deletes the rest before the unique constraints land.

It rewinds the schema to 0041 (pre-fix), inserts duplicates using the
historical models from that state, migrates forward to 0042, and asserts
that exactly one (the newest) row survives per user / per ip and that the
unique constraints are now enforced.
"""
from collections.abc import Generator

import pytest
from django.conf import settings
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.db.models import Max

APP = "web_annotation"
BEFORE = "0041_add_session_quota"
AFTER = "0042_alter_anonymoususerquota_ip_alter_userquota_user"

#: The 0044 conversion of stored remaining units into stored consumed ones.
CONSUMED_BEFORE = "0043_rename_annotation_type_columns_to_tabular"
CONSUMED_AFTER = "0044_quota_counters_store_consumed_units"

#: Where a migration test leaves the schema. Every test here rewinds, so the
#: rest of the suite depends on this being the head migration.
LATEST = CONSUMED_AFTER


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
    _migrate(LATEST)


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


@pytest.mark.usefixtures("_reset_schema")
@pytest.mark.django_db(transaction=True)
def test_0044_converts_every_quota_type_from_remaining_to_consumed() -> None:
    """Each stored counter becomes ``limit - remaining`` for its own type.

    All three quota types are covered, and with different limits between
    them: converting a row against the wrong type's configuration is the
    mistake a single-type test would miss, and the user limits are ten times
    the anonymous ones, so it shows up as a wrong number rather than none.
    """
    executor = _migrate(CONSUMED_BEFORE)
    state = executor.loader.project_state((APP, CONSUMED_BEFORE))
    user_model = state.apps.get_model(APP, "User")
    user_quota = state.apps.get_model(APP, "UserQuota")
    anon_quota = state.apps.get_model(APP, "AnonymousUserQuota")
    session_quota = state.apps.get_model(APP, "SessionQuota")

    user = user_model.objects.create(username="mig-user", email="m@e.com")
    user_quota.objects.create(
        user_id=user.pk, daily_jobs=40, monthly_variants=3_000_000,
        extra_variants=500)
    anon_quota.objects.create(ip="10.0.1.1", daily_jobs=4)
    session_quota.objects.create(session_id="mig-session", daily_jobs=6)

    after = _migrate(CONSUMED_AFTER)
    after_state = after.loader.project_state((APP, CONSUMED_AFTER))
    user_row = after_state.apps.get_model(APP, "UserQuota").objects.get(
        user_id=user.pk)
    anon_row = after_state.apps.get_model(
        APP, "AnonymousUserQuota").objects.get(ip="10.0.1.1")
    session_row = after_state.apps.get_model(APP, "SessionQuota").objects.get(
        session_id="mig-session")

    user_limits = settings.QUERY_QUOTAS["user"]
    anon_limits = settings.QUERY_QUOTAS["anonymous"]
    assert user_row.daily_jobs == user_limits["daily_jobs"] - 40
    assert user_row.monthly_variants == \
        user_limits["monthly_variants"] - 3_000_000
    assert anon_row.daily_jobs == anon_limits["daily_jobs"] - 4
    # A session quota is configured as an anonymous one, not as a user one.
    assert session_row.daily_jobs == anon_limits["daily_jobs"] - 6

    # The extras store units remaining and keep doing so: converting them
    # would spend a granted balance the moment the migration ran.
    assert user_row.extra_variants == 500


@pytest.mark.usefixtures("_reset_schema")
@pytest.mark.django_db(transaction=True)
def test_0044_clamps_a_row_holding_more_than_its_current_limit() -> None:
    """A row left above its limit by a *lowered* limit converts to zero.

    ``limit - remaining`` goes negative there, and a negative consumption
    would read as headroom beyond the limit -- the opposite of what lowering
    a limit is for. Under live limits such a user has consumed nothing
    against the new one, so zero is the consistent answer as well as the
    safe one.
    """
    executor = _migrate(CONSUMED_BEFORE)
    state = executor.loader.project_state((APP, CONSUMED_BEFORE))
    anon_quota = state.apps.get_model(APP, "AnonymousUserQuota")
    above_limit = settings.QUERY_QUOTAS["anonymous"]["daily_jobs"] + 1
    anon_quota.objects.create(ip="10.0.1.2", daily_jobs=above_limit)

    after = _migrate(CONSUMED_AFTER)
    after_state = after.loader.project_state((APP, CONSUMED_AFTER))
    row = after_state.apps.get_model(APP, "AnonymousUserQuota").objects.get(
        ip="10.0.1.2")

    assert row.daily_jobs == 0
