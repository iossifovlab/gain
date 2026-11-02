# pylint: disable=W0621,C0116
"""``/api/pipelines/user`` refuses ids that are not an own saved pipeline.

GET and DELETE address saved pipelines only. Any other id -- anything an
anonymous caller sends, a temporary pipeline id, a non-integer -- answers
a JSON 400 on GET and a no-op 204 on DELETE, never a 500 (#1674).
"""
import pathlib
from typing import Any

import pytest
from django.conf import settings
from django.core.files.base import ContentFile
from django.test import Client
from pytest_mock import MockerFixture

from web_annotation.models import Pipeline, TemporaryPipeline, User
from web_annotation.pipeline_cache import LRUPipelineCache

NOT_RECOGNIZED = {"reason": "Pipeline name not recognized!"}
INVALID_NAME = {"reason": "Invalid pipeline name!"}


def _config_files() -> set[pathlib.Path]:
    return set(pathlib.Path(settings.ANNOTATION_CONFIG_STORAGE_DIR).rglob("*"))


def _post_named_pipeline(client: Client, name: str) -> Any:
    return client.post("/api/pipelines/user", {
        "name": name,
        "config": ContentFile("- position_score: scores/pos1"),
    })


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


@pytest.mark.django_db
@pytest.mark.usefixtures("patched_lru_cache")
@pytest.mark.parametrize("name", ["../x", "a\\b", "a\x00b"])
def test_create_with_a_path_like_name_is_refused(
    clients: dict[str, Client],
    name: str,
) -> None:
    before = _config_files()

    response = _post_named_pipeline(clients["user"], name)

    assert response.status_code == 400
    assert response.json() == INVALID_NAME
    assert _config_files() == before
    assert not Pipeline.objects.filter(name=name).exists()


@pytest.mark.django_db
@pytest.mark.usefixtures("patched_lru_cache")
def test_create_with_an_absolute_name_is_refused(
    clients: dict[str, Client],
) -> None:
    target = pathlib.Path(settings.DATA_STORAGE_DIR, "outside")

    response = _post_named_pipeline(clients["user"], str(target))

    assert response.status_code == 400
    assert response.json() == INVALID_NAME
    assert not target.with_suffix(".yaml").exists()
    assert not Pipeline.objects.filter(name=str(target)).exists()


@pytest.mark.django_db
@pytest.mark.usefixtures("patched_lru_cache")
@pytest.mark.parametrize("name", ["..", ".hidden"])
def test_create_with_a_dot_name_is_refused(
    clients: dict[str, Client],
    name: str,
) -> None:
    before = _config_files()

    response = _post_named_pipeline(clients["user"], name)

    assert response.status_code == 400
    assert response.json() == INVALID_NAME
    assert _config_files() == before
    assert not Pipeline.objects.filter(name=name).exists()


@pytest.mark.django_db
@pytest.mark.usefixtures("patched_lru_cache")
@pytest.mark.parametrize("name", ["v1..2", "my pipeline"])
def test_create_with_a_safe_name_saves_the_pipeline(
    clients: dict[str, Client],
    name: str,
) -> None:
    response = _post_named_pipeline(clients["user"], name)

    assert response.status_code == 200
    assert pathlib.Path(
        settings.ANNOTATION_CONFIG_STORAGE_DIR,
        "user@example.com",
        f"{name}.yaml",
    ).is_file()


@pytest.mark.django_db
@pytest.mark.usefixtures("patched_lru_cache")
def test_create_refuses_a_path_outside_the_user_directory(
    clients: dict[str, Client],
    mocker: MockerFixture,
) -> None:
    mocker.patch(
        "web_annotation.pipelines.views.pipeline_name_error",
        return_value=None,
    )

    response = _post_named_pipeline(clients["user"], "../x")

    assert response.status_code == 400
    assert response.json() == INVALID_NAME
    assert not pathlib.Path(
        settings.ANNOTATION_CONFIG_STORAGE_DIR, "x.yaml",
    ).exists()
    assert not Pipeline.objects.filter(name="../x").exists()


@pytest.mark.django_db
@pytest.mark.usefixtures("patched_lru_cache")
def test_create_checks_the_name_before_the_existing_name_query(
    clients: dict[str, Client],
) -> None:
    owner = User.objects.get(email="user@example.com")
    Pipeline.objects.create(
        name="../x", config_path="unused.yaml", owner=owner)

    response = _post_named_pipeline(clients["user"], "../x")

    assert response.status_code == 400
    assert response.json() == INVALID_NAME


@pytest.mark.django_db
@pytest.mark.usefixtures("patched_lru_cache")
def test_create_refuses_a_nul_name_past_the_name_check(
    clients: dict[str, Client],
    mocker: MockerFixture,
) -> None:
    mocker.patch(
        "web_annotation.pipelines.views.pipeline_name_error",
        return_value=None,
    )
    before = _config_files()

    response = _post_named_pipeline(clients["user"], "a\x00b")

    assert response.status_code == 400
    assert response.json() == INVALID_NAME
    assert _config_files() == before


@pytest.mark.django_db
@pytest.mark.usefixtures("patched_lru_cache")
def test_update_of_a_saved_pipeline_with_a_legacy_name_succeeds(
    clients: dict[str, Client],
) -> None:
    client = clients["user"]
    owner = User.objects.get(email="user@example.com")
    user_dir = pathlib.Path(
        settings.ANNOTATION_CONFIG_STORAGE_DIR, owner.identifier)
    user_dir.mkdir(parents=True, exist_ok=True)
    config_path = user_dir / ".draft.yaml"
    config_path.write_text("- position_score: scores/pos1")
    legacy = Pipeline.objects.create(
        name=".draft", config_path=str(config_path), owner=owner)

    response = client.post("/api/pipelines/user", {
        "id": str(legacy.pk),
        "name": ".draft",
        "config": ContentFile("- position_score: scores/pos2"),
    })

    assert response.status_code == 200
    assert config_path.read_text() == "- position_score: scores/pos2"
