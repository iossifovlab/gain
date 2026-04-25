# pylint: disable=W0621,C0114,C0116,W0212,W0613
import csv
import io
import pathlib

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from web_annotation.management.commands.export_quotas import HEADER
from web_annotation.models import AnonymousUserQuota, User, UserQuota


@pytest.fixture
def user_quota() -> UserQuota:
    user = User.objects.get(email="user@example.com")
    quota = UserQuota(user=user)
    quota.reset_daily()
    quota.reset_monthly()
    return quota


@pytest.fixture
def anonymous_quota() -> AnonymousUserQuota:
    quota = AnonymousUserQuota(ip="127.0.0.1")
    quota.reset_daily()
    quota.reset_monthly()
    return quota


# --- add_units command ---

def test_add_units_command_adds_units_to_user_quota(
    user_quota: UserQuota,
) -> None:
    user = User.objects.get(email="user@example.com")
    before = user_quota.extra_jobs

    call_command("add_units", user.pk)

    user_quota.refresh_from_db()
    assert user_quota.extra_jobs == before + user_quota.get_monthly_job_max()


def test_add_units_command_raises_for_nonexistent_user() -> None:
    with pytest.raises(CommandError, match="does not exist"):
        call_command("add_units", 99999)


# --- refreshdaily command ---

def test_refreshdaily_resets_user_quota_daily_fields(
    user_quota: UserQuota,
) -> None:
    user_quota.daily_jobs = 0
    user_quota.daily_variants = 0
    user_quota.save()

    call_command("refreshdaily")

    user_quota.refresh_from_db()
    assert user_quota.daily_jobs == user_quota.get_daily_job_max()
    assert user_quota.daily_variants == user_quota.get_daily_variant_max()


def test_refreshdaily_resets_anonymous_quota_daily_fields(
    anonymous_quota: AnonymousUserQuota,
) -> None:
    anonymous_quota.daily_jobs = 0
    anonymous_quota.daily_variants = 0
    anonymous_quota.save()

    call_command("refreshdaily")

    anonymous_quota.refresh_from_db()
    assert anonymous_quota.daily_jobs == anonymous_quota.get_daily_job_max()
    assert (
        anonymous_quota.daily_variants
        == anonymous_quota.get_daily_variant_max()
    )


def test_refreshdaily_does_not_reset_monthly_fields(
    user_quota: UserQuota,
) -> None:
    user_quota.monthly_jobs = 0
    user_quota.save()

    call_command("refreshdaily")

    user_quota.refresh_from_db()
    assert user_quota.monthly_jobs == 0


# --- refreshmonthly command ---

def test_refreshmonthly_resets_user_quota_monthly_fields(
    user_quota: UserQuota,
) -> None:
    user_quota.monthly_jobs = 0
    user_quota.monthly_variants = 0
    user_quota.save()

    call_command("refreshmonthly")

    user_quota.refresh_from_db()
    assert user_quota.monthly_jobs == user_quota.get_monthly_job_max()
    assert user_quota.monthly_variants == user_quota.get_monthly_variant_max()


def test_refreshmonthly_resets_anonymous_quota_monthly_fields(
    anonymous_quota: AnonymousUserQuota,
) -> None:
    anonymous_quota.monthly_jobs = 0
    anonymous_quota.monthly_variants = 0
    anonymous_quota.save()

    call_command("refreshmonthly")

    anonymous_quota.refresh_from_db()
    assert anonymous_quota.monthly_jobs == anonymous_quota.get_monthly_job_max()
    assert anonymous_quota.monthly_variants == \
        anonymous_quota.get_monthly_variant_max()


def test_refreshmonthly_does_not_reset_daily_fields(
    user_quota: UserQuota,
) -> None:
    user_quota.daily_jobs = 0
    user_quota.save()

    call_command("refreshmonthly")

    user_quota.refresh_from_db()
    assert user_quota.daily_jobs == 0


# --- export_quotas command ---

def _run_export() -> list[dict[str, str]]:
    buf = io.StringIO()
    call_command("export_quotas", stdout=buf)
    buf.seek(0)
    return list(csv.DictReader(buf))


def test_export_quotas_writes_correct_header(
    user_quota: UserQuota,
) -> None:
    buf = io.StringIO()
    call_command("export_quotas", stdout=buf)
    buf.seek(0)
    actual_header = next(csv.reader(buf))
    assert actual_header == HEADER


def test_export_quotas_includes_user_row(user_quota: UserQuota) -> None:
    rows = _run_export()
    user = User.objects.get(email="user@example.com")
    user_rows = [r for r in rows if r["type"] == "user"]
    assert any(
        r["id"] == str(user.pk) and r["email"] == user.email
        for r in user_rows
    )


def test_export_quotas_includes_anonymous_row(
    anonymous_quota: AnonymousUserQuota,
) -> None:
    rows = _run_export()
    anon_rows = [r for r in rows if r["type"] == "anonymous"]
    assert any(r["id"] == "127.0.0.1" and r["email"] == "" for r in anon_rows)


def test_export_quotas_user_row_quota_values(user_quota: UserQuota) -> None:
    rows = _run_export()
    user = User.objects.get(email="user@example.com")
    row = next(
        r for r in rows
        if r["type"] == "user" and r["id"] == str(user.pk)
    )
    assert int(row["daily_jobs"]) == user_quota.daily_jobs
    assert int(row["monthly_jobs"]) == user_quota.monthly_jobs
    assert int(row["daily_variants"]) == user_quota.daily_variants
    assert int(row["monthly_variants"]) == user_quota.monthly_variants


def test_export_quotas_anonymous_row_quota_values(
    anonymous_quota: AnonymousUserQuota,
) -> None:
    rows = _run_export()
    row = next(r for r in rows if r["id"] == "127.0.0.1")
    assert int(row["daily_jobs"]) == anonymous_quota.daily_jobs
    assert int(row["monthly_jobs"]) == anonymous_quota.monthly_jobs
    assert (
        int(row["extra_allele_queries"])
        == anonymous_quota.extra_allele_queries
    )


def test_export_quotas_writes_to_file(
    user_quota: UserQuota,
    tmp_path: pathlib.Path,
) -> None:
    output = tmp_path / "quotas.csv"
    call_command("export_quotas", str(output))
    rows = list(csv.DictReader(output.open()))
    assert any(r["type"] == "user" for r in rows)
