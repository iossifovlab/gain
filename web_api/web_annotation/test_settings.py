# pylint: disable=wildcard-import,unused-wildcard-import
import os
import pathlib
import tempfile
import time

import yaml

from .settings import *  # noqa

# Dir for all data storage
DATA_STORAGE_DIR = \
    f"{tempfile.gettempdir()}/gpf-web-annotation-tests-data-{int(time.time())}"
# Subdir to store uploaded annotation configurations in
ANNOTATION_CONFIG_STORAGE_DIR = f"{DATA_STORAGE_DIR}/annotation-configs"
# Subdir to store uploaded files in before they are annotated
JOB_INPUT_STORAGE_DIR = f"{DATA_STORAGE_DIR}/job-inputs"
# Subdir to store results of annotation in
JOB_RESULT_STORAGE_DIR = f"{DATA_STORAGE_DIR}/job-results"

QUOTAS = {
    "daily_jobs": 5,
    "filesize": "64M",
    "disk_space": "2048M",
    "variant_count": 1000,
}

QUERY_QUOTAS = {
    "anonymous": {
        "daily_jobs": 10,
        "monthly_jobs": 100,
        "daily_allele_queries": 100,
        "monthly_allele_queries": 1_000,
        "daily_variants": 100_000,
        "monthly_variants": 1_000_000,
        "daily_attributes": 1_000_000,
        "monthly_attributes": 10_000_000,
    },
    "user": {
        "daily_jobs": 100,
        "monthly_jobs": 1_000,
        "daily_allele_queries": 1_000,
        "monthly_allele_queries": 10_000,
        "daily_variants": 1_000_000,
        "monthly_variants": 10_000_000,
        "daily_attributes": 10_000_000,
        "monthly_attributes": 100_000_000,
    },
}


GRR_DIRECTORY = str(
    pathlib.Path(__file__).parent / "tests" / "fixtures" / "grr")

GRR_DEFINITION_PATH = str(
    pathlib.Path(GRR_DIRECTORY) / "grr_definition.yaml")
pathlib.Path(GRR_DEFINITION_PATH).write_text(yaml.safe_dump({
    "id": "test",
    "type": "dir",
    "directory": GRR_DIRECTORY,
}))

RESOURCES_BASE_URL = "http://test/"

EMAIL_REDIRECT_ENDPOINT = os.environ.get(
    "GPFWA_EMAIL_REDIRECT_ENDPOINT", "http://testserver/")

JOB_CLEANUP_INTERVAL_DAYS = 7
