# pylint: disable=C0114,C0116,W0621
import pathlib

import pytest
from asgiref.sync import sync_to_async
from django.test import Client

from web_annotation.consumers import AnnotationStateConsumer
from web_annotation.models import (
    AnonymousJob,
    Job,
    User,
    WebAnnotationAnonymousUser,
)
from web_annotation.testing import CustomWebsocketCommunicator


def _make_files(tmp_path: pathlib.Path, tag: str) -> dict[str, pathlib.Path]:
    paths = {
        "input_path": tmp_path / f"input-{tag}.vcf",
        "config_path": tmp_path / f"config-{tag}.yaml",
        "result_path": tmp_path / f"result-{tag}.vcf",
    }
    for path in paths.values():
        path.write_text("mock data")
    return paths


@pytest.mark.django_db
def test_delete_jobs_preserves_in_progress_anonymous_job(
    tmp_path: pathlib.Path,
) -> None:
    user = WebAnnotationAnonymousUser(session_id="sess1", ip="1.2.3.4")
    paths = _make_files(tmp_path, "running")
    job = AnonymousJob.objects.create(
        owner=user.identifier,
        ip=user.ip,
        status=AnonymousJob.Status.IN_PROGRESS,
        **{key: str(value) for key, value in paths.items()},
    )

    user.delete_jobs()

    assert AnonymousJob.objects.filter(pk=job.pk).exists()
    for path in paths.values():
        assert path.exists(), f"{path} was deleted out from under a running job"


@pytest.mark.django_db
def test_delete_jobs_preserves_waiting_anonymous_job(
    tmp_path: pathlib.Path,
) -> None:
    user = WebAnnotationAnonymousUser(session_id="sess1", ip="1.2.3.4")
    paths = _make_files(tmp_path, "queued")
    job = AnonymousJob.objects.create(
        owner=user.identifier,
        ip=user.ip,
        status=AnonymousJob.Status.WAITING,
        **{key: str(value) for key, value in paths.items()},
    )

    user.delete_jobs()

    assert AnonymousJob.objects.filter(pk=job.pk).exists()
    for path in paths.values():
        assert path.exists(), f"{path} was deleted out from under a queued job"


@pytest.mark.django_db
@pytest.mark.parametrize(
    "status",
    [AnonymousJob.Status.SUCCESS, AnonymousJob.Status.FAILED],
)
def test_delete_jobs_removes_terminal_anonymous_job(
    tmp_path: pathlib.Path,
    status: int,
) -> None:
    user = WebAnnotationAnonymousUser(session_id="sess1", ip="1.2.3.4")
    paths = _make_files(tmp_path, "done")
    job = AnonymousJob.objects.create(
        owner=user.identifier,
        ip=user.ip,
        status=status,
        **{key: str(value) for key, value in paths.items()},
    )

    user.delete_jobs()

    assert not AnonymousJob.objects.filter(pk=job.pk).exists()
    for path in paths.values():
        assert not path.exists(), f"{path} not cleaned up for finished job"


@pytest.mark.django_db
def test_delete_jobs_only_spares_active_jobs_of_the_same_user(
    tmp_path: pathlib.Path,
) -> None:
    user = WebAnnotationAnonymousUser(session_id="sess1", ip="1.2.3.4")
    running = _make_files(tmp_path, "running")
    finished = _make_files(tmp_path, "finished")
    running_job = AnonymousJob.objects.create(
        owner=user.identifier, ip=user.ip,
        status=AnonymousJob.Status.IN_PROGRESS,
        **{key: str(value) for key, value in running.items()},
    )
    finished_job = AnonymousJob.objects.create(
        owner=user.identifier, ip=user.ip,
        status=AnonymousJob.Status.SUCCESS,
        **{key: str(value) for key, value in finished.items()},
    )

    user.delete_jobs()

    assert AnonymousJob.objects.filter(pk=running_job.pk).exists()
    assert not AnonymousJob.objects.filter(pk=finished_job.pk).exists()


@pytest.mark.django_db
def test_delete_jobs_preserves_in_progress_authenticated_job(
    tmp_path: pathlib.Path,
) -> None:
    user = User.objects.get(email="user@example.com")
    paths = _make_files(tmp_path, "running")
    job = Job.objects.create(
        owner=user,
        status=Job.Status.IN_PROGRESS,
        **{key: str(value) for key, value in paths.items()},
    )

    user.delete_jobs()

    job.refresh_from_db()
    assert job.is_active
    for path in paths.values():
        assert path.exists(), f"{path} was deleted out from under a running job"


@pytest.mark.django_db
def test_delete_jobs_deactivates_terminal_authenticated_job(
    tmp_path: pathlib.Path,
) -> None:
    user = User.objects.get(email="user@example.com")
    paths = _make_files(tmp_path, "done")
    job = Job.objects.create(
        owner=user,
        status=Job.Status.SUCCESS,
        **{key: str(value) for key, value in paths.items()},
    )

    user.delete_jobs()

    job.refresh_from_db()
    assert not job.is_active
    for path in paths.values():
        assert not path.exists(), f"{path} not cleaned up for finished job"


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_websocket_disconnect_spares_running_anonymous_job(
    anonymous_client: Client,
    tmp_path: pathlib.Path,
) -> None:
    """The literal #147 scenario, end to end through the consumer.

    An anonymous user with an in-flight job loses their last WebSocket; the
    consumer's ``disconnect`` cleanup must not delete the running job's files.
    """
    session = await anonymous_client.asession()
    assert session.session_key is not None
    user = WebAnnotationAnonymousUser(session.session_key, ip="test")

    paths = _make_files(tmp_path, "running")
    await sync_to_async(AnonymousJob.objects.create)(
        owner=user.identifier,
        ip=user.ip,
        status=AnonymousJob.Status.IN_PROGRESS,
        **{key: str(value) for key, value in paths.items()},
    )

    communicator = CustomWebsocketCommunicator(
        AnnotationStateConsumer.as_asgi(),
        "/ws/test/", user=user, session=session,
    )
    connected, _ = await communicator.connect(timeout=1000)
    assert connected

    await communicator.disconnect(timeout=1000)

    assert await sync_to_async(user.job_class.objects.count)() == 1
    for path in paths.values():
        assert path.exists(), (
            f"{path} was deleted by the WS disconnect while the job was running"
        )
