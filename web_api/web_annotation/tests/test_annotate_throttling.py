# pylint: disable=C0114,C0116
from types import SimpleNamespace
from typing import Any

from pytest_django.fixtures import SettingsWrapper
from rest_framework.throttling import UserRateThrottle

from web_annotation import settings_e2e
from web_annotation.single_allele_annotation.throttling import (
    AnnotateUserRateThrottle,
)
from web_annotation.single_allele_annotation.views import SingleAnnotation


def _request(
    *,
    authenticated: bool = False,
    user_pk: int | None = None,
    session_key: str | None = None,
    ip: str = "10.0.0.1",
) -> Any:
    """Build a stand-in request exposing what the throttle key reads."""
    user = SimpleNamespace(is_authenticated=authenticated, pk=user_pk)
    session = SimpleNamespace(session_key=session_key)
    return SimpleNamespace(user=user, session=session, META={"REMOTE_ADDR": ip})


def test_anon_distinct_sessions_get_distinct_buckets(
    settings: SettingsWrapper,
) -> None:
    # Under the e2e session-scoped throttle, two anonymous requests from the
    # SAME IP but DIFFERENT sessions must land in different rate-limit buckets,
    # so one Playwright test cannot exhaust another's annotate budget.
    settings.E2E_SESSION_SCOPED_THROTTLE = True
    throttle = AnnotateUserRateThrottle()

    key_a = throttle.get_cache_key(
        _request(session_key="sess-A", ip="10.0.0.1"), view=None)
    key_b = throttle.get_cache_key(
        _request(session_key="sess-B", ip="10.0.0.1"), view=None)

    assert key_a is not None
    assert key_b is not None
    assert key_a != key_b
    assert "sess-A" in key_a
    assert "sess-B" in key_b


def test_anon_same_session_shares_one_bucket(
    settings: SettingsWrapper,
) -> None:
    # Within one session the budget is shared, so the dedicated rate-limit spec
    # still trips 429 after exhausting its 10/minute in a single session.
    settings.E2E_SESSION_SCOPED_THROTTLE = True
    throttle = AnnotateUserRateThrottle()

    key1 = throttle.get_cache_key(_request(session_key="sess-A"), view=None)
    key2 = throttle.get_cache_key(_request(session_key="sess-A"), view=None)

    assert key1 == key2


def test_flag_off_keys_anonymous_by_ip_like_userratethrottle(
    settings: SettingsWrapper,
) -> None:
    # Production parity: with the flag off, the anonymous bucket is keyed by IP
    # exactly as DRF's UserRateThrottle, regardless of session.
    settings.E2E_SESSION_SCOPED_THROTTLE = False
    throttle = AnnotateUserRateThrottle()
    baseline = UserRateThrottle()

    request = _request(session_key="sess-A", ip="203.0.113.7")
    assert (
        throttle.get_cache_key(request, view=None)
        == baseline.get_cache_key(request, view=None)
    )
    # Same IP, different session -> SAME bucket when the flag is off.
    other_session = _request(session_key="sess-Z", ip="203.0.113.7")
    assert (
        throttle.get_cache_key(request, view=None)
        == throttle.get_cache_key(other_session, view=None)
    )


def test_authenticated_user_keyed_by_user_regardless_of_flag(
    settings: SettingsWrapper,
) -> None:
    # Session-scoping must never apply to authenticated users -- their bucket is
    # keyed by user id (so the logged-in rate-limit spec still works).
    settings.E2E_SESSION_SCOPED_THROTTLE = True
    throttle = AnnotateUserRateThrottle()

    request = _request(authenticated=True, user_pk=42, session_key="sess-A")
    key = throttle.get_cache_key(request, view=None)

    assert key is not None
    assert "42" in key
    assert "sess-A" not in key


def test_anon_without_session_falls_back_to_ip(
    settings: SettingsWrapper,
) -> None:
    # Safety: a not-yet-established session must not break keying -- fall
    # back to the IP bucket (today's behavior).
    settings.E2E_SESSION_SCOPED_THROTTLE = True
    throttle = AnnotateUserRateThrottle()
    baseline = UserRateThrottle()

    request = _request(session_key=None, ip="198.51.100.4")
    assert (
        throttle.get_cache_key(request, view=None)
        == baseline.get_cache_key(request, view=None)
    )


def test_single_annotation_view_uses_session_scoped_throttle() -> None:
    # The annotate endpoint must be wired to the session-scoping throttle.
    assert AnnotateUserRateThrottle in SingleAnnotation.throttle_classes


def test_settings_e2e_enables_session_scoped_throttle() -> None:
    # The flag is only ever true under settings_e2e (the module the e2e daphne
    # server runs under); pytest runs under test_settings, so assert on the
    # settings_e2e module object directly.
    assert settings_e2e.E2E_SESSION_SCOPED_THROTTLE is True
