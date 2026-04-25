# pylint: disable=W0621,C0114,C0116,W0212,W0613
import textwrap
from django.core.files.base import ContentFile
from django.test import Client
import pytest
import pytest_mock

from gain.genomic_resources.repository import GenomicResourceRepo

from web_annotation.models import Pipeline, User
from web_annotation.pipeline_cache import LRUPipelineCache


@pytest.mark.django_db
def test_create_pipeline_stores_in_cache(
    test_grr: GenomicResourceRepo,
    user_client: Client,
    mocker: pytest_mock.MockerFixture,
) -> None:
    pipeline_config = "- position_score: scores/pos1"

    params = {
        "config": ContentFile(pipeline_config),
        "name": "cache_test_pipeline",
    }

    user = User.objects.get(email="user@example.com")
    assert Pipeline.objects.filter(owner=user).count() == 0

    cache = LRUPipelineCache(test_grr, 16)
    mocker.patch(
        "web_annotation.pipelines"
        ".views.UserPipeline.lru_cache",
        new=cache,
    )
    assert "1" not in cache._cache

    response = user_client.post("/api/pipelines/user", params)

    assert response.status_code == 200
    assert "1" in cache._cache
    pipeline = cache._cache["1"]
    assert pipeline.future.result().raw == [{"position_score": "scores/pos1"}]

    pipeline_config = textwrap.dedent("""
        - position_score:
            attributes:
              - name: position_1
                source: pos1
            resource_id: scores/pos1
    """)

    params = {
        "id": "1",
        "config": ContentFile(pipeline_config),
        "name": "cache_test_pipeline",
    }
    response = user_client.post("/api/pipelines/user", params)

    assert response.status_code == 200
    assert "1" in cache._cache
    pipeline = cache._cache["1"]
    assert pipeline.future.result().raw == [{"position_score": {
        "attributes": [{"name": "position_1", "source": "pos1"}],
        "resource_id": "scores/pos1",
    }}]
