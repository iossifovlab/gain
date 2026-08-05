# pylint: disable=C0114,C0116,W0621
from typing import Any

import pytest
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.http import HttpRequest
from django.test import RequestFactory
from pytest_django.fixtures import SettingsWrapper
from rest_framework.request import Request

from web_annotation.authentication import WebAnnotationAuthentication
from web_annotation.models import (
    AnonymousJob,
    AnonymousUserQuota,
    WebAnnotationAnonymousUser,
)
from web_annotation.single_allele_annotation.throttling import (
    AnnotateUserRateThrottle,
)
from web_annotation.utils import get_ip_from_request


def _request(
    x_forwarded_for: str | None = None,
    remote_addr: str = "127.0.0.1",
) -> HttpRequest:
    """Build a request carrying the given forwarding headers."""
    request = RequestFactory().get("/")
    request.META["REMOTE_ADDR"] = remote_addr
    if x_forwarded_for is not None:
        request.META["HTTP_X_FORWARDED_FOR"] = x_forwarded_for
    return request


def _anonymous_request(
    x_forwarded_for: str | None = None,
    remote_addr: str = "127.0.0.1",
) -> Any:
    """Wrap that request as the API stack sees it: DRF, anonymous user."""
    request = _request(x_forwarded_for, remote_addr)
    request.user = AnonymousUser()
    request.session = SessionStore()
    return Request(request)


def _authenticate(request: Any) -> WebAnnotationAnonymousUser:
    """Authenticate an anonymous request, as the API stack does."""
    user, _ = WebAnnotationAuthentication().authenticate(request)
    assert isinstance(user, WebAnnotationAnonymousUser)
    return user


def _set_trusted_hops(
    settings: SettingsWrapper, num_proxies: int | None,
) -> None:
    """Configure the trusted-hop count the way a deployment would."""
    settings.REST_FRAMEWORK = {
        **settings.REST_FRAMEWORK, "NUM_PROXIES": num_proxies}


@pytest.mark.parametrize(
    "num_proxies, x_forwarded_for, remote_addr, expected_ip",
    [
        # One trusted hop -> the entry the trusted proxy appended, i.e. the
        # rightmost one. The client-supplied prefix cannot influence it.
        (1, "192.168.1.100, 10.0.0.1", "127.0.0.1", "10.0.0.1"),
        (1, "spoofed, 10.0.0.1", "127.0.0.1", "10.0.0.1"),
        # Two trusted hops -> the second entry counted from the right.
        (2, "203.0.113.45, 198.51.100.20, 192.0.2.1", "127.0.0.1",
         "198.51.100.20"),
        (2, "a, b, 198.51.100.20, 192.0.2.1", "127.0.0.1", "198.51.100.20"),
        # Fewer entries than trusted hops -> the leftmost entry there is.
        (3, "198.51.100.20, 192.0.2.1", "127.0.0.1", "198.51.100.20"),
        # No header at all -> REMOTE_ADDR, whatever the hop count.
        (1, None, "192.168.1.50", "192.168.1.50"),
        (2, None, "192.168.1.50", "192.168.1.50"),
        # Zero trusted hops -> the header is ignored entirely.
        (0, "192.168.1.100, 10.0.0.1", "10.0.0.5", "10.0.0.5"),
        (0, None, "10.0.0.5", "10.0.0.5"),
    ],
)
def test_get_ip_from_request_uses_configured_hop_count(
    settings: SettingsWrapper,
    num_proxies: int,
    x_forwarded_for: str | None,
    remote_addr: str,
    expected_ip: str,
) -> None:
    _set_trusted_hops(settings, num_proxies)

    assert get_ip_from_request(
        _request(x_forwarded_for, remote_addr)) == expected_ip


@pytest.mark.django_db
def test_unset_hop_count_keeps_todays_throttle_and_quota_keys(
    settings: SettingsWrapper,
) -> None:
    # The no-op pin: with no hop count configured, the throttle still buckets
    # on the whole header and the quota still keys on its leftmost entry --
    # byte for byte what shipped before #667. Deploying #667 alone must not
    # move a single bucket or quota row; #660 owns choosing a value.
    _set_trusted_hops(settings, None)
    request = _anonymous_request("203.0.113.45, 10.0.0.1", "127.0.0.1")

    throttle_key = AnnotateUserRateThrottle().get_cache_key(request, view=None)
    user = _authenticate(request)

    assert throttle_key == "throttle_annotate_203.0.113.45,10.0.0.1"
    assert user.ip == "203.0.113.45"


@pytest.mark.django_db
def test_anonymous_user_ip_uses_configured_hop_count(
    settings: SettingsWrapper,
) -> None:
    # The quota key must come from the one configurable rule, not from a
    # private leftmost-entry copy inside the authentication class.
    _set_trusted_hops(settings, 1)

    user = _authenticate(_anonymous_request("spoofed-by-client, 10.0.0.1"))

    assert user.ip == "10.0.0.1"


@pytest.mark.django_db
@pytest.mark.parametrize(
    "num_proxies, expected_ip",
    [
        # Zero trusted hops: the header is ignored by BOTH consumers.
        (0, "127.0.0.1"),
        (1, "10.0.0.1"),
        (2, "198.51.100.20"),
    ],
)
def test_throttle_bucket_and_quota_key_are_the_same_address(
    settings: SettingsWrapper,
    num_proxies: int,
    expected_ip: str,
) -> None:
    # One rule, two consumers: a client must not be rate-limited under one
    # address while its expensive-work budget is charged to another.
    _set_trusted_hops(settings, num_proxies)
    request = _anonymous_request("spoofed, 198.51.100.20, 10.0.0.1")

    throttle_key = AnnotateUserRateThrottle().get_cache_key(request, view=None)
    user = _authenticate(request)

    assert user.ip == expected_ip
    assert throttle_key == f"throttle_annotate_{expected_ip}"


@pytest.mark.django_db
def test_client_supplied_prefix_cannot_move_the_bucket_or_the_quota(
    settings: SettingsWrapper,
) -> None:
    # With a hop count configured, everything left of the Nth entry from the
    # right is client-controlled noise: it must not mint a fresh quota row,
    # nor a fresh rate-limit bucket, nor hand back a spent daily job cap.
    _set_trusted_hops(settings, 1)
    throttle = AnnotateUserRateThrottle()
    honest = _anonymous_request("198.51.100.20, 10.0.0.1")
    varied = _anonymous_request("decoy-a, decoy-b, 10.0.0.1")

    honest_user = _authenticate(honest)
    varied_user = _authenticate(varied)
    honest_user.get_quota()
    varied_user.get_quota()
    for _ in range(settings.QUOTAS["daily_jobs"]):
        AnonymousJob(
            input_path="test",
            config_path="test",
            result_path="test",
            owner=honest_user.identifier,
            ip=honest_user.ip,
        ).save()

    assert (
        throttle.get_cache_key(honest, view=None)
        == throttle.get_cache_key(varied, view=None)
    )
    assert list(
        AnonymousUserQuota.objects.values_list("ip", flat=True),
    ) == ["10.0.0.1"]
    assert not honest_user.can_create()
    assert not varied_user.can_create()
