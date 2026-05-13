# pylint: disable=W0621,C0114,C0116
import json

import pytest
from admin_panel.views import (
    reset_daily_quota,
    reset_monthly_quota,
    set_current_quota,
    set_extra_quota,
)
from django.test import RequestFactory

from web_annotation.models import (
    DailyQuotaRefreshLog,
    MonthlyQuotaRefreshLog,
    User,
    UserQuota,
)


@pytest.fixture
def factory() -> RequestFactory:
    return RequestFactory()


@pytest.fixture
def user_quota() -> UserQuota:
    user = User.objects.get(email="user@example.com")
    quota = UserQuota(user=user)
    quota.reset_daily()
    quota.reset_monthly()
    quota.save()
    return quota


def test_reset_daily_quota_returns_204(factory: RequestFactory) -> None:
    request = factory.get("/admin-panel/reset-daily-quota")
    response = reset_daily_quota(request)
    assert response.status_code == 204


def test_reset_monthly_quota_returns_204(factory: RequestFactory) -> None:
    request = factory.get("/admin-panel/reset-monthly-quota")
    response = reset_monthly_quota(request)
    assert response.status_code == 204


def test_reset_daily_quota_resets_user_quotas(
    factory: RequestFactory,
    user_quota: UserQuota,
) -> None:
    user_quota.daily_jobs = 0
    user_quota.save()

    reset_daily_quota(factory.get("/admin-panel/reset-daily-quota"))

    user_quota.refresh_from_db()
    assert user_quota.daily_jobs == user_quota.get_daily_job_max()


def test_reset_monthly_quota_resets_user_quotas(
    factory: RequestFactory,
    user_quota: UserQuota,
) -> None:
    user_quota.monthly_jobs = 0
    user_quota.save()

    reset_monthly_quota(factory.get("/admin-panel/reset-monthly-quota"))

    user_quota.refresh_from_db()
    assert user_quota.monthly_jobs == user_quota.get_monthly_job_max()


def test_reset_daily_quota_creates_log(
    factory: RequestFactory,
    user_quota: UserQuota,
) -> None:
    assert DailyQuotaRefreshLog.objects.count() == 0
    reset_daily_quota(factory.get("/admin-panel/reset-daily-quota"))
    assert DailyQuotaRefreshLog.objects.count() == 1


def test_reset_monthly_quota_creates_log(
    factory: RequestFactory,
    user_quota: UserQuota,
) -> None:
    assert MonthlyQuotaRefreshLog.objects.count() == 0
    reset_monthly_quota(factory.get("/admin-panel/reset-monthly-quota"))
    assert MonthlyQuotaRefreshLog.objects.count() == 1


def test_set_extra_quota_returns_200(
    factory: RequestFactory,
    user_quota: UserQuota,
) -> None:
    request = factory.get("/admin-panel/set-extra-quota", {
        "user_email": "user@example.com",
        "quota_type": "jobs",
        "amount": "10",
    })
    response = set_extra_quota(request)
    assert response.status_code == 200


def test_set_extra_quota_returns_snapshot_json(
    factory: RequestFactory,
    user_quota: UserQuota,
) -> None:
    request = factory.get("/admin-panel/set-extra-quota", {
        "user_email": "user@example.com",
        "quota_type": "jobs",
        "amount": "10",
    })
    response = set_extra_quota(request)
    data = json.loads(response.content)
    assert "extra_jobs" in data
    assert "daily_jobs" in data
    assert "monthly_jobs" in data


def test_set_extra_quota_jobs(
    factory: RequestFactory,
    user_quota: UserQuota,
) -> None:
    request = factory.get("/admin-panel/set-extra-quota", {
        "user_email": "user@example.com",
        "quota_type": "jobs",
        "amount": "42",
    })
    set_extra_quota(request)

    user_quota.refresh_from_db()
    assert user_quota.extra_jobs == 42


def test_set_extra_quota_variants(
    factory: RequestFactory,
    user_quota: UserQuota,
) -> None:
    request = factory.get("/admin-panel/set-extra-quota", {
        "user_email": "user@example.com",
        "quota_type": "variants",
        "amount": "999",
    })
    set_extra_quota(request)

    user_quota.refresh_from_db()
    assert user_quota.extra_variants == 999


def test_set_extra_quota_attributes(
    factory: RequestFactory,
    user_quota: UserQuota,
) -> None:
    request = factory.get("/admin-panel/set-extra-quota", {
        "user_email": "user@example.com",
        "quota_type": "attributes",
        "amount": "500",
    })
    set_extra_quota(request)

    user_quota.refresh_from_db()
    assert user_quota.extra_attributes == 500


def test_set_extra_quota_snapshot_reflects_change(
    factory: RequestFactory,
    user_quota: UserQuota,
) -> None:
    request = factory.get("/admin-panel/set-extra-quota", {
        "user_email": "user@example.com",
        "quota_type": "variants",
        "amount": "77",
    })
    response = set_extra_quota(request)
    assert json.loads(response.content)["extra_variants"] == 77


def test_set_extra_quota_invalid_type(
    factory: RequestFactory,
    user_quota: UserQuota,
) -> None:
    request = factory.get("/admin-panel/set-extra-quota", {
        "user_email": "user@example.com",
        "quota_type": "daily_jobs",
        "amount": "10",
    })
    response = set_extra_quota(request)
    assert response.status_code == 400


def test_set_extra_quota_missing_param(
    factory: RequestFactory,
    user_quota: UserQuota,
) -> None:
    request = factory.get("/admin-panel/set-extra-quota", {
        "user_email": "user@example.com",
        "quota_type": "jobs",
    })
    response = set_extra_quota(request)
    assert response.status_code == 400


def test_set_extra_quota_user_not_found(
    factory: RequestFactory,
) -> None:
    request = factory.get("/admin-panel/set-extra-quota", {
        "user_email": "nobody@example.com",
        "quota_type": "jobs",
        "amount": "5",
    })
    response = set_extra_quota(request)
    assert response.status_code == 404


def test_set_extra_quota_invalid_amount(
    factory: RequestFactory,
    user_quota: UserQuota,
) -> None:
    request = factory.get("/admin-panel/set-extra-quota", {
        "user_email": "user@example.com",
        "quota_type": "jobs",
        "amount": "not-a-number",
    })
    response = set_extra_quota(request)
    assert response.status_code == 400


def test_set_current_quota_returns_200(
    factory: RequestFactory,
    user_quota: UserQuota,
) -> None:
    request = factory.get("/admin-panel/set-current-quota", {
        "user_email": "user@example.com",
        "quota_type": "daily_jobs",
        "amount": "50",
    })
    response = set_current_quota(request)
    assert response.status_code == 200


def test_set_current_quota_returns_snapshot_json(
    factory: RequestFactory,
    user_quota: UserQuota,
) -> None:
    request = factory.get("/admin-panel/set-current-quota", {
        "user_email": "user@example.com",
        "quota_type": "daily_jobs",
        "amount": "50",
    })
    response = set_current_quota(request)
    data = json.loads(response.content)
    assert "daily_jobs" in data
    assert "extra_jobs" in data


def test_set_current_quota_daily_jobs(
    factory: RequestFactory,
    user_quota: UserQuota,
) -> None:
    request = factory.get("/admin-panel/set-current-quota", {
        "user_email": "user@example.com",
        "quota_type": "daily_jobs",
        "amount": "7",
    })
    set_current_quota(request)

    user_quota.refresh_from_db()
    assert user_quota.daily_jobs == 7


def test_set_current_quota_monthly_jobs(
    factory: RequestFactory,
    user_quota: UserQuota,
) -> None:
    request = factory.get("/admin-panel/set-current-quota", {
        "user_email": "user@example.com",
        "quota_type": "monthly_jobs",
        "amount": "300",
    })
    set_current_quota(request)

    user_quota.refresh_from_db()
    assert user_quota.monthly_jobs == 300


def test_set_current_quota_daily_variants(
    factory: RequestFactory,
    user_quota: UserQuota,
) -> None:
    request = factory.get("/admin-panel/set-current-quota", {
        "user_email": "user@example.com",
        "quota_type": "daily_variants",
        "amount": "12345",
    })
    set_current_quota(request)

    user_quota.refresh_from_db()
    assert user_quota.daily_variants == 12345


def test_set_current_quota_monthly_variants(
    factory: RequestFactory,
    user_quota: UserQuota,
) -> None:
    request = factory.get("/admin-panel/set-current-quota", {
        "user_email": "user@example.com",
        "quota_type": "monthly_variants",
        "amount": "99999",
    })
    set_current_quota(request)

    user_quota.refresh_from_db()
    assert user_quota.monthly_variants == 99999


def test_set_current_quota_daily_attributes(
    factory: RequestFactory,
    user_quota: UserQuota,
) -> None:
    request = factory.get("/admin-panel/set-current-quota", {
        "user_email": "user@example.com",
        "quota_type": "daily_attributes",
        "amount": "8000",
    })
    set_current_quota(request)

    user_quota.refresh_from_db()
    assert user_quota.daily_attributes == 8000


def test_set_current_quota_monthly_attributes(
    factory: RequestFactory,
    user_quota: UserQuota,
) -> None:
    request = factory.get("/admin-panel/set-current-quota", {
        "user_email": "user@example.com",
        "quota_type": "monthly_attributes",
        "amount": "55000",
    })
    set_current_quota(request)

    user_quota.refresh_from_db()
    assert user_quota.monthly_attributes == 55000


def test_set_current_quota_snapshot_reflects_change(
    factory: RequestFactory,
    user_quota: UserQuota,
) -> None:
    request = factory.get("/admin-panel/set-current-quota", {
        "user_email": "user@example.com",
        "quota_type": "monthly_jobs",
        "amount": "123",
    })
    response = set_current_quota(request)
    assert json.loads(response.content)["monthly_jobs"] == 123


def test_set_current_quota_invalid_type(
    factory: RequestFactory,
    user_quota: UserQuota,
) -> None:
    request = factory.get("/admin-panel/set-current-quota", {
        "user_email": "user@example.com",
        "quota_type": "jobs",
        "amount": "10",
    })
    response = set_current_quota(request)
    assert response.status_code == 400


def test_set_current_quota_missing_param(
    factory: RequestFactory,
    user_quota: UserQuota,
) -> None:
    request = factory.get("/admin-panel/set-current-quota", {
        "user_email": "user@example.com",
        "quota_type": "daily_jobs",
    })
    response = set_current_quota(request)
    assert response.status_code == 400


def test_set_current_quota_user_not_found(
    factory: RequestFactory,
) -> None:
    request = factory.get("/admin-panel/set-current-quota", {
        "user_email": "ghost@example.com",
        "quota_type": "daily_jobs",
        "amount": "5",
    })
    response = set_current_quota(request)
    assert response.status_code == 404


def test_set_current_quota_invalid_amount(
    factory: RequestFactory,
    user_quota: UserQuota,
) -> None:
    request = factory.get("/admin-panel/set-current-quota", {
        "user_email": "user@example.com",
        "quota_type": "daily_jobs",
        "amount": "abc",
    })
    response = set_current_quota(request)
    assert response.status_code == 400
