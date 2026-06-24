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

RESOURCES_BASE_URL = "http://grr.seqpipe.org/"

ANNOTATION_MAX_WORKERS = 16
PIPELINES_CACHE_SIZE = 256

# Enable the test-only WS ping route (web_annotation.loadtest.ping_view) used by
# the #170 WS-notification-responsiveness harness. Never set in production.
LOADTEST_PING_ENABLED = True
