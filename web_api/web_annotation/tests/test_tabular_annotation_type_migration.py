# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""Regression test for the 0043 annotation_type rename migration.

Issue #29 renames the stored ``Job.annotation_type`` value from the legacy
``"columns"`` string to ``"tabular"``. Migration 0043 runs a ``RunPython``
data step that rewrites every legacy row in both the ``Job`` and
``AnonymousJob`` tables, and is reversible (``tabular`` -> ``columns``).

This test drives the migration with Django's ``MigrationExecutor``: it
rewinds to 0042, inserts legacy ``columns`` rows using the historical
models, migrates forward to 0043 and asserts the values are now
``tabular``, then migrates back to 0042 and asserts the reverse rewrite.
"""
from collections.abc import Generator

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

APP = "web_annotation"
BEFORE = "0042_alter_anonymoususerquota_ip_alter_userquota_user"
AFTER = "0043_rename_annotation_type_columns_to_tabular"


def _migrate(target: str) -> MigrationExecutor:
    """Migrate the schema to ``target`` and return a fresh executor."""
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate([(APP, target)])
    executor.loader.build_graph()
    return executor


@pytest.fixture
def _reset_schema() -> Generator[None, None, None]:
    """Leave the DB on the latest migration after the test runs."""
    yield
    _migrate(AFTER)


@pytest.mark.usefixtures("_reset_schema")
@pytest.mark.django_db(transaction=True)
def test_0043_rewrites_columns_to_tabular() -> None:
    executor = _migrate(BEFORE)
    state = executor.loader.project_state((APP, BEFORE))
    user_model = state.apps.get_model(APP, "User")
    job_model = state.apps.get_model(APP, "Job")
    anon_job_model = state.apps.get_model(APP, "AnonymousJob")

    user = user_model.objects.create(username="mig-user", email="m@e.com")

    legacy_job = job_model.objects.create(
        input_path="in", config_path="cfg", result_path="res",
        owner_id=user.pk, annotation_type="columns",
    )
    vcf_job = job_model.objects.create(
        input_path="in", config_path="cfg", result_path="res",
        owner_id=user.pk, annotation_type="vcf",
    )
    legacy_anon = anon_job_model.objects.create(
        input_path="in", config_path="cfg", result_path="res",
        owner="anon", annotation_type="columns",
    )
    vcf_anon = anon_job_model.objects.create(
        input_path="in", config_path="cfg", result_path="res",
        owner="anon", annotation_type="vcf",
    )

    # Migrate forward: columns -> tabular, vcf untouched.
    after = _migrate(AFTER)
    after_state = after.loader.project_state((APP, AFTER))
    job_model = after_state.apps.get_model(APP, "Job")
    anon_job_model = after_state.apps.get_model(APP, "AnonymousJob")

    assert job_model.objects.get(pk=legacy_job.pk).annotation_type \
        == "tabular"
    assert job_model.objects.get(pk=vcf_job.pk).annotation_type == "vcf"
    assert anon_job_model.objects.get(pk=legacy_anon.pk).annotation_type \
        == "tabular"
    assert anon_job_model.objects.get(pk=vcf_anon.pk).annotation_type == "vcf"

    # Migrate backward: tabular -> columns (reversible).
    back = _migrate(BEFORE)
    back_state = back.loader.project_state((APP, BEFORE))
    job_model = back_state.apps.get_model(APP, "Job")
    anon_job_model = back_state.apps.get_model(APP, "AnonymousJob")

    assert job_model.objects.get(pk=legacy_job.pk).annotation_type \
        == "columns"
    assert anon_job_model.objects.get(pk=legacy_anon.pk).annotation_type \
        == "columns"
