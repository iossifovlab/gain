# pylint: disable=wildcard-import,unused-wildcard-import
# flake8: noqa
from .settings_default import *


USERS_ACTIVATED_BY_DEFAULT = True
STATIC_ROOT = '/static/gpf/static'

INSTALLED_APPS += ["admin_panel"]


QUOTAS = {
    "daily_jobs": 2,
    "filesize": "64M",
    "disk_space": "2048M",
}

ANNOTATION_MAX_WORKERS = 16
PIPELINES_CACHE_SIZE = 256

# Enable the test-only WS ping route (web_annotation.loadtest.ping_view) used by
# the #170 WS-notification-responsiveness harness. Never set in production.
LOADTEST_PING_ENABLED = True

# Bucket the anonymous single-allele annotate throttle by session instead of IP
# (AnnotateUserRateThrottle), so each Playwright test (fresh browser context =>
# fresh session) gets its own 10/minute budget and the suite stops cross-
# exhausting the shared-container-IP bucket (iossifovlab/gain#179). Never set in
# production -- prod keeps IP-based anonymous throttling.
E2E_SESSION_SCOPED_THROTTLE = True

# Switch off the per-submitted-address axis of the authentication throttles
# (gain#694). The flag above isolates the IP-keyed axis per browser context,
# but nothing isolates a bucket keyed on the email a request submits: the e2e
# suite logs in as a handful of seeded accounts, over and over, from every
# spec, so those few addresses would cross-exhaust their shared bucket and
# unrelated specs would flake on a spurious 429. Loosening the rate only moves
# the cliff -- the suite grows, the cliff comes back -- so the axis is off
# here rather than merely generous. The per-IP axis still applies, so the
# rate-limit specs that assert a 429 keep working. Never set in production:
# there the identifier axis is what stops one account being sprayed from many
# hosts.
#
# The per-IP axis of those same throttles is left at its production rate,
# because the flag above already gives each browser context its own bucket.
# That matters most for register and confirm_account: nearly every spec calls
# utils.registerUser, which POSTs /api/register and then opens the
# confirmation link from the mail, and 5/hour and 20/hour against one shared
# container IP would not survive a suite this size. Two different mechanisms
# keep those requests off the IP bucket, and only one of them is airtight:
#
#   * login, register and forgotten_password authenticate through
#     WebAnnotationAuthentication, which saves the session before DRF checks
#     throttles, so a session key always exists by then -- no cookie needed.
#   * confirm_account and reset_password do not, so they are session-scoped
#     only while the browser sends a session cookie. It does: the SPA calls
#     GET /api/user_info on boot (AppComponent.ngOnInit), that goes through
#     WebAnnotationAuthentication too and its response sets the cookie, and
#     in CI the mail link is the same origin as the app
#     (GPFWA_EMAIL_VERIFICATION_ENDPOINT is http://frontend in
#     web_infra/compose-jenkins.yaml, which is also playwright's baseURL), so
#     the cookie is sent.
#
# If a spec ever starts flaking on a 429 from confirm_account or
# reset_password, that second chain is what broke -- the request arrived with
# no session cookie and fell through to the shared-container-IP bucket. Raise
# the auth_confirm rate here rather than in settings_default: production is
# where that bucket has to bind.
E2E_DISABLE_IDENTIFIER_THROTTLE = True
