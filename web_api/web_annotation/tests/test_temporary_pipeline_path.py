# pylint: disable=W0621,C0116
"""``POST /api/pipelines/user`` stores the temporary pipeline of a session.

The temporary pipeline of a session lives at
``temporary/pipeline-<session_id>.yaml`` under the annotation config root.
The ``name`` field of the request never reaches that path.
"""
import pathlib

import pytest
from django.conf import settings
from django.core.files.base import ContentFile
from django.test import Client

from web_annotation.models import TemporaryPipeline

ESCAPE = "../../cq89-escape"
CONFIG = "- position_score: scores/pos1"


def _all_files() -> set[pathlib.Path]:
    root = pathlib.Path(settings.ANNOTATION_CONFIG_STORAGE_DIR).parent
    return {p.resolve() for p in root.rglob("*") if p.is_file()}


def _session_id(client: Client) -> str:
    """Materialise the session of ``client`` and return its key."""
    client.get("/api/pipelines/user?id=1")
    session_key = client.session.session_key
    assert session_key is not None
    return session_key


def _temporary_path(session_id: str) -> pathlib.Path:
    return pathlib.Path(
        settings.ANNOTATION_CONFIG_STORAGE_DIR,
        "temporary",
        f"pipeline-{session_id}.yaml",
    ).resolve()


@pytest.mark.django_db
@pytest.mark.usefixtures("patched_lru_cache")
@pytest.mark.parametrize("caller", ["anonymous", "user"])
def test_first_temporary_save_writes_one_yaml_suffix(
    clients: dict[str, Client],
    caller: str,
) -> None:
    client = clients[caller]
    session_id = _session_id(client)
    before = _all_files()

    response = client.post(
        "/api/pipelines/user", {"config": ContentFile(CONFIG)})

    assert response.status_code == 200
    assert _all_files() - before == {_temporary_path(session_id)}
    row = TemporaryPipeline.objects.get(session_id=session_id)
    assert row.name == f"pipeline-{session_id}.yaml"
    assert pathlib.Path(str(row.config_path)).resolve() == \
        _temporary_path(session_id)


@pytest.mark.django_db
@pytest.mark.usefixtures("patched_lru_cache")
@pytest.mark.parametrize("caller", ["anonymous", "user"])
def test_temporary_save_writes_the_stored_config_path_of_the_row(
    clients: dict[str, Client],
    caller: str,
) -> None:
    client = clients[caller]
    session_id = _session_id(client)
    stored_path = pathlib.Path(
        settings.ANNOTATION_CONFIG_STORAGE_DIR,
        "temporary",
        f"pipeline-{session_id}.yaml.yaml",
    ).resolve()
    TemporaryPipeline.objects.create(
        session_id=session_id,
        name=f"pipeline-{session_id}.yaml",
        config_path=str(stored_path),
    )
    before = _all_files()

    response = client.post(
        "/api/pipelines/user", {"config": ContentFile(CONFIG)})

    assert response.status_code == 200
    assert _all_files() - before == {stored_path}
    assert stored_path.read_text() == CONFIG


def _post_escape_name(
    client: Client, pipeline_id: str | None,
) -> int:
    data: dict[str, object] = {
        "name": ESCAPE,
        "config": ContentFile(CONFIG),
    }
    if pipeline_id is not None:
        data["id"] = pipeline_id
    return client.post("/api/pipelines/user", data).status_code


@pytest.mark.django_db
@pytest.mark.usefixtures("patched_lru_cache")
@pytest.mark.parametrize("caller", ["anonymous", "user"])
@pytest.mark.parametrize("pipeline_id", [None, ""])
def test_escape_name_without_id_is_refused_and_writes_nothing(
    clients: dict[str, Client],
    caller: str,
    pipeline_id: str | None,
) -> None:
    client = clients[caller]
    _session_id(client)
    before = _all_files()

    status = _post_escape_name(client, pipeline_id)

    assert _all_files() - before == set()
    assert status == 400


@pytest.mark.django_db
@pytest.mark.usefixtures("patched_lru_cache")
@pytest.mark.parametrize("caller", ["anonymous", "user"])
def test_escape_name_with_session_id_writes_only_the_temporary_file(
    clients: dict[str, Client],
    caller: str,
) -> None:
    client = clients[caller]
    session_id = _session_id(client)
    first = client.post(
        "/api/pipelines/user", {"config": ContentFile(CONFIG)})
    assert first.status_code == 200
    before = _all_files()

    _post_escape_name(client, session_id)

    assert _all_files() - before <= {_temporary_path(session_id)}
    assert _temporary_path(session_id) in _all_files()
