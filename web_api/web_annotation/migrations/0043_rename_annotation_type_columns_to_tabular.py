"""Rename the stored ``Job.annotation_type`` value 'columns' -> 'tabular'.

Issue #29 (deferred from #25). The application now writes and compares
against ``"tabular"``; this data migration rewrites legacy rows in both the
``Job`` and ``AnonymousJob`` tables so existing deployments keep working.
The migration is reversible (``tabular`` -> ``columns``).
"""

from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.state import StateApps


def _columns_to_tabular(
    apps: StateApps,
    schema_editor: BaseDatabaseSchemaEditor,
) -> None:
    job = apps.get_model("web_annotation", "Job")
    job.objects.filter(annotation_type="columns").update(
        annotation_type="tabular")
    anon_job = apps.get_model("web_annotation", "AnonymousJob")
    anon_job.objects.filter(annotation_type="columns").update(
        annotation_type="tabular")


def _tabular_to_columns(
    apps: StateApps,
    schema_editor: BaseDatabaseSchemaEditor,
) -> None:
    job = apps.get_model("web_annotation", "Job")
    job.objects.filter(annotation_type="tabular").update(
        annotation_type="columns")
    anon_job = apps.get_model("web_annotation", "AnonymousJob")
    anon_job.objects.filter(annotation_type="tabular").update(
        annotation_type="columns")


class Migration(migrations.Migration):

    dependencies = [
        ("web_annotation", "0042_alter_anonymoususerquota_ip_alter_userquota_user"),  # noqa: E501
    ]

    operations = [
        migrations.RunPython(_columns_to_tabular, _tabular_to_columns),
    ]
