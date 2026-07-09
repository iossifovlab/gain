# pylint: disable=C0114,C0116,W0621
import datetime
import pathlib

import pytest
import pytest_mock
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone
from pytest_django.fixtures import SettingsWrapper

from web_annotation.management.commands.cleanup_anonymous_jobs import (
    DEFAULT_TTL_HOURS,
    resolve_ttl_hours,
)
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
def test_one_raising_job_reaps_others_then_signals_failure(
    tmp_path: pathlib.Path,
    mocker: pytest_mock.MockerFixture,
) -> None:
    """A poison row is skipped, the rest reaped, then the run signals failure.

    Per-job error isolation (#216): a failure ``_cleanup_files`` cannot
    pre-empt (e.g. a permission error) must not abort the sweep of healthy
    rows. But the run must still exit non-zero so cron alerting fires -- Django
    turns a ``CommandError`` into a non-zero exit written to stderr.
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

    with pytest.raises(CommandError):
        call_command("cleanup_anonymous_jobs", "--older-than-hours", "24")

    # The healthy row is still reaped despite the poison row; the poison row
    # is left intact for a later run / manual inspection.
    assert AnonymousJob.objects.filter(pk=first_job.pk).exists()
    assert not AnonymousJob.objects.filter(pk=second_job.pk).exists()
    for path in second.values():
        assert not path.exists()


@pytest.mark.django_db
def test_negative_older_than_hours_raises_and_deletes_nothing(
    tmp_path: pathlib.Path,
) -> None:
    """A negative TTL is a footgun (cutoff = now + N deletes everything).

    ``--older-than-hours -1`` makes ``cutoff = now + 1h`` so every terminal
    job matches ``created__lt=cutoff`` regardless of age. The command must
    reject it loudly *before* any deletion (#216).
    """
    paths = _make_files(tmp_path, "safe")
    job = AnonymousJob.objects.create(
        owner="anon_sess1", ip="1.2.3.4",
        status=AnonymousJob.Status.SUCCESS,
        **{key: str(value) for key, value in paths.items()},
    )
    _age_job(job, hours=1000)  # ancient; would be nuked by a negative cutoff

    with pytest.raises(CommandError):
        call_command("cleanup_anonymous_jobs", "--older-than-hours", "-1")

    assert AnonymousJob.objects.filter(pk=job.pk).exists()
    for path in paths.values():
        assert path.exists(), f"{path} deleted despite the negative-TTL guard"


@pytest.mark.django_db
def test_negative_ttl_setting_raises(
    tmp_path: pathlib.Path,
    settings: SettingsWrapper,
) -> None:
    """A negative resolved *setting* is rejected the same way as the CLI arg."""
    settings.ANONYMOUS_JOB_TTL_HOURS = -5
    paths = _make_files(tmp_path, "safe-setting")
    job = AnonymousJob.objects.create(
        owner="anon_sess1", ip="1.2.3.4",
        status=AnonymousJob.Status.SUCCESS,
        **{key: str(value) for key, value in paths.items()},
    )
    _age_job(job, hours=1000)

    with pytest.raises(CommandError):
        call_command("cleanup_anonymous_jobs")

    assert AnonymousJob.objects.filter(pk=job.pk).exists()


@pytest.mark.django_db
def test_ttl_zero_flushes_terminal_job(
    tmp_path: pathlib.Path,
) -> None:
    """``--older-than-hours 0`` means flush all currently-terminal jobs now."""
    paths = _make_files(tmp_path, "flush")
    job = AnonymousJob.objects.create(
        owner="anon_sess1", ip="1.2.3.4",
        status=AnonymousJob.Status.SUCCESS,
        **{key: str(value) for key, value in paths.items()},
    )
    _age_job(job, hours=1)  # just completed

    call_command("cleanup_anonymous_jobs", "--older-than-hours", "0")

    assert not AnonymousJob.objects.filter(pk=job.pk).exists()
    for path in paths.values():
        assert not path.exists()


def test_resolve_ttl_hours_falls_back_on_garbage() -> None:
    """The TTL coercion helper never raises on a non-integer value (#216)."""
    assert resolve_ttl_hours("48") == 48
    assert resolve_ttl_hours(48) == 48
    assert resolve_ttl_hours("garbage") == DEFAULT_TTL_HOURS
    assert resolve_ttl_hours(None) == DEFAULT_TTL_HOURS


@pytest.mark.django_db
def test_non_int_setting_falls_back_to_default(
    tmp_path: pathlib.Path,
    settings: SettingsWrapper,
) -> None:
    """A garbage ANONYMOUS_JOB_TTL_HOURS must not crash the janitor.

    A non-numeric ``GPFWA_ANONYMOUS_JOB_TTL_HOURS`` env var that slipped past
    settings parsing must not take down the command; it falls back to the
    24h default (#216). Verified through the public reap boundary: a 25h job
    is reaped and a 23h job survives.
    """
    settings.ANONYMOUS_JOB_TTL_HOURS = "not-a-number"
    old = _make_files(tmp_path, "old")
    recent = _make_files(tmp_path, "recent")
    old_job = AnonymousJob.objects.create(
        owner="anon_sess1", ip="1.2.3.4",
        status=AnonymousJob.Status.SUCCESS,
        **{key: str(value) for key, value in old.items()},
    )
    recent_job = AnonymousJob.objects.create(
        owner="anon_sess1", ip="1.2.3.4",
        status=AnonymousJob.Status.SUCCESS,
        **{key: str(value) for key, value in recent.items()},
    )
    _age_job(old_job, hours=25)
    _age_job(recent_job, hours=23)

    call_command("cleanup_anonymous_jobs")  # no --older-than-hours

    assert not AnonymousJob.objects.filter(pk=old_job.pk).exists()
    assert AnonymousJob.objects.filter(pk=recent_job.pk).exists()
