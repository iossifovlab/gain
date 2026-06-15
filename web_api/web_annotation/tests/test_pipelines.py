# pylint: disable=W0621,C0114,C0116,W0212,W0613
import textwrap
from unittest.mock import MagicMock

import pytest
import pytest_mock
from django.conf import settings
from django.core.files.base import ContentFile
from django.test import Client
from gain.genomic_resources.repository import GenomicResourceRepo

from web_annotation.annotation_base_view import AnnotationBaseView
from web_annotation.models import Pipeline, User
from web_annotation.pipeline_cache import LRUPipelineCache


@pytest.mark.django_db
def test_pipeline_doc_returns_html_download(
    user_client: Client,
) -> None:
    response = user_client.get(
        "/api/pipelines/doc?pipeline_id=pipeline/test_pipeline")
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/html")
    assert "attachment" in response["Content-Disposition"]
    assert "pipeline/test_pipeline.html" in response["Content-Disposition"]
    assert len(response.content) > 0


@pytest.mark.django_db
def test_pipeline_doc_does_not_expose_pipeline_path(
    user_client: Client,
) -> None:
    response = user_client.get(
        "/api/pipelines/doc?pipeline_id=pipeline/test_pipeline")
    assert response.status_code == 200
    content = response.content.decode()
    assert "Pipeline path:" not in content


@pytest.mark.django_db
def test_pipeline_doc_missing_pipeline_id(
    user_client: Client,
) -> None:
    response = user_client.get("/api/pipelines/doc")
    assert response.status_code == 400


@pytest.mark.django_db
def test_list_pipelines_default_pipeline_first(
    user_client: Client,
    mocker: pytest_mock.MockerFixture,
) -> None:
    mocker.patch.object(settings, "DEFAULT_PIPELINE", "pipeline/test_pipeline")
    response = user_client.get("/api/pipelines")
    assert response.status_code == 200
    pipelines = response.json()
    assert len(pipelines) > 0
    assert pipelines[0]["name"] == "pipeline/test_pipeline"


@pytest.mark.django_db
def test_list_pipelines_default_pipeline_none_preserves_order(
    user_client: Client,
    mocker: pytest_mock.MockerFixture,
) -> None:
    mocker.patch.object(settings, "DEFAULT_PIPELINE", None)
    response = user_client.get("/api/pipelines")
    assert response.status_code == 200
    pipelines = response.json()
    names = [p["name"] for p in pipelines]
    assert names == sorted(names)


@pytest.mark.django_db
def test_list_pipelines_default_pipeline_not_found_errors(
    user_client: Client,
    mocker: pytest_mock.MockerFixture,
) -> None:
    mocker.patch.object(settings, "DEFAULT_PIPELINE", "pipeline/nonexistent")
    response = user_client.get("/api/pipelines")
    assert response.status_code == 500


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


def test_view_get_pipeline_reloads_on_cache_miss(
    mocker: pytest_mock.MockerFixture,
) -> None:
    """View ``get_pipeline`` recovers from a cache-miss by reloading (#140).

    Reproduces Findings 1/2: even with pinning, an entry can vanish between
    the view's has/put check-then-act and the pin taken inside the cache's
    ``get_pipeline`` (capacity eviction in the residual window, or the timeout
    reaper / a force reload), surfacing a ``ValueError`` cache-miss. The view
    must re-``put_pipeline`` (reload from the same source) and retry, not
    propagate a spurious 4xx for a pipeline that is genuinely available.
    """
    view = AnnotationBaseView()
    user = MagicMock()
    sentinel = object()

    fake_cache = MagicMock()
    fake_cache.has_pipeline.return_value = True
    # First resolution misses (evicted/reaped in the residual window);
    # after a reload it resolves.
    fake_cache.get_pipeline.side_effect = [
        ValueError("Pipeline p not found"),
        sentinel,
    ]
    mocker.patch.object(view, "lru_cache", fake_cache)
    put_spy = mocker.patch.object(view, "put_pipeline")

    result = view.get_pipeline("p", user)

    assert result is sentinel
    assert fake_cache.get_pipeline.call_count == 2
    # Reload happened via the existing locked put_pipeline path.
    assert put_spy.called


def test_view_get_pipeline_reraises_after_exhausting_retries(
    mocker: pytest_mock.MockerFixture,
) -> None:
    """A genuinely-missing pipeline still raises after bounded retries (#140).

    The reload-on-miss retry must be bounded so a pipeline that cannot be
    loaded does not loop forever; after the bound it re-raises the original
    cache-miss ValueError so the view layer still returns a 4xx.
    """
    view = AnnotationBaseView()
    user = MagicMock()

    fake_cache = MagicMock()
    fake_cache.has_pipeline.return_value = False
    fake_cache.get_pipeline.side_effect = ValueError("Pipeline p not found")
    mocker.patch.object(view, "lru_cache", fake_cache)
    put_spy = mocker.patch.object(view, "put_pipeline")

    with pytest.raises(ValueError, match="Pipeline p not found"):
        view.get_pipeline("p", user)

    # Bounded: a small finite number of attempts, not an infinite loop.
    assert 1 < fake_cache.get_pipeline.call_count <= 5
    assert put_spy.call_count >= 1
