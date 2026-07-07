# pylint: disable=W0621,C0114,C0116,W0212,W0613
import csv
import datetime
import io
import pathlib

import pytest
import pytest_mock
from django.conf import LazySettings
from django.core.management import call_command
from django.core.management.base import CommandError

from web_annotation.management.commands.export_quotas import HEADER
from web_annotation.models import (
    AnonymousUserQuota,
    DailyQuotaRefreshLog,
    MonthlyQuotaRefreshLog,
    SessionQuota,
    User,
    UserQuota,
)


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


@pytest.fixture
def session_quota() -> SessionQuota:
    quota = SessionQuota(session_id="test-session")
    quota.reset_daily()
    quota.reset_monthly()
    return quota


def test_create_user_creates_user_with_given_email() -> None:
    call_command("create_user", "new@example.com", "secret")
    assert User.objects.filter(email="new@example.com").exists()


def test_create_user_raises_for_duplicate_email() -> None:
    with pytest.raises(CommandError, match="already exists"):
        call_command("create_user", "user@example.com", "secret")


# --- add_units command ---

def test_add_units_command_adds_units_to_user_quota(
    user_quota: UserQuota,
) -> None:
    before = user_quota.extra_jobs

    call_command("add_units", "user@example.com")

    user_quota.refresh_from_db()
    assert user_quota.extra_jobs == before + user_quota.get_monthly_job_max()


def test_add_units_command_raises_for_nonexistent_user() -> None:
    with pytest.raises(CommandError, match="does not exist"):
        call_command("add_units", "nobody@example.com")


# --- set_unlimited command ---

def test_set_unlimited_sets_flag() -> None:
    user = User.objects.get(email="user@example.com")
    user.is_unlimited = False
    user.save()

    call_command("set_unlimited", "user@example.com")

    user.refresh_from_db()
    assert user.is_unlimited is True


def test_set_unlimited_remove_clears_flag() -> None:
    user = User.objects.get(email="user@example.com")
    user.is_unlimited = True
    user.save()

    call_command("set_unlimited", "user@example.com", "--remove")

    user.refresh_from_db()
    assert user.is_unlimited is False


def test_set_unlimited_raises_for_nonexistent_user() -> None:
    with pytest.raises(CommandError, match="does not exist"):
        call_command("set_unlimited", "nobody@example.com")


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


def test_refreshdaily_resets_session_quota_daily_fields(
    session_quota: SessionQuota,
) -> None:
    session_quota.daily_jobs = 0
    session_quota.daily_variants = 0
    session_quota.save()

    call_command("refreshdaily")

    session_quota.refresh_from_db()
    assert session_quota.daily_jobs == session_quota.get_daily_job_max()
    assert (
        session_quota.daily_variants
        == session_quota.get_daily_variant_max()
    )


def test_refreshdaily_does_not_reset_monthly_fields(
    user_quota: UserQuota,
) -> None:
    user_quota.monthly_jobs = 0
    user_quota.save()

    call_command("refreshdaily")

    user_quota.refresh_from_db()
    assert user_quota.monthly_jobs == 0


def test_refreshdaily_creates_log_entry(user_quota: UserQuota) -> None:
    assert DailyQuotaRefreshLog.objects.count() == 0
    call_command("refreshdaily")
    assert DailyQuotaRefreshLog.objects.count() == 1


def test_refreshdaily_skips_if_already_ran_today(
    user_quota: UserQuota,
) -> None:
    call_command("refreshdaily")
    user_quota.daily_jobs = 0
    user_quota.save()

    call_command("refreshdaily")

    user_quota.refresh_from_db()
    assert user_quota.daily_jobs == 0


def test_refreshdaily_force_runs_even_if_already_ran(
    user_quota: UserQuota,
) -> None:
    call_command("refreshdaily")
    user_quota.daily_jobs = 0
    user_quota.save()

    call_command("refreshdaily", "--force")

    user_quota.refresh_from_db()
    assert user_quota.daily_jobs == user_quota.get_daily_job_max()


def test_refreshdaily_uses_configured_timezone_for_day_boundary(
    user_quota: UserQuota,
    settings: LazySettings,
    mocker: pytest_mock.MockerFixture,
) -> None:
    """The already-ran guard counts a day in QUOTA_RESET_TIMEZONE.

    A zone behind UTC (New York): a refresh logged at 02:00 UTC is the
    previous New York day, so a run at 06:00 UTC -- a new NY day, but the
    same UTC calendar day -- must still reset.
    """
    settings.QUOTA_RESET_TIMEZONE = "America/New_York"
    utc = datetime.UTC
    DailyQuotaRefreshLog.objects.create(
        executed_at=datetime.datetime(2026, 1, 15, 2, 0, tzinfo=utc))
    user_quota.daily_jobs = 0
    user_quota.save()
    mocker.patch(
        "django.utils.timezone.now",
        return_value=datetime.datetime(2026, 1, 15, 6, 0, tzinfo=utc))

    call_command("refreshdaily")

    user_quota.refresh_from_db()
    assert user_quota.daily_jobs == user_quota.get_daily_job_max()


def test_refreshdaily_timezone_ahead_of_utc_stays_aligned(
    user_quota: UserQuota,
    settings: LazySettings,
    mocker: pytest_mock.MockerFixture,
) -> None:
    """A zone ahead of UTC (Tokyo) tracks the local day, not the UTC day.

    A refresh logged at 10:00 UTC (19:00 JST, Jan 15) must not block a run
    at 20:00 UTC (05:00 JST, Jan 16) -- a new Tokyo day, but still the same
    UTC calendar day. This is the case the old UTC-only guard got wrong.
    """
    settings.QUOTA_RESET_TIMEZONE = "Asia/Tokyo"
    utc = datetime.UTC
    DailyQuotaRefreshLog.objects.create(
        executed_at=datetime.datetime(2026, 1, 15, 10, 0, tzinfo=utc))
    user_quota.daily_jobs = 0
    user_quota.save()
    mocker.patch(
        "django.utils.timezone.now",
        return_value=datetime.datetime(2026, 1, 15, 20, 0, tzinfo=utc))

    call_command("refreshdaily")

    user_quota.refresh_from_db()
    assert user_quota.daily_jobs == user_quota.get_daily_job_max()


def test_refreshdaily_defaults_to_utc_day_boundary(
    user_quota: UserQuota,
    mocker: pytest_mock.MockerFixture,
) -> None:
    """With QUOTA_RESET_TIMEZONE unset, the guard stays a UTC day.

    Two runs within the same UTC calendar day: the second is skipped.
    """
    utc = datetime.UTC
    DailyQuotaRefreshLog.objects.create(
        executed_at=datetime.datetime(2026, 1, 15, 2, 0, tzinfo=utc))
    user_quota.daily_jobs = 0
    user_quota.save()
    mocker.patch(
        "django.utils.timezone.now",
        return_value=datetime.datetime(2026, 1, 15, 6, 0, tzinfo=utc))

    call_command("refreshdaily")

    user_quota.refresh_from_db()
    assert user_quota.daily_jobs == 0


def test_refreshdaily_rolls_back_all_changes_on_failure(
    user_quota: UserQuota,
    anonymous_quota: AnonymousUserQuota,
    mocker: pytest_mock.MockerFixture,
) -> None:
    """A failure partway through leaves no partial resets and no log row."""
    user_quota.daily_jobs = 0
    user_quota.save()
    mocker.patch.object(
        AnonymousUserQuota, "reset_daily",
        side_effect=RuntimeError("boom"))

    with pytest.raises(RuntimeError, match="boom"):
        call_command("refreshdaily")

    user_quota.refresh_from_db()
    assert user_quota.daily_jobs == 0  # rolled back, not refilled
    assert DailyQuotaRefreshLog.objects.count() == 0


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


def test_refreshmonthly_resets_session_quota_monthly_fields(
    session_quota: SessionQuota,
) -> None:
    session_quota.monthly_jobs = 0
    session_quota.monthly_variants = 0
    session_quota.save()

    call_command("refreshmonthly")

    session_quota.refresh_from_db()
    assert session_quota.monthly_jobs == session_quota.get_monthly_job_max()
    assert (
        session_quota.monthly_variants
        == session_quota.get_monthly_variant_max()
    )


def test_refreshmonthly_does_not_reset_daily_fields(
    user_quota: UserQuota,
) -> None:
    user_quota.daily_jobs = 0
    user_quota.save()

    call_command("refreshmonthly")

    user_quota.refresh_from_db()
    assert user_quota.daily_jobs == 0


def test_refreshmonthly_creates_log_entry(user_quota: UserQuota) -> None:
    assert MonthlyQuotaRefreshLog.objects.count() == 0
    call_command("refreshmonthly")
    assert MonthlyQuotaRefreshLog.objects.count() == 1


def test_refreshmonthly_skips_if_already_ran_this_month(
    user_quota: UserQuota,
) -> None:
    call_command("refreshmonthly")
    user_quota.monthly_jobs = 0
    user_quota.save()

    call_command("refreshmonthly")

    user_quota.refresh_from_db()
    assert user_quota.monthly_jobs == 0


def test_refreshmonthly_force_runs_even_if_already_ran(
    user_quota: UserQuota,
) -> None:
    call_command("refreshmonthly")
    user_quota.monthly_jobs = 0
    user_quota.save()

    call_command("refreshmonthly", "--force")

    user_quota.refresh_from_db()
    assert user_quota.monthly_jobs == user_quota.get_monthly_job_max()


def test_refreshmonthly_uses_configured_timezone_for_month_boundary(
    user_quota: UserQuota,
    settings: LazySettings,
    mocker: pytest_mock.MockerFixture,
) -> None:
    """The already-ran guard counts a month in QUOTA_RESET_TIMEZONE.

    A zone behind UTC (New York): a refresh logged at 02:00 UTC on the
    1st is still the previous New York month, so a run at 06:00 UTC -- a
    new NY month, but the same UTC calendar month -- must still reset.
    """
    settings.QUOTA_RESET_TIMEZONE = "America/New_York"
    utc = datetime.UTC
    MonthlyQuotaRefreshLog.objects.create(
        executed_at=datetime.datetime(2026, 2, 1, 2, 0, tzinfo=utc))
    user_quota.monthly_jobs = 0
    user_quota.save()
    mocker.patch(
        "django.utils.timezone.now",
        return_value=datetime.datetime(2026, 2, 1, 6, 0, tzinfo=utc))

    call_command("refreshmonthly")

    user_quota.refresh_from_db()
    assert user_quota.monthly_jobs == user_quota.get_monthly_job_max()


def test_refreshmonthly_timezone_ahead_of_utc_stays_aligned(
    user_quota: UserQuota,
    settings: LazySettings,
    mocker: pytest_mock.MockerFixture,
) -> None:
    """A zone ahead of UTC (Tokyo) tracks the local month, not the UTC one.

    A refresh logged at 10:00 UTC (19:00 JST, Jan 31) must not block a run
    at 20:00 UTC (05:00 JST, Feb 1) -- a new Tokyo month, but still the same
    UTC calendar month.
    """
    settings.QUOTA_RESET_TIMEZONE = "Asia/Tokyo"
    utc = datetime.UTC
    MonthlyQuotaRefreshLog.objects.create(
        executed_at=datetime.datetime(2026, 1, 31, 10, 0, tzinfo=utc))
    user_quota.monthly_jobs = 0
    user_quota.save()
    mocker.patch(
        "django.utils.timezone.now",
        return_value=datetime.datetime(2026, 1, 31, 20, 0, tzinfo=utc))

    call_command("refreshmonthly")

    user_quota.refresh_from_db()
    assert user_quota.monthly_jobs == user_quota.get_monthly_job_max()


def test_refreshmonthly_defaults_to_utc_month_boundary(
    user_quota: UserQuota,
    mocker: pytest_mock.MockerFixture,
) -> None:
    """With QUOTA_RESET_TIMEZONE unset, the guard stays a UTC month.

    Two runs within the same UTC calendar month: the second is skipped.
    """
    utc = datetime.UTC
    MonthlyQuotaRefreshLog.objects.create(
        executed_at=datetime.datetime(2026, 2, 1, 2, 0, tzinfo=utc))
    user_quota.monthly_jobs = 0
    user_quota.save()
    mocker.patch(
        "django.utils.timezone.now",
        return_value=datetime.datetime(2026, 2, 1, 6, 0, tzinfo=utc))

    call_command("refreshmonthly")

    user_quota.refresh_from_db()
    assert user_quota.monthly_jobs == 0


def test_refreshmonthly_rolls_back_all_changes_on_failure(
    user_quota: UserQuota,
    anonymous_quota: AnonymousUserQuota,
    mocker: pytest_mock.MockerFixture,
) -> None:
    """A failure partway through leaves no partial resets and no log row."""
    user_quota.monthly_jobs = 0
    user_quota.save()
    mocker.patch.object(
        AnonymousUserQuota, "reset_monthly",
        side_effect=RuntimeError("boom"))

    with pytest.raises(RuntimeError, match="boom"):
        call_command("refreshmonthly")

    user_quota.refresh_from_db()
    assert user_quota.monthly_jobs == 0  # rolled back, not refilled
    assert MonthlyQuotaRefreshLog.objects.count() == 0


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
    assert int(row["extra_variants"]) == anonymous_quota.extra_variants


def test_export_quotas_writes_to_file(
    user_quota: UserQuota,
    tmp_path: pathlib.Path,
) -> None:
    output = tmp_path / "quotas.csv"
    call_command("export_quotas", str(output))
    rows = list(csv.DictReader(output.open()))
    assert any(r["type"] == "user" for r in rows)
