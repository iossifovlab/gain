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

Throttling is opt-in per view: ``DEFAULT_THROTTLE_CLASSES`` is deliberately
unset, so a view is limited only by listing a throttle in its
``throttle_classes``. The scopes that exist, and the views each one reaches:

* ``annotate`` -- ``/api/single_allele/annotate``
* ``pipeline_validate`` -- ``/api/pipelines/validate``
* ``auth_login`` -- ``/api/login``, per client IP
* ``auth_login_identifier`` -- ``/api/login``, per submitted email
* ``auth_register`` -- ``/api/register``, per client IP
* ``auth_password_reset`` -- ``/api/forgotten_password``, per client IP, on
  the POSTs only; the GET renders the form the login page links to and is
  exempt, because a three-an-hour budget spent on page renders locks a user
  out before a single mail is sent
* ``auth_password_reset_identifier`` -- ``/api/forgotten_password``, per
  submitted email, on the POSTs only as well: both axes of one endpoint have
  to exempt the same methods, or the exempt one becomes a free way to spend
  the other (see ``SafeMethodExemptRateThrottle``)
* ``auth_confirm`` -- ``/api/confirm_account`` and ``/api/reset_password``,
  per client IP

The authentication endpoints were entirely unthrottled until
iossifovlab/gain#694: sixty wrong-password logins were all served, and sixty
forgotten-password requests sent sixty mails through the production relay.
Login and forgotten_password are limited on two independent axes, both
enforced -- by client IP, which stops one host spraying many accounts, and by
the submitted email, which stops one account being sprayed from many hosts.
The per-identifier rates are deliberately the more generous of the two: a
tight identifier bucket is a lockout denial-of-service, since anyone who knows
an address could burn that user's budget. That axis catches distributed
spraying; it is not the primary limit.

The two axes only stay independent because the dual-keyed views derive from
``FirstRefusalThrottledAPIView``: DRF would otherwise charge the per-address
bucket for requests the per-IP bucket has already refused, and the lockout
the generous rate exists to avoid would be back. The per-IP axis is also
address-keyed for authenticated requests, not user-keyed -- see
``ClientIpRateThrottle``.

Throttle state lives in Django's default ``LocMemCache``: it is per-process
and wiped on every redeploy. That is only sound because production runs a
single daphne process (iossifovlab/gain#150) -- scaling to replicas would
multiply every limit here by the worker count, because each process would
carry its own counters. A shared cache arrives with the Redis channel layer
multi-worker support needs; until then, one process is a load-bearing
deployment invariant.
"""
import hashlib
import math
from typing import Any, cast

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from rest_framework import status
from rest_framework.exceptions import APIException, Throttled
from rest_framework.permissions import SAFE_METHODS
from rest_framework.throttling import (
    BaseThrottle,
    SimpleRateThrottle,
    UserRateThrottle,
)
from rest_framework.views import APIView

from web_annotation.utils import get_ip_from_request


class FirstRefusalThrottledAPIView(APIView):
    """An APIView whose throttle check stops at the first refusal.

    DRF's ``APIView.check_throttles`` calls ``allow_request`` on every
    configured throttle and only raises once all of them have run, while
    ``SimpleRateThrottle`` records a hit in its bucket for every request it
    allows. A throttle listed after another therefore charges its bucket for
    a request the earlier one has already refused.

    On the dual-keyed endpoints that turns the deliberately generous
    per-address axis into a lockout denial-of-service. One host can POST
    thirty logins a minute carrying a victim's address: its own per-IP bucket
    refuses twenty of them, but all thirty land in the per-address bucket, so
    the owner of that address is refused login -- from every address, for as
    long as the attacker keeps going. The 3x ratio between the two rates
    bounds nothing here, because the attacker is not paying the per-IP rate
    while spending the identifier budget. On ``/api/forgotten_password`` it
    is worse still: nine POSTs an hour, only three of them served, cost the
    victim the password reset for the rest of the hour.

    So the first throttle to refuse ends the check and nothing after it is
    charged. The views list the per-IP axis first, which is the order that
    makes a refusal cost the refused client's own bucket and no one else's.
    The reported ``Retry-After`` becomes the first refusing throttle's wait
    rather than the longest across all of them -- the wait for the bucket the
    client is actually sitting behind.
    """

    def check_throttles(self, request: Any) -> None:
        for throttle in self.get_throttles():
            if not throttle.allow_request(request, self):
                self.throttled(request, throttle.wait())


def throttled_message(wait: float | None) -> str:
    """Say how long the client has to wait, not merely that it was refused.

    ``Retry-After`` carries the delay for machines; this carries it for the
    person reading the page. On the reset bucket the wait can be an hour, and
    "try again later" gives them no way to tell that from a minute. Rounded
    up, and never to "0 minutes".
    """
    if wait is None:
        return "Too many requests. Please try again later."
    minutes = math.ceil(wait / 60)
    if minutes <= 1:
        return "Too many requests. Please try again in about a minute."
    return (
        f"Too many requests. Please try again in about {minutes} minutes."
    )


class HtmlThrottledAPIView(APIView):
    """An APIView that answers a throttled request with the app's own page.

    DRF refuses a throttled request before the view runs, and renders that
    refusal through content negotiation. These views configure no renderer,
    so a browser's ``Accept: text/html`` selects DRF's
    ``BrowsableAPIRenderer`` and the user meets a framework debug page
    titled "... - Django REST framework". On ``confirm_account`` that page
    is what a user gets for clicking a link in their own mail, and the
    endpoint could not answer 429 at all before iossifovlab/gain#694 added
    these throttles -- so the debug page is a regression this change would
    otherwise have shipped.

    Subclasses point ``throttled_template`` at the page they already render
    and override ``get_throttled_context`` if that page needs more than a
    message; the default is a standalone page for the views that redirect on
    every other path and so have none of their own.

    ``Retry-After`` is re-applied by hand because it is set by the exception
    handler this bypasses, and it is the only machine-readable part of a 429.
    """

    throttled_template = "throttled.html"

    def get_throttled_context(self, wait: float | None) -> dict[str, Any]:
        """Return the template context for a throttled response."""
        return {"message": throttled_message(wait)}

    def handle_exception(self, exc: Exception) -> HttpResponse:
        if not isinstance(exc, Throttled):
            return cast("HttpResponse", super().handle_exception(exc))

        response = render(
            cast(HttpRequest, self.request),
            self.throttled_template,
            self.get_throttled_context(exc.wait),
            status=status.HTTP_429_TOO_MANY_REQUESTS,
        )
        if exc.wait is not None:
            response["Retry-After"] = f"{math.ceil(exc.wait)}"
        return response


class SessionScopedUserRateThrottle(UserRateThrottle):
    """UserRateThrottle that can bucket anonymous requests by session (e2e)."""

    def get_cache_key(self, request: Any, view: Any) -> str | None:
        if request.user and request.user.is_authenticated:
            return cast("str | None", super().get_cache_key(request, view))

        return self.get_session_or_ip_cache_key(request, view)

    def get_session_or_ip_cache_key(
        self, request: Any, view: Any,
    ) -> str | None:
        """Key a request by session under e2e, otherwise by address.

        The branch DRF takes for an anonymous request -- and the only branch
        ``ClientIpRateThrottle`` ever takes, whether or not the request
        carries a session (see there).
        """
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
        return self.get_ip_cache_key(request, view)

    def get_ip_cache_key(self, request: Any, view: Any) -> str | None:
        """Key an anonymous request by address, using DRF's derivation.

        Kept as DRF's for the throttles that predate this seam: with no hop
        count configured DRF buckets on the whole ``X-Forwarded-For`` header,
        and ``test_client_ip`` pins that as today's annotate bucket, byte for
        byte. ``ClientIpRateThrottle`` overrides it for the throttles added
        since.
        """
        return cast("str | None", super().get_cache_key(request, view))


class ClientIpRateThrottle(SessionScopedUserRateThrottle):
    """Session-scoped under e2e, otherwise keyed on the shared client IP.

    The address comes from ``web_annotation.utils.get_ip_from_request``, the
    one place the web API interprets ``X-Forwarded-For``
    (iossifovlab/gain#667). Deriving it anywhere else -- including through
    DRF's own ``get_ident``, which reads the whole header when no hop count is
    configured -- would let a client be budgeted under one address and
    rate-limited under another.

    Unlike the base class, this takes the session/IP path *unconditionally*:
    an authenticated request is keyed by address too, never by user id. The
    per-user bucket the base class inherits from DRF is a quota on work a
    logged-in user asks for; nothing on the authentication surface is that.
    Login, register and forgotten_password all install
    ``WebAnnotationAuthentication``, and DRF authenticates before it checks
    throttles, so a request carrying a valid session cookie arrives here
    already authenticated -- and a per-user bucket would hand it a private
    budget outside the address bucket entirely. An attacker who registers
    and logs into N accounts would get N independent login budgets from one host
    and could spray N times faster, with the identifier axis catching none of
    it (spraying is one attempt per victim address). "Per client IP" has to
    bind for everyone, session or no session.
    """

    def get_cache_key(self, request: Any, view: Any) -> str | None:
        return self.get_session_or_ip_cache_key(request, view)

    def get_ip_cache_key(
        self, request: Any, view: Any,  # noqa: ARG002
    ) -> str | None:
        key: str = self.cache_format % {
            "scope": self.scope,
            "ident": get_ip_from_request(request),
        }
        return key


class LoginRateThrottle(ClientIpRateThrottle):
    """Per-IP cap on ``/api/login``: one host spraying many accounts."""

    scope = "auth_login"


class SubmittedEmailRateThrottle(SimpleRateThrottle):
    """Bucket a request by a hash of the email address it submits.

    The second axis of the dual key: the IP axis stops one host spraying many
    accounts, this one stops many hosts spraying one account.

    Two properties are load-bearing:

    * The bucket exists whether or not the address belongs to a real account.
      Keying on a resolved user row would make the 429 boundary an
      account-existence oracle -- the very disclosure iossifovlab/gain#696
      exists to close.
    * The address is hashed into the key, never embedded raw. A raw address is
      PII sitting in the cache, and can carry characters hostile to cache-key
      encoding.

    A request carrying no usable address is not throttled on this axis at all
    (no cache key): the view's own 400 already refuses it, and inventing a
    bucket for "malformed" would pool unrelated clients into one.
    """

    identifier_field = "email"

    def get_cache_key(
        self, request: Any, view: Any,  # noqa: ARG002
    ) -> str | None:
        if getattr(settings, "E2E_DISABLE_IDENTIFIER_THROTTLE", False):
            return None

        identifier = self._submitted_identifier(request)
        if identifier is None:
            return None

        digest = hashlib.sha256(identifier.encode("utf-8")).hexdigest()
        key: str = self.cache_format % {"scope": self.scope, "ident": digest}
        return key

    def _submitted_identifier(self, request: Any) -> str | None:
        """Return the normalised submitted address, or None if unusable."""
        try:
            data = request.data
        except APIException:
            # A body that will not parse, or arrives in a media type no
            # configured parser handles, carries no identifier. Both surface
            # as APIExceptions (ParseError, UnsupportedMediaType) and both are
            # the view's to answer, not the throttle's. Swallowing them here
            # does not swallow the refusal: on an unsupported media type DRF
            # never reached the body, so the view's own ``request.data`` raises
            # again and answers 415; on a parse error DRF has already replaced
            # the body with an empty QueryDict, so the view sees a request
            # carrying no email and answers its own 400. Either way the client
            # gets a 4xx from the view, and the throttle declines to invent a
            # bucket for a body it could not read.
            return None
        if not isinstance(data, dict):
            return None
        submitted = data.get(self.identifier_field)
        if not isinstance(submitted, str):
            return None
        candidate = submitted.strip().lower()
        try:
            validate_email(candidate)
        except ValidationError:
            return None
        return candidate


class LoginIdentifierRateThrottle(SubmittedEmailRateThrottle):
    """Per-address cap on ``/api/login``: many hosts spraying one account."""

    scope = "auth_login_identifier"


class RegisterRateThrottle(ClientIpRateThrottle):
    """Per-IP cap on ``/api/register``.

    Registration is only per-IP: its identifier axis would be a free
    account-existence oracle (the address either registers or is refused as
    taken), and one address can only ever register once anyway.
    """

    scope = "auth_register"


class SafeMethodExemptRateThrottle(BaseThrottle):
    """A mixin whose throttle neither refuses nor charges a safe method.

    Not a throttle on its own: it keys nothing and must be mixed in ahead of
    a real one, whose ``allow_request`` it short-circuits. The exemption is
    decided before any bucket is keyed, so nothing is recorded -- and on the
    identifier-keyed side ``request.data`` is never touched, so a safe method
    no longer parses a body that only the POST path reads.

    Every throttle on one endpoint has to agree about this, which is why the
    exemption lives here rather than being written out twice. An endpoint
    whose per-IP axis is exempt while its per-address axis is not is strictly
    worse than one with no exemption: the safe method then charges someone
    else's bucket while paying into none of its own, so an attacker can hold
    a victim's per-address bucket full forever -- with GET, HEAD or OPTIONS,
    since all three carry a body DRF will parse -- for free. That is the
    lockout ``FirstRefusalThrottledAPIView`` exists to prevent, re-entered
    through the exemption.
    """

    def allow_request(self, request: Any, view: Any) -> bool:
        if request.method in SAFE_METHODS:
            return True
        return cast("bool", super().allow_request(request, view))


class PasswordResetRateThrottle(SafeMethodExemptRateThrottle,
                                ClientIpRateThrottle):
    """Per-IP cap on the ``/api/forgotten_password`` requests that send mail.

    A served POST here sends a mail through the production relay, so this is
    the tightest per-IP bucket of the five -- tight enough that it must not be
    spent on anything cheaper. The endpoint answers two different things: a
    GET renders the HTML form the login page links to
    (``reset-password-link``), and a POST is what mails. Charging the render
    against a three-an-hour budget would leave a legitimate user a single
    attempt, and clicking the link three times -- or reloading after a typo --
    would lock them out of resetting their password for an hour without a
    single mail having been sent.

    So safe methods are exempt, on this axis and on the per-address one
    alike -- see ``SafeMethodExemptRateThrottle`` for why they cannot differ.
    This gives up nothing: the form render costs one template and touches no
    user, no mail relay and no database, and every path that can send a mail
    is a POST, still budgeted at the full rate. The other four throttles have
    no such split -- login and register are POST only, and on the
    code-redeeming endpoints the GET *is* the redemption, so exempting it
    there would leave code guessing unthrottled.
    """

    scope = "auth_password_reset"


class PasswordResetIdentifierRateThrottle(SafeMethodExemptRateThrottle,
                                          SubmittedEmailRateThrottle):
    """Per-address cap on the ``/api/forgotten_password`` POSTs.

    Bounds how many reset mails one mailbox can be made to receive, however
    many hosts ask for them.

    Safe methods are exempt here for the same reason they are on the per-IP
    axis, and it matters more: this bucket is not the requester's own. A
    method the per-IP axis does not charge must not charge this one either,
    or spending someone else's reset budget costs nothing.
    """

    scope = "auth_password_reset_identifier"


class AccountConfirmRateThrottle(ClientIpRateThrottle):
    """Per-IP cap on the code-redeeming endpoints.

    ``/api/confirm_account`` and ``/api/reset_password`` both take a
    single-use code from a mail, so the traffic that matters here is guessing
    codes. They share a scope because they are the same activity against the
    same code space; neither has a submitted address to key on -- the code is
    the credential.
    """

    scope = "auth_confirm"
