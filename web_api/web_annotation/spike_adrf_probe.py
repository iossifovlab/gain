"""SPIKE ARTIFACT -- throwaway async-vehicle probe for iossifovlab/gain#162.

This module is a deliberately minimal, *throwaway* probe used to de-risk the
async-DRF (`adrf`) vehicle BEFORE the real read-view conversion (#163). It is
NOT production code: it wires a single instrumented async endpoint at
``/api/_spike/adrf-probe`` (see ``web_annotation/urls.py``) that exercises the
real ``WebAnnotationAuthentication`` + ``UserRateThrottle`` stack and records
which thread each phase ran on.

What it proves (encoded as assertions in
``tests/test_spike_adrf_probe.py``):

* ``adrf.views.APIView`` cleanly hosts an ``async def`` handler -- DRF 3.17.1
  alone cannot.
* adrf's ``async_dispatch`` wraps DRF's ``initial()`` in
  ``await sync_to_async(self.initial)(...)``. ``initial()`` runs
  ``perform_authentication`` (our session read + ``session.save()``) and
  ``check_throttles``, so all of that blocking I/O executes in a thread-pool
  worker -- OFF the event-loop thread. No fallback override of
  ``perform_authentication`` is needed.
* DRF exception mapping survives: a ``ValidationError`` raised inside the async
  handler renders as 400 (with reason) and ``NotFound`` as 404 -- not 500 --
  because ``async_dispatch`` keeps the ``try/except -> handle_exception`` hook.

Delete this module (and its test) when #163 lands.
"""
import threading
from typing import Any, ClassVar

from adrf.views import APIView as AsyncAPIView
from django.http.request import HttpRequest
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import Request, Response

from web_annotation.authentication import WebAnnotationAuthentication
from web_annotation.models import BaseUser

# Records the thread idents each request phase ran on. The test reads this to
# assert that auth/session/throttle ran off the event-loop thread.
PROBE_RECORD: dict[str, Any] = {}


class ProbeAuthentication(WebAnnotationAuthentication):
    """Real auth, instrumented to record the thread it (and save) run on."""

    def authenticate(
        self, request: HttpRequest,
    ) -> tuple[BaseUser, None]:
        PROBE_RECORD["auth_thread"] = threading.get_ident()
        result = super().authenticate(request)
        # The base class does the blocking session read (and a
        # ``session.save()`` on the anonymous path). Force a real session write
        # here unconditionally so we can prove the save-thread on BOTH the
        # authenticated and anonymous paths; record the thread it ran on.
        request.session["spike_probe_touch"] = threading.get_ident()
        request.session.save()
        PROBE_RECORD["session_save_thread"] = threading.get_ident()
        return result


class ProbeThrottle(UserRateThrottle):
    """Real throttle, instrumented to record the thread it runs on."""

    def allow_request(self, request: Request, view: Any) -> bool:
        PROBE_RECORD["throttle_thread"] = threading.get_ident()
        return bool(super().allow_request(request, view))


class AdrfProbeView(AsyncAPIView):
    """Throwaway async probe endpoint -- SPIKE #162, delete with #163."""

    authentication_classes: ClassVar = [ProbeAuthentication]
    throttle_classes: ClassVar = [ProbeThrottle]

    async def get(self, request: Request) -> Response:
        """Record the event-loop thread and exercise exception mapping."""
        PROBE_RECORD["handler_thread"] = threading.get_ident()

        # ``request.user`` was resolved during ``initial()`` (off-loop); read
        # it here only to report the authenticated flag.
        user = request.user
        raise_kind = request.query_params.get("raise")
        if raise_kind == "validation":
            raise ValidationError("spike validation boom")
        if raise_kind == "notfound":
            raise NotFound("spike not found boom")

        return Response({
            "authenticated": bool(getattr(user, "is_authenticated", False)),
            "thread": PROBE_RECORD["handler_thread"],
        })
