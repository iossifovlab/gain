"""Rate throttle for the single-allele annotate endpoint.

``AnnotateUserRateThrottle`` behaves exactly like DRF's ``UserRateThrottle``
(per-user bucket when authenticated, per-IP bucket when anonymous) EXCEPT when
``settings.E2E_SESSION_SCOPED_THROTTLE`` is set, where anonymous requests are
bucketed by their **session** instead of their IP.

This exists for the Playwright e2e suite (iossifovlab/gain#179): every anonymous
annotate from the single test container shares one IP, so the 10/minute bucket
is exhausted across unrelated tests and they flake with a spurious 429. Keying
anonymous bucket by session (each test runs in a fresh browser context, hence a
fresh session) isolates tests from each other while keeping the limit at 10/min,
so the dedicated rate-limit specs still trip 429 within their own session. The
flag is only ever true under ``settings_e2e`` -- production keying is byte-for-
byte unchanged (IP for anonymous, user id for authenticated).
"""
from typing import Any, cast

from django.conf import settings
from rest_framework.throttling import UserRateThrottle


class AnnotateUserRateThrottle(UserRateThrottle):
    """UserRateThrottle that can bucket anonymous requests by session (e2e)."""

    def get_cache_key(self, request: Any, view: Any) -> str | None:
        if request.user and request.user.is_authenticated:
            return cast("str | None", super().get_cache_key(request, view))

        if getattr(settings, "E2E_SESSION_SCOPED_THROTTLE", False):
            session = getattr(request, "session", None)
            session_key = getattr(session, "session_key", None)
            if session_key:
                key: str = self.cache_format % {
                    "scope": self.scope,
                    "ident": session_key,
                }
                return key

        return cast("str | None", super().get_cache_key(request, view))
