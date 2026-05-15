# pylint: disable=W0621,C0114,C0116
from typing import cast
from unittest.mock import MagicMock

import pytest
from admin_panel.views import (
    ResetDailyQuotaView,
    ResetMonthlyQuotaView,
    SetCurrentQuotaView,
    SetExtraQuotaView,
    SetIpQuotaView,
    SetSessionQuotaView,
)
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory, force_authenticate

from web_annotation.models import (
    AnonymousUserQuota,
    DailyQuotaRefreshLog,
    MonthlyQuotaRefreshLog,
    SessionQuota,
    User,
    UserQuota,
    WebAnnotationAnonymousUser,
)


def _anon() -> WebAnnotationAnonymousUser:
    return WebAnnotationAnonymousUser(
        session_id="test-session", ip="127.0.0.1",
    )


@pytest.fixture
def factory() -> APIRequestFactory:
    return APIRequestFactory()


@pytest.fixture
def user_quota() -> UserQuota:
    user = User.objects.get(email="user@example.com")
    quota = UserQuota(user=user)
    quota.reset_daily()
    quota.reset_monthly()
    quota.save()
    return quota


def test_reset_daily_quota_returns_204(factory: APIRequestFactory) -> None:
    request = factory.get("/admin-panel/reset-daily-quota")
    force_authenticate(request, user=_anon())
    response = ResetDailyQuotaView.as_view()(request)
    assert response.status_code == 204


def test_reset_monthly_quota_returns_204(factory: APIRequestFactory) -> None:
    request = factory.get("/admin-panel/reset-monthly-quota")
    force_authenticate(request, user=_anon())
    response = ResetMonthlyQuotaView.as_view()(request)
    assert response.status_code == 204


def test_reset_daily_quota_resets_user_quotas(
    factory: APIRequestFactory,
    user_quota: UserQuota,
) -> None:
    user_quota.daily_jobs = 0
    user_quota.save()

    request = factory.get("/admin-panel/reset-daily-quota")
    force_authenticate(request, user=_anon())
    ResetDailyQuotaView.as_view()(request)

    user_quota.refresh_from_db()
    assert user_quota.daily_jobs == user_quota.get_daily_job_max()


def test_reset_monthly_quota_resets_user_quotas(
    factory: APIRequestFactory,
    user_quota: UserQuota,
) -> None:
    user_quota.monthly_jobs = 0
    user_quota.save()

    request = factory.get("/admin-panel/reset-monthly-quota")
    force_authenticate(request, user=_anon())
    ResetMonthlyQuotaView.as_view()(request)

    user_quota.refresh_from_db()
    assert user_quota.monthly_jobs == user_quota.get_monthly_job_max()


def test_reset_daily_quota_creates_log(
    factory: APIRequestFactory,
    user_quota: UserQuota,
) -> None:
    assert DailyQuotaRefreshLog.objects.count() == 0
    request = factory.get("/admin-panel/reset-daily-quota")
    force_authenticate(request, user=_anon())
    ResetDailyQuotaView.as_view()(request)
    assert DailyQuotaRefreshLog.objects.count() == 1


def test_reset_monthly_quota_creates_log(
    factory: APIRequestFactory,
    user_quota: UserQuota,
) -> None:
    assert MonthlyQuotaRefreshLog.objects.count() == 0
    request = factory.get("/admin-panel/reset-monthly-quota")
    force_authenticate(request, user=_anon())
    ResetMonthlyQuotaView.as_view()(request)
    assert MonthlyQuotaRefreshLog.objects.count() == 1


def test_set_extra_quota_returns_200(
    factory: APIRequestFactory,
    user_quota: UserQuota,
) -> None:
    request = factory.get("/admin-panel/set-extra-quota", {
        "user_email": "user@example.com",
        "quota_type": "jobs",
        "amount": "10",
    })
    force_authenticate(request, user=_anon())
    response = SetExtraQuotaView.as_view()(request)
    assert response.status_code == 200


def test_set_extra_quota_returns_snapshot_json(
    factory: APIRequestFactory,
    user_quota: UserQuota,
) -> None:
    request = factory.get("/admin-panel/set-extra-quota", {
        "user_email": "user@example.com",
        "quota_type": "jobs",
        "amount": "10",
    })
    force_authenticate(request, user=_anon())
    response: Response = cast(Response, SetExtraQuotaView.as_view()(request))
    assert response.data is not None
    assert "extra_jobs" in response.data
    assert "daily_jobs" in response.data
    assert "monthly_jobs" in response.data


def test_set_extra_quota_jobs(
    factory: APIRequestFactory,
    user_quota: UserQuota,
) -> None:
    request = factory.get("/admin-panel/set-extra-quota", {
        "user_email": "user@example.com",
        "quota_type": "jobs",
        "amount": "42",
    })
    force_authenticate(request, user=_anon())
    SetExtraQuotaView.as_view()(request)

    user_quota.refresh_from_db()
    assert user_quota.extra_jobs == 42


def test_set_extra_quota_variants(
    factory: APIRequestFactory,
    user_quota: UserQuota,
) -> None:
    request = factory.get("/admin-panel/set-extra-quota", {
        "user_email": "user@example.com",
        "quota_type": "variants",
        "amount": "999",
    })
    force_authenticate(request, user=_anon())
    SetExtraQuotaView.as_view()(request)

    user_quota.refresh_from_db()
    assert user_quota.extra_variants == 999


def test_set_extra_quota_attributes(
    factory: APIRequestFactory,
    user_quota: UserQuota,
) -> None:
    request = factory.get("/admin-panel/set-extra-quota", {
        "user_email": "user@example.com",
        "quota_type": "attributes",
        "amount": "500",
    })
    force_authenticate(request, user=_anon())
    SetExtraQuotaView.as_view()(request)

    user_quota.refresh_from_db()
    assert user_quota.extra_attributes == 500


def test_set_extra_quota_snapshot_reflects_change(
    factory: APIRequestFactory,
    user_quota: UserQuota,
) -> None:
    request = factory.get("/admin-panel/set-extra-quota", {
        "user_email": "user@example.com",
        "quota_type": "variants",
        "amount": "77",
    })
    force_authenticate(request, user=_anon())
    response: Response = cast(Response, SetExtraQuotaView.as_view()(request))
    assert response.data is not None
    assert response.data["extra_variants"] == 77


def test_set_extra_quota_invalid_type(
    factory: APIRequestFactory,
    user_quota: UserQuota,
) -> None:
    request = factory.get("/admin-panel/set-extra-quota", {
        "user_email": "user@example.com",
        "quota_type": "daily_jobs",
        "amount": "10",
    })
    force_authenticate(request, user=_anon())
    response = SetExtraQuotaView.as_view()(request)
    assert response.status_code == 400


def test_set_extra_quota_missing_param(
    factory: APIRequestFactory,
    user_quota: UserQuota,
) -> None:
    request = factory.get("/admin-panel/set-extra-quota", {
        "user_email": "user@example.com",
        "quota_type": "jobs",
    })
    force_authenticate(request, user=_anon())
    response = SetExtraQuotaView.as_view()(request)
    assert response.status_code == 400


def test_set_extra_quota_user_not_found(
    factory: APIRequestFactory,
) -> None:
    request = factory.get("/admin-panel/set-extra-quota", {
        "user_email": "nobody@example.com",
        "quota_type": "jobs",
        "amount": "5",
    })
    force_authenticate(request, user=_anon())
    response = SetExtraQuotaView.as_view()(request)
    assert response.status_code == 404


def test_set_extra_quota_invalid_amount(
    factory: APIRequestFactory,
    user_quota: UserQuota,
) -> None:
    request = factory.get("/admin-panel/set-extra-quota", {
        "user_email": "user@example.com",
        "quota_type": "jobs",
        "amount": "not-a-number",
    })
    force_authenticate(request, user=_anon())
    response = SetExtraQuotaView.as_view()(request)
    assert response.status_code == 400


def test_set_current_quota_returns_200(
    factory: APIRequestFactory,
    user_quota: UserQuota,
) -> None:
    request = factory.get("/admin-panel/set-current-quota", {
        "user_email": "user@example.com",
        "quota_type": "daily_jobs",
        "amount": "50",
    })
    force_authenticate(request, user=_anon())
    response = SetCurrentQuotaView.as_view()(request)
    assert response.status_code == 200


def test_set_current_quota_returns_snapshot_json(
    factory: APIRequestFactory,
    user_quota: UserQuota,
) -> None:
    request = factory.get("/admin-panel/set-current-quota", {
        "user_email": "user@example.com",
        "quota_type": "daily_jobs",
        "amount": "50",
    })
    force_authenticate(request, user=_anon())
    response: Response = cast(Response, SetCurrentQuotaView.as_view()(request))
    assert response.data is not None
    assert "daily_jobs" in response.data
    assert "extra_jobs" in response.data


def test_set_current_quota_daily_jobs(
    factory: APIRequestFactory,
    user_quota: UserQuota,
) -> None:
    request = factory.get("/admin-panel/set-current-quota", {
        "user_email": "user@example.com",
        "quota_type": "daily_jobs",
        "amount": "7",
    })
    force_authenticate(request, user=_anon())
    SetCurrentQuotaView.as_view()(request)

    user_quota.refresh_from_db()
    assert user_quota.daily_jobs == 7


def test_set_current_quota_monthly_jobs(
    factory: APIRequestFactory,
    user_quota: UserQuota,
) -> None:
    request = factory.get("/admin-panel/set-current-quota", {
        "user_email": "user@example.com",
        "quota_type": "monthly_jobs",
        "amount": "300",
    })
    force_authenticate(request, user=_anon())
    SetCurrentQuotaView.as_view()(request)

    user_quota.refresh_from_db()
    assert user_quota.monthly_jobs == 300


def test_set_current_quota_daily_variants(
    factory: APIRequestFactory,
    user_quota: UserQuota,
) -> None:
    request = factory.get("/admin-panel/set-current-quota", {
        "user_email": "user@example.com",
        "quota_type": "daily_variants",
        "amount": "12345",
    })
    force_authenticate(request, user=_anon())
    SetCurrentQuotaView.as_view()(request)

    user_quota.refresh_from_db()
    assert user_quota.daily_variants == 12345


def test_set_current_quota_monthly_variants(
    factory: APIRequestFactory,
    user_quota: UserQuota,
) -> None:
    request = factory.get("/admin-panel/set-current-quota", {
        "user_email": "user@example.com",
        "quota_type": "monthly_variants",
        "amount": "99999",
    })
    force_authenticate(request, user=_anon())
    SetCurrentQuotaView.as_view()(request)

    user_quota.refresh_from_db()
    assert user_quota.monthly_variants == 99999


def test_set_current_quota_daily_attributes(
    factory: APIRequestFactory,
    user_quota: UserQuota,
) -> None:
    request = factory.get("/admin-panel/set-current-quota", {
        "user_email": "user@example.com",
        "quota_type": "daily_attributes",
        "amount": "8000",
    })
    force_authenticate(request, user=_anon())
    SetCurrentQuotaView.as_view()(request)

    user_quota.refresh_from_db()
    assert user_quota.daily_attributes == 8000


def test_set_current_quota_monthly_attributes(
    factory: APIRequestFactory,
    user_quota: UserQuota,
) -> None:
    request = factory.get("/admin-panel/set-current-quota", {
        "user_email": "user@example.com",
        "quota_type": "monthly_attributes",
        "amount": "55000",
    })
    force_authenticate(request, user=_anon())
    SetCurrentQuotaView.as_view()(request)

    user_quota.refresh_from_db()
    assert user_quota.monthly_attributes == 55000


def test_set_current_quota_snapshot_reflects_change(
    factory: APIRequestFactory,
    user_quota: UserQuota,
) -> None:
    request = factory.get("/admin-panel/set-current-quota", {
        "user_email": "user@example.com",
        "quota_type": "monthly_jobs",
        "amount": "123",
    })
    force_authenticate(request, user=_anon())
    response: Response = cast(Response, SetCurrentQuotaView.as_view()(request))
    assert response.data is not None
    assert response.data["monthly_jobs"] == 123


def test_set_current_quota_invalid_type(
    factory: APIRequestFactory,
    user_quota: UserQuota,
) -> None:
    request = factory.get("/admin-panel/set-current-quota", {
        "user_email": "user@example.com",
        "quota_type": "jobs",
        "amount": "10",
    })
    force_authenticate(request, user=_anon())
    response = SetCurrentQuotaView.as_view()(request)
    assert response.status_code == 400


def test_set_current_quota_missing_param(
    factory: APIRequestFactory,
    user_quota: UserQuota,
) -> None:
    request = factory.get("/admin-panel/set-current-quota", {
        "user_email": "user@example.com",
        "quota_type": "daily_jobs",
    })
    force_authenticate(request, user=_anon())
    response = SetCurrentQuotaView.as_view()(request)
    assert response.status_code == 400


def test_set_current_quota_user_not_found(
    factory: APIRequestFactory,
) -> None:
    request = factory.get("/admin-panel/set-current-quota", {
        "user_email": "ghost@example.com",
        "quota_type": "daily_jobs",
        "amount": "5",
    })
    force_authenticate(request, user=_anon())
    response = SetCurrentQuotaView.as_view()(request)
    assert response.status_code == 404


def test_set_current_quota_invalid_amount(
    factory: APIRequestFactory,
    user_quota: UserQuota,
) -> None:
    request = factory.get("/admin-panel/set-current-quota", {
        "user_email": "user@example.com",
        "quota_type": "daily_jobs",
        "amount": "abc",
    })
    force_authenticate(request, user=_anon())
    response = SetCurrentQuotaView.as_view()(request)
    assert response.status_code == 400


# --- set_session_quota ---

@pytest.fixture
def session_quota() -> SessionQuota:
    quota = SessionQuota(session_id="test-session-id")
    quota.reset_daily()
    quota.reset_monthly()
    quota.save()
    return quota


def test_set_session_quota_returns_200(
    factory: APIRequestFactory,
    session_quota: SessionQuota,
) -> None:
    request = factory.get("/admin-panel/set-session-quota", {
        "session_id": "test-session-id",
        "quota_type": "daily_jobs",
        "amount": "5",
    })
    force_authenticate(request, user=_anon())
    response = SetSessionQuotaView.as_view()(request)
    assert response.status_code == 200


def test_set_session_quota_returns_snapshot_json(
    factory: APIRequestFactory,
    session_quota: SessionQuota,
) -> None:
    request = factory.get("/admin-panel/set-session-quota", {
        "session_id": "test-session-id",
        "quota_type": "daily_jobs",
        "amount": "5",
    })
    force_authenticate(request, user=_anon())
    response: Response = cast(Response, SetSessionQuotaView.as_view()(request))
    assert response.data is not None
    assert "daily_jobs" in response.data
    assert "extra_jobs" in response.data


def test_set_session_quota_sets_field(
    factory: APIRequestFactory,
    session_quota: SessionQuota,
) -> None:
    request = factory.get("/admin-panel/set-session-quota", {
        "session_id": "test-session-id",
        "quota_type": "monthly_variants",
        "amount": "888",
    })
    force_authenticate(request, user=_anon())
    SetSessionQuotaView.as_view()(request)
    session_quota.refresh_from_db()
    assert session_quota.monthly_variants == 888


def test_set_session_quota_snapshot_reflects_change(
    factory: APIRequestFactory,
    session_quota: SessionQuota,
) -> None:
    request = factory.get("/admin-panel/set-session-quota", {
        "session_id": "test-session-id",
        "quota_type": "daily_attributes",
        "amount": "42",
    })
    force_authenticate(request, user=_anon())
    response: Response = cast(Response, SetSessionQuotaView.as_view()(request))
    assert response.data is not None
    assert response.data["daily_attributes"] == 42


def test_set_session_quota_fallback_to_cookie(
    factory: APIRequestFactory,
    session_quota: SessionQuota,
) -> None:
    request = factory.get("/admin-panel/set-session-quota", {
        "quota_type": "daily_jobs",
        "amount": "3",
    })
    request.session = MagicMock()
    request.session.session_key = "test-session-id"
    force_authenticate(request, user=_anon())
    response = SetSessionQuotaView.as_view()(request)
    assert response.status_code == 200
    session_quota.refresh_from_db()
    assert session_quota.daily_jobs == 3


def test_set_session_quota_creates_quota_if_missing(
    factory: APIRequestFactory,
) -> None:
    request = factory.get("/admin-panel/set-session-quota", {
        "session_id": "brand-new-session",
        "quota_type": "daily_jobs",
        "amount": "7",
    })
    force_authenticate(request, user=_anon())
    response = SetSessionQuotaView.as_view()(request)
    assert response.status_code == 200
    quota = SessionQuota.objects.get(session_id="brand-new-session")
    assert quota.daily_jobs == 7


def test_set_session_quota_missing_session_id(
    factory: APIRequestFactory,
) -> None:
    request = factory.get("/admin-panel/set-session-quota", {
        "quota_type": "daily_jobs",
        "amount": "5",
    })
    request.session = MagicMock()
    request.session.session_key = None
    force_authenticate(request, user=_anon())
    response = SetSessionQuotaView.as_view()(request)
    assert response.status_code == 400


def test_set_session_quota_invalid_type(
    factory: APIRequestFactory,
    session_quota: SessionQuota,
) -> None:
    request = factory.get("/admin-panel/set-session-quota", {
        "session_id": "test-session-id",
        "quota_type": "jobs",
        "amount": "5",
    })
    force_authenticate(request, user=_anon())
    response = SetSessionQuotaView.as_view()(request)
    assert response.status_code == 400


def test_set_session_quota_missing_param(
    factory: APIRequestFactory,
    session_quota: SessionQuota,
) -> None:
    request = factory.get("/admin-panel/set-session-quota", {
        "session_id": "test-session-id",
        "quota_type": "daily_jobs",
    })
    force_authenticate(request, user=_anon())
    response = SetSessionQuotaView.as_view()(request)
    assert response.status_code == 400


def test_set_session_quota_invalid_amount(
    factory: APIRequestFactory,
    session_quota: SessionQuota,
) -> None:
    request = factory.get("/admin-panel/set-session-quota", {
        "session_id": "test-session-id",
        "quota_type": "daily_jobs",
        "amount": "not-a-number",
    })
    force_authenticate(request, user=_anon())
    response = SetSessionQuotaView.as_view()(request)
    assert response.status_code == 400


# --- set_ip_quota ---

@pytest.fixture
def ip_quota() -> AnonymousUserQuota:
    quota = AnonymousUserQuota(ip="1.2.3.4")
    quota.reset_daily()
    quota.reset_monthly()
    quota.save()
    return quota


def test_set_ip_quota_returns_200(
    factory: APIRequestFactory,
    ip_quota: AnonymousUserQuota,
) -> None:
    request = factory.get("/admin-panel/set-ip-quota", {
        "ip": "1.2.3.4",
        "quota_type": "daily_jobs",
        "amount": "5",
    })
    force_authenticate(request, user=_anon())
    response = SetIpQuotaView.as_view()(request)
    assert response.status_code == 200


def test_set_ip_quota_returns_snapshot_json(
    factory: APIRequestFactory,
    ip_quota: AnonymousUserQuota,
) -> None:
    request = factory.get("/admin-panel/set-ip-quota", {
        "ip": "1.2.3.4",
        "quota_type": "daily_jobs",
        "amount": "5",
    })
    force_authenticate(request, user=_anon())
    response: Response = cast(Response, SetIpQuotaView.as_view()(request))
    assert response.data is not None
    assert "daily_jobs" in response.data
    assert "extra_jobs" in response.data


def test_set_ip_quota_sets_field(
    factory: APIRequestFactory,
    ip_quota: AnonymousUserQuota,
) -> None:
    request = factory.get("/admin-panel/set-ip-quota", {
        "ip": "1.2.3.4",
        "quota_type": "monthly_variants",
        "amount": "555",
    })
    force_authenticate(request, user=_anon())
    SetIpQuotaView.as_view()(request)
    ip_quota.refresh_from_db()
    assert ip_quota.monthly_variants == 555


def test_set_ip_quota_snapshot_reflects_change(
    factory: APIRequestFactory,
    ip_quota: AnonymousUserQuota,
) -> None:
    request = factory.get("/admin-panel/set-ip-quota", {
        "ip": "1.2.3.4",
        "quota_type": "daily_attributes",
        "amount": "99",
    })
    force_authenticate(request, user=_anon())
    response: Response = cast(Response, SetIpQuotaView.as_view()(request))
    assert response.data is not None
    assert response.data["daily_attributes"] == 99


def test_set_ip_quota_fallback_to_anonymous_user(
    factory: APIRequestFactory,
    ip_quota: AnonymousUserQuota,
) -> None:
    request = factory.get("/admin-panel/set-ip-quota", {
        "quota_type": "daily_jobs",
        "amount": "3",
    })
    anon = WebAnnotationAnonymousUser(session_id="some-session", ip="1.2.3.4")
    force_authenticate(request, user=anon)
    response = SetIpQuotaView.as_view()(request)
    assert response.status_code == 200
    ip_quota.refresh_from_db()
    assert ip_quota.daily_jobs == 3


def test_set_ip_quota_returns_400_for_authenticated_user(
    factory: APIRequestFactory,
) -> None:
    request = factory.get("/admin-panel/set-ip-quota", {
        "quota_type": "daily_jobs",
        "amount": "5",
    })
    force_authenticate(
        request, user=User.objects.get(email="user@example.com"))
    response = SetIpQuotaView.as_view()(request)
    assert response.status_code == 400


def test_set_ip_quota_creates_quota_if_missing(
    factory: APIRequestFactory,
) -> None:
    request = factory.get("/admin-panel/set-ip-quota", {
        "ip": "9.9.9.9",
        "quota_type": "daily_jobs",
        "amount": "7",
    })
    force_authenticate(request, user=_anon())
    response = SetIpQuotaView.as_view()(request)
    assert response.status_code == 200
    quota = AnonymousUserQuota.objects.get(ip="9.9.9.9")
    assert quota.daily_jobs == 7


def test_set_ip_quota_invalid_type(
    factory: APIRequestFactory,
    ip_quota: AnonymousUserQuota,
) -> None:
    request = factory.get("/admin-panel/set-ip-quota", {
        "ip": "1.2.3.4",
        "quota_type": "jobs",
        "amount": "5",
    })
    force_authenticate(request, user=_anon())
    response = SetIpQuotaView.as_view()(request)
    assert response.status_code == 400


def test_set_ip_quota_missing_param(
    factory: APIRequestFactory,
    ip_quota: AnonymousUserQuota,
) -> None:
    request = factory.get("/admin-panel/set-ip-quota", {
        "ip": "1.2.3.4",
        "quota_type": "daily_jobs",
    })
    force_authenticate(request, user=_anon())
    response = SetIpQuotaView.as_view()(request)
    assert response.status_code == 400


def test_set_ip_quota_invalid_amount(
    factory: APIRequestFactory,
    ip_quota: AnonymousUserQuota,
) -> None:
    request = factory.get("/admin-panel/set-ip-quota", {
        "ip": "1.2.3.4",
        "quota_type": "daily_jobs",
        "amount": "not-a-number",
    })
    force_authenticate(request, user=_anon())
    response = SetIpQuotaView.as_view()(request)
    assert response.status_code == 400
