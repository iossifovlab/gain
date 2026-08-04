"""Rate throttles that stay usable under the Playwright e2e suite.

``SessionScopedUserRateThrottle`` behaves exactly like DRF's
``UserRateThrottle`` (per-user bucket when authenticated, per-IP bucket when
anonymous) EXCEPT when ``settings.E2E_SESSION_SCOPED_THROTTLE`` is set, where
anonymous requests are bucketed by their **session** instead of their IP.

This exists for the e2e suite (iossifovlab/gain#179): every anonymous request
from the single test container shares one IP, so a per-IP bucket is exhausted
across unrelated tests and they flake with a spurious 429. Keying the
anonymous bucket by session (each test runs in a fresh browser context, hence
a fresh session) isolates tests from each other while keeping the limit
intact, so the dedicated rate-limit specs still trip 429 within their own
session. The flag is only ever true under ``settings_e2e`` -- production
keying is byte-for-byte unchanged (IP for anonymous, user id for
authenticated).

Subclasses pick a ``scope``, and therefore a rate from
``REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]``. They must not share one: the
annotate endpoint's budget is a quota on expensive work, while the pipeline
editor validates on a keystroke cadence, and one bucket cannot serve both.
"""
from typing import Any, cast

from django.conf import settings
from rest_framework.throttling import UserRateThrottle


class SessionScopedUserRateThrottle(UserRateThrottle):
    """UserRateThrottle that can bucket anonymous requests by session (e2e)."""

    def get_cache_key(self, request: Any, view: Any) -> str | None:
        if request.user and request.user.is_authenticated:
            return cast("str | None", super().get_cache_key(request, view))

        if getattr(settings, "E2E_SESSION_SCOPED_THROTTLE", False):
            session = getattr(request, "session", None)
            session_key = getattr(session, "session_key", None)
            if session_key:
                # Build the key by hand for this session-scoped branch; ``key``
                # is annotated so mypy accepts the ``Any``-typed
                # ``cache_format %`` result.
                key: str = self.cache_format % {
                    "scope": self.scope,
                    "ident": session_key,
                }
                return key

        # No session key -> fall back to the IP bucket. Not hit in the real
        # annotate flow: WebAnnotationAuthentication forces ``session.save()``
        # and DRF authenticates before ``check_throttles``, so ``session_key``
        # is always populated by the time the bucket is keyed. The pipeline
        # validation endpoint is anonymous and does not force a session, so
        # there the IP bucket is the normal path outside e2e.
        return cast("str | None", super().get_cache_key(request, view))
