# pylint: disable=W0621,C0116
"""``/api/pipelines/user`` refuses ids that are not an own saved pipeline.

GET and DELETE address saved pipelines only. Any other id -- anything an
anonymous caller sends, a temporary pipeline id, a non-integer -- answers
a JSON 400 on GET and a no-op 204 on DELETE, never a 500 (#1674).
"""
import pytest
from django.core.files.base import ContentFile
from django.test import Client

from web_annotation.models import Pipeline, TemporaryPipeline
from web_annotation.pipeline_cache import LRUPipelineCache

NOT_RECOGNIZED = {"reason": "Pipeline name not recognized!"}


def _save_pipeline(client: Client, name: str | None = None) -> str:
    """Save a pipeline; without a name it is the session's temporary one."""
    data: dict[str, object] = {
        "config": ContentFile("- position_score: scores/pos1"),
    }
    if name is not None:
        data["name"] = name
    response = client.post("/api/pipelines/user", data)
    assert response.status_code == 200
    return str(response.json()["id"])


@pytest.mark.django_db
@pytest.mark.usefixtures("patched_lru_cache")
@pytest.mark.parametrize(("caller", "pipeline_id"), [
    ("anonymous", "999999"),
    ("anonymous", "foo"),
    ("user", "foo"),
])
def test_get_of_an_unresolvable_id_is_refused(
    clients: dict[str, Client],
    caller: str,
    pipeline_id: str,
) -> None:
    response = clients[caller].get(f"/api/pipelines/user?id={pipeline_id}")

    assert response.status_code == 400
    assert response.json() == NOT_RECOGNIZED


@pytest.mark.django_db
@pytest.mark.usefixtures("patched_lru_cache")
@pytest.mark.parametrize("caller", ["anonymous", "user"])
def test_get_of_own_temporary_id_is_refused(
    clients: dict[str, Client],
    caller: str,
) -> None:
    client = clients[caller]
    temporary_id = _save_pipeline(client)

    response = client.get(f"/api/pipelines/user?id={temporary_id}")

    assert response.status_code == 400
    assert response.json() == NOT_RECOGNIZED


@pytest.mark.django_db
@pytest.mark.usefixtures("patched_lru_cache")
def test_get_of_another_users_saved_pipeline_is_refused(
    admin_client: Client,
    user_client: Client,
) -> None:
    admins_pipeline_id = _save_pipeline(admin_client, "admins_pipeline")

    response = user_client.get(
        f"/api/pipelines/user?id={admins_pipeline_id}")

    assert response.status_code == 400
    assert response.json() == NOT_RECOGNIZED


@pytest.mark.django_db
@pytest.mark.usefixtures("patched_lru_cache")
@pytest.mark.parametrize(("caller", "pipeline_id"), [
    ("anonymous", "999999"),
    ("anonymous", "foo"),
    ("user", "999999"),
    ("user", "foo"),
])
def test_delete_of_an_unresolvable_id_is_a_no_op(
    clients: dict[str, Client],
    caller: str,
    pipeline_id: str,
) -> None:
    response = clients[caller].delete(
        f"/api/pipelines/user?id={pipeline_id}")

    assert response.status_code == 204


@pytest.mark.django_db
@pytest.mark.parametrize("caller", ["anonymous", "user"])
def test_delete_of_own_temporary_id_keeps_the_temporary_pipeline(
    clients: dict[str, Client],
    patched_lru_cache: LRUPipelineCache,
    caller: str,
) -> None:
    client = clients[caller]
    temporary_id = _save_pipeline(client)

    response = client.delete(f"/api/pipelines/user?id={temporary_id}")

    assert response.status_code == 204
    assert TemporaryPipeline.objects.filter(session_id=temporary_id).exists()
    assert patched_lru_cache.has_pipeline(temporary_id) is True


@pytest.mark.django_db
def test_delete_of_another_users_saved_pipeline_keeps_it(
    admin_client: Client,
    user_client: Client,
    patched_lru_cache: LRUPipelineCache,
) -> None:
    admins_pipeline_id = _save_pipeline(admin_client, "admins_pipeline")

    response = user_client.delete(
        f"/api/pipelines/user?id={admins_pipeline_id}")

    assert response.status_code == 204
    assert Pipeline.objects.filter(pk=int(admins_pipeline_id)).exists()
    assert patched_lru_cache.has_pipeline(admins_pipeline_id) is True
