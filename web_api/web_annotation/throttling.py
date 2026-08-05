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
  submitted email
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

Throttle state lives in Django's default ``LocMemCache``: it is per-process
and wiped on every redeploy. That is only sound because production runs a
single daphne process (iossifovlab/gain#150) -- scaling to replicas would
multiply every limit here by the worker count, because each process would
carry its own counters. A shared cache arrives with the Redis channel layer
multi-worker support needs; until then, one process is a load-bearing
deployment invariant.
"""
import hashlib
from typing import Any, cast

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from rest_framework.exceptions import APIException
from rest_framework.permissions import SAFE_METHODS
from rest_framework.throttling import SimpleRateThrottle, UserRateThrottle

from web_annotation.utils import get_ip_from_request


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
    """

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


class PasswordResetRateThrottle(ClientIpRateThrottle):
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

    So safe methods are exempt. This gives up nothing: the form render costs
    one template and touches no user, no mail relay and no database, and every
    path that can send a mail is a POST, still budgeted at the full rate. The
    other four throttles have no such split -- login and register are POST
    only, and on the code-redeeming endpoints the GET *is* the redemption, so
    exempting it there would leave code guessing unthrottled.
    """

    scope = "auth_password_reset"

    def allow_request(self, request: Any, view: Any) -> bool:
        if request.method in SAFE_METHODS:
            return True
        return cast("bool", super().allow_request(request, view))


class PasswordResetIdentifierRateThrottle(SubmittedEmailRateThrottle):
    """Per-address cap on ``/api/forgotten_password``.

    Bounds how many reset mails one mailbox can be made to receive, however
    many hosts ask for them.
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
