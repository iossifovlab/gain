# pylint: disable=W0621,C0114,C0116
"""A multipart body without its file field is a 400, not a KeyError.

These views indexed ``request.FILES[...]`` directly, so a form body that
carried its other fields but left the file out raised
``MultiValueDictKeyError``: an unhandled 500, on the preview endpoint an
anonymous one (iossifovlab/gain#1661).
"""
import pathlib

import pytest
from django.conf import settings
from django.test import Client

from web_annotation.models import Job, Pipeline, TemporaryPipeline

JOB_URLS = ["/api/jobs/annotate_vcf", "/api/jobs/annotate_tabular"]


def _config_files() -> set[pathlib.Path]:
    return set(pathlib.Path(settings.ANNOTATION_CONFIG_STORAGE_DIR).rglob("*"))


@pytest.mark.django_db
def test_preview_without_a_data_file_is_a_400(
    anonymous_client: Client,
) -> None:
    response = anonymous_client.post(
        "/api/jobs/preview", {"separator": ","},
    )

    assert response.status_code == 400
    assert response.json() == {"reason": "No 'data' file uploaded!"}


@pytest.mark.django_db
def test_temporary_pipeline_without_a_config_file_is_a_400(
    anonymous_client: Client,
) -> None:
    before = _config_files()

    response = anonymous_client.post("/api/pipelines/user", {"id": ""})

    assert response.status_code == 400
    assert response.json() == {"reason": "No 'config' file uploaded!"}
    assert not TemporaryPipeline.objects.exists()
    assert _config_files() == before


@pytest.mark.django_db
def test_named_pipeline_without_a_config_file_is_a_400(
    user_client: Client,
) -> None:
    before = _config_files()

    response = user_client.post("/api/pipelines/user", {"name": "p1"})

    assert response.status_code == 400
    assert response.json() == {"reason": "No 'config' file uploaded!"}
    assert not Pipeline.objects.filter(name="p1").exists()
    assert _config_files() == before


@pytest.mark.django_db
@pytest.mark.parametrize(("data", "status", "reason"), [
    ({"name": "p1"}, 401, "Only authenticated users can create pipelines!"),
    ({"id": "not-my-session"}, 400, "Pipeline ID does not match session ID!"),
])
def test_pipeline_refusals_outrank_the_missing_config_file(
    anonymous_client: Client, data: dict[str, str], status: int, reason: str,
) -> None:
    response = anonymous_client.post("/api/pipelines/user", data)

    assert response.status_code == status
    assert response.json() == {"reason": reason}


@pytest.mark.django_db
@pytest.mark.parametrize("url", JOB_URLS)
def test_job_without_a_data_file_is_a_400(
    user_client: Client, url: str,
) -> None:
    jobs_before = Job.objects.count()
    before = _config_files()

    response = user_client.post(
        url, {"pipeline_id": "pipeline/test_pipeline"},
    )

    assert response.status_code == 400
    assert response.json() == {"reason": "No 'data' file uploaded!"}
    assert Job.objects.count() == jobs_before
    assert _config_files() == before


@pytest.mark.django_db
@pytest.mark.parametrize("url", JOB_URLS)
def test_invalid_genome_outranks_the_missing_data_file(
    user_client: Client, url: str,
) -> None:
    response = user_client.post(
        url, {"pipeline_id": "pipeline/test_pipeline", "genome": "bogus"},
    )

    assert response.status_code == 404
    assert response.json() == {
        "reason": "Genome bogus is not a valid option!",
    }
