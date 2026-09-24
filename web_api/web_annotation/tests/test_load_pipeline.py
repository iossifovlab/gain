# pylint: disable=W0621,C0116
"""``POST /api/pipelines/load`` refuses ids that do not resolve (#1666)."""
import pytest
from django.core.files.base import ContentFile
from django.test import Client

from web_annotation.pipeline_cache import LRUPipelineCache


def _save_unloaded_pipeline(
    client: Client, cache: LRUPipelineCache, name: str,
) -> str:
    """Save a pipeline as the client, then drop the load the save began."""
    response = client.post("/api/pipelines/user", {
        "config": ContentFile("- position_score: scores/pos1"),
        "name": name,
    })
    assert response.status_code == 200
    pipeline_id = str(response.json()["id"])
    cache.unload_pipeline(pipeline_id)
    return pipeline_id


def _assert_refused_as_not_found(
    client: Client, pipeline_id: str, cache: LRUPipelineCache,
) -> None:
    response = client.post("/api/pipelines/load", {"id": pipeline_id})

    assert response.status_code == 404
    assert "detail" in response.json()
    assert cache.has_pipeline(pipeline_id) is False


@pytest.mark.django_db
@pytest.mark.parametrize(("caller", "pipeline_id"), [
    ("anonymous", "1"),
    ("anonymous", "foo"),
    ("user", "999999"),
    ("user", "foo"),
])
def test_load_of_an_unresolvable_id_is_not_found(
    clients: dict[str, Client],
    patched_lru_cache: LRUPipelineCache,
    caller: str,
    pipeline_id: str,
) -> None:
    _assert_refused_as_not_found(
        clients[caller], pipeline_id, patched_lru_cache)


@pytest.mark.django_db
def test_load_of_own_saved_pipeline_is_accepted(
    user_client: Client,
    patched_lru_cache: LRUPipelineCache,
) -> None:
    own_pipeline_id = _save_unloaded_pipeline(
        user_client, patched_lru_cache, "own_pipeline")

    response = user_client.post(
        "/api/pipelines/load", {"id": own_pipeline_id})

    assert response.status_code == 204
    assert patched_lru_cache.has_pipeline(own_pipeline_id) is True


@pytest.mark.django_db
def test_load_of_another_users_pipeline_is_not_found(
    admin_client: Client,
    user_client: Client,
    patched_lru_cache: LRUPipelineCache,
) -> None:
    admins_pipeline_id = _save_unloaded_pipeline(
        admin_client, patched_lru_cache, "admins_pipeline")

    _assert_refused_as_not_found(
        user_client, admins_pipeline_id, patched_lru_cache)
