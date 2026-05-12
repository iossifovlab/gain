# pylint: disable=W0621,C0114,C0116
import pytest
from django.test import RequestFactory

from admin_panel.views import reset_daily_quota, reset_monthly_quota
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
