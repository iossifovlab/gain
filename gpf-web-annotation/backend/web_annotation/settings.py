# pylint: disable=wildcard-import,unused-wildcard-import
# flake8: noqa
from .settings_default import *

from pathlib import Path

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = True

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

CORS_ORIGIN_WHITELIST = [
    # For local development
    "http://localhost:4200",
    "http://127.0.0.1:4200",
]

CORS_ALLOW_CREDENTIALS = True

CSRF_TRUSTED_ORIGINS = [
    "http://localhost:4200",
    "http://127.0.0.1:4200",
]

QUOTAS = {
    "daily_jobs": 2000,
    "filesize": "64M",
    "disk_space": "2048M",
    "variant_count": 50_000,
}
