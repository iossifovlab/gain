# pylint: disable=wildcard-import,unused-wildcard-import
# flake8: noqa
from .settings_default import *


USERS_ACTIVATED_BY_DEFAULT = True
STATIC_ROOT = '/static/gpf/static'


QUOTAS = {
    "daily_jobs": 2,
    "filesize": "64M",
    "disk_space": "2048M",
    "variant_count": 1000,
}

RESOURCES_BASE_URL = "http://grr.seqpipe.org/"

ANNOTATION_MAX_WORKERS = 16
PIPELINES_CACHE_SIZE = 256
