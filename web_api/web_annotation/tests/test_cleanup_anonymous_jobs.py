# pylint: disable=C0114,C0116,W0621
import datetime
import pathlib

import pytest
from django.core.management import call_command
from django.utils import timezone
from pytest_django.fixtures import SettingsWrapper

from web_annotation.models import (
    AnonymousJob,
    Job,
    User,
)


def _make_files(tmp_path: pathlib.Path, tag: str) -> dict[str, pathlib.Path]:
    paths = {
        "input_path": tmp_path / f"input-{tag}.vcf",
        "config_path": tmp_path / f"config-{tag}.yaml",
        "result_path": tmp_path / f"result-{tag}.vcf",
    }
    for path in paths.values():
        path.write_text("mock data")
    return paths


def _age_job(job: AnonymousJob, hours: float) -> None:
    """Backdate a job's ``created`` (default=timezone.now) to ``hours`` ago."""
    AnonymousJob.objects.filter(pk=job.pk).update(
        created=timezone.now() - datetime.timedelta(hours=hours),
    )


@pytest.mark.django_db
def test_old_terminal_job_deleted_with_files(
    tmp_path: pathlib.Path,
) -> None:
    paths = _make_files(tmp_path, "old")
    job = AnonymousJob.objects.create(
        owner="anon_sess1", ip="1.2.3.4",
        status=AnonymousJob.Status.SUCCESS,
        **{key: str(value) for key, value in paths.items()},
    )
    _age_job(job, hours=48)

    call_command("cleanup_anonymous_jobs", "--older-than-hours", "24")

    assert not AnonymousJob.objects.filter(pk=job.pk).exists()
    for path in paths.values():
        assert not path.exists(), f"{path} not cleaned up for old job"


@pytest.mark.django_db
def test_recent_terminal_job_preserved(
    tmp_path: pathlib.Path,
) -> None:
    paths = _make_files(tmp_path, "recent")
    job = AnonymousJob.objects.create(
        owner="anon_sess1", ip="1.2.3.4",
        status=AnonymousJob.Status.SUCCESS,
        **{key: str(value) for key, value in paths.items()},
    )
    _age_job(job, hours=1)

    call_command("cleanup_anonymous_jobs", "--older-than-hours", "24")

    assert AnonymousJob.objects.filter(pk=job.pk).exists()
    for path in paths.values():
        assert path.exists(), f"{path} deleted for a recent job"


@pytest.mark.django_db
@pytest.mark.parametrize(
    "status",
    [AnonymousJob.Status.WAITING, AnonymousJob.Status.IN_PROGRESS],
)
def test_active_job_preserved_regardless_of_age(
    tmp_path: pathlib.Path,
    status: int,
) -> None:
    paths = _make_files(tmp_path, "active")
    job = AnonymousJob.objects.create(
        owner="anon_sess1", ip="1.2.3.4",
        status=status,
        **{key: str(value) for key, value in paths.items()},
    )
    _age_job(job, hours=1000)

    call_command("cleanup_anonymous_jobs", "--older-than-hours", "24")

    assert AnonymousJob.objects.filter(pk=job.pk).exists()
    for path in paths.values():
        assert path.exists(), (
            f"{path} deleted out from under an active job regardless of age"
        )


@pytest.mark.django_db
def test_ttl_boundary(
    tmp_path: pathlib.Path,
) -> None:
    """A job exactly at the TTL boundary is preserved; older is deleted."""
    just_inside = _make_files(tmp_path, "inside")
    just_outside = _make_files(tmp_path, "outside")
    inside_job = AnonymousJob.objects.create(
        owner="anon_sess1", ip="1.2.3.4",
        status=AnonymousJob.Status.SUCCESS,
        **{key: str(value) for key, value in just_inside.items()},
    )
    outside_job = AnonymousJob.objects.create(
        owner="anon_sess1", ip="1.2.3.4",
        status=AnonymousJob.Status.SUCCESS,
        **{key: str(value) for key, value in just_outside.items()},
    )
    _age_job(inside_job, hours=23)
    _age_job(outside_job, hours=25)

    call_command("cleanup_anonymous_jobs", "--older-than-hours", "24")

    assert AnonymousJob.objects.filter(pk=inside_job.pk).exists()
    assert not AnonymousJob.objects.filter(pk=outside_job.pk).exists()


@pytest.mark.django_db
def test_ttl_defaults_to_setting(
    tmp_path: pathlib.Path,
    settings: SettingsWrapper,
) -> None:
    settings.ANONYMOUS_JOB_TTL_HOURS = 10
    paths = _make_files(tmp_path, "setting")
    job = AnonymousJob.objects.create(
        owner="anon_sess1", ip="1.2.3.4",
        status=AnonymousJob.Status.SUCCESS,
        **{key: str(value) for key, value in paths.items()},
    )
    _age_job(job, hours=12)

    call_command("cleanup_anonymous_jobs")

    assert not AnonymousJob.objects.filter(pk=job.pk).exists()


@pytest.mark.django_db
def test_authenticated_jobs_untouched(
    tmp_path: pathlib.Path,
) -> None:
    owner = User.objects.get(email="user@example.com")
    paths = _make_files(tmp_path, "auth")
    job = Job.objects.create(
        owner=owner,
        status=Job.Status.SUCCESS,
        **{key: str(value) for key, value in paths.items()},
    )
    Job.objects.filter(pk=job.pk).update(
        created=timezone.now() - datetime.timedelta(hours=1000),
    )

    call_command("cleanup_anonymous_jobs", "--older-than-hours", "24")

    assert Job.objects.filter(pk=job.pk).exists()
    for path in paths.values():
        assert path.exists(), f"{path} deleted for an authenticated Job"
