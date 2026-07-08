# pylint: disable=C0114,C0116,W0621
import datetime
import pathlib

import pytest
import pytest_mock
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
    """A job younger than the TTL is preserved; an older one is deleted.

    The cutoff is ``now - ttl`` and the filter is ``created__lt=cutoff``, so a
    job aged just under the TTL (23h vs 24h) is kept and one just over (25h)
    is reaped.
    """
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


@pytest.mark.django_db
def test_cleanup_files_missing_file_does_not_raise(
    tmp_path: pathlib.Path,
) -> None:
    """delete() on a job whose input file is already gone must not raise.

    A cleanup interrupted between the unlink and the DB row delete leaves a
    row whose input/config file no longer exists. ``_cleanup_files`` must be
    idempotent w.r.t. disk state (#216).
    """
    paths = _make_files(tmp_path, "partial")
    job = AnonymousJob.objects.create(
        owner="anon_sess1", ip="1.2.3.4",
        status=AnonymousJob.Status.SUCCESS,
        **{key: str(value) for key, value in paths.items()},
    )
    paths["input_path"].unlink()  # simulate an interrupted prior cleanup

    job.delete()  # must not raise FileNotFoundError

    assert not AnonymousJob.objects.filter(pk=job.pk).exists()
    for path in paths.values():
        assert not path.exists()


@pytest.mark.django_db
def test_missing_input_file_does_not_wedge_janitor(
    tmp_path: pathlib.Path,
) -> None:
    """One old job missing its input file must not block reaping the rest.

    Regression for the wedge (#216): a ``FileNotFoundError`` from the first
    row would previously abort ``handle()``, re-selecting the same bad row on
    every cron run and reaping nothing.
    """
    broken = _make_files(tmp_path, "broken")
    healthy = _make_files(tmp_path, "healthy")
    broken_job = AnonymousJob.objects.create(
        owner="anon_sess1", ip="1.2.3.4",
        status=AnonymousJob.Status.SUCCESS,
        **{key: str(value) for key, value in broken.items()},
    )
    healthy_job = AnonymousJob.objects.create(
        owner="anon_sess1", ip="1.2.3.4",
        status=AnonymousJob.Status.SUCCESS,
        **{key: str(value) for key, value in healthy.items()},
    )
    _age_job(broken_job, hours=48)
    _age_job(healthy_job, hours=48)
    broken["input_path"].unlink()  # interrupted prior cleanup

    call_command("cleanup_anonymous_jobs", "--older-than-hours", "24")

    # The healthy job's row and files are reaped ...
    assert not AnonymousJob.objects.filter(pk=healthy_job.pk).exists()
    for path in healthy.values():
        assert not path.exists()
    # ... and the broken row is eventually reaped too (files idempotently).
    assert not AnonymousJob.objects.filter(pk=broken_job.pk).exists()
    for path in broken.values():
        assert not path.exists()


@pytest.mark.django_db
def test_one_raising_job_does_not_block_others(
    tmp_path: pathlib.Path,
    mocker: pytest_mock.MockerFixture,
) -> None:
    """Per-job error isolation: a job whose delete() raises is skipped.

    Even for a failure ``_cleanup_files`` cannot pre-empt (e.g. a permission
    error), the janitor's try/except must let the loop reap the other rows
    instead of aborting ``handle()`` (#216).
    """
    first = _make_files(tmp_path, "first")
    second = _make_files(tmp_path, "second")
    first_job = AnonymousJob.objects.create(
        owner="anon_sess1", ip="1.2.3.4",
        status=AnonymousJob.Status.SUCCESS,
        **{key: str(value) for key, value in first.items()},
    )
    second_job = AnonymousJob.objects.create(
        owner="anon_sess1", ip="1.2.3.4",
        status=AnonymousJob.Status.SUCCESS,
        **{key: str(value) for key, value in second.items()},
    )
    _age_job(first_job, hours=48)
    _age_job(second_job, hours=48)

    real_delete = AnonymousJob.delete

    def flaky_delete(
        self: AnonymousJob, *args: object, **kwargs: object,
    ) -> object:
        if self.pk == first_job.pk:
            raise PermissionError("cannot unlink")
        return real_delete(self, *args, **kwargs)

    mocker.patch.object(AnonymousJob, "delete", flaky_delete)

    # Must not raise despite the first job blowing up.
    call_command("cleanup_anonymous_jobs", "--older-than-hours", "24")

    # The raising job is left intact; the other is still reaped.
    assert AnonymousJob.objects.filter(pk=first_job.pk).exists()
    assert not AnonymousJob.objects.filter(pk=second_job.pk).exists()
