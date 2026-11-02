# pylint: disable=W0621,C0116
"""Account emails and the storage paths built from them.

The server names each user's storage folder after the account email. These
tests pin that registration refuses an email that is not a plain folder
name, and that every view which builds a storage path from the folder name
keeps the path inside its configured storage root.

The storage roots sit several levels below ``tmp_path``, so a path that
leaves a root still lands inside ``tmp_path``, where the tests look for it.
"""
import pathlib
import textwrap

import pytest
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client
from pytest_django.fixtures import SettingsWrapper
from pytest_mock import MockerFixture

from web_annotation.annotation_base_view import AnnotationBaseView
from web_annotation.executor import SequentialTaskExecutor
from web_annotation.jobs import views as jobs_views
from web_annotation.models import Job, User, UserWrapper
from web_annotation.storage_paths import UnsafeStoragePathError


@pytest.fixture
def storage_roots(
    settings: SettingsWrapper,
    tmp_path: pathlib.Path,
) -> dict[str, pathlib.Path]:
    base = tmp_path / "level1" / "level2" / "level3"
    roots = {
        "ANNOTATION_CONFIG_STORAGE_DIR": base / "configs",
        "JOB_INPUT_STORAGE_DIR": base / "inputs",
        "JOB_RESULT_STORAGE_DIR": base / "results",
    }
    for name, root in roots.items():
        root.mkdir(parents=True)
        setattr(settings, name, str(root))
    return roots


def _files_outside(
    roots: dict[str, pathlib.Path],
    tmp_path: pathlib.Path,
) -> set[pathlib.Path]:
    """Return the files under ``tmp_path`` that no storage root holds."""
    return {
        path for path in tmp_path.rglob("*")
        if path.is_file()
        and not any(path.is_relative_to(root) for root in roots.values())
    }


def _client_for(email: str) -> Client:
    """Log in a user created directly in the database, past registration."""
    user = User.objects.create_user(email, email, "secret")
    client = Client()
    client.force_login(user)
    return client


@pytest.mark.django_db
@pytest.mark.usefixtures("storage_roots")
@pytest.mark.parametrize("email", [
    "",
    ".",
    "..",
    "../../outside",
    "/abs/dir",
    "a\\b@example.com",
    '"../../x"@example.com',
    "a\x00b@example.com",
])
def test_register_refuses_an_email_that_is_not_a_folder_name(
    client: Client,
    email: str,
) -> None:
    response = client.post(
        "/api/register",
        {"email": email, "password": "secret"},
        content_type="application/json",
    )

    assert response.status_code == 400
    assert "error" in response.json()
    assert not User.objects.filter(email=email).exists()


@pytest.mark.django_db
@pytest.mark.usefixtures("storage_roots")
@pytest.mark.parametrize("email", [".", "..", "../../outside"])
def test_create_user_command_refuses_an_email_that_is_not_a_folder_name(
    email: str,
) -> None:
    with pytest.raises(CommandError):
        call_command("create_user", email, "secret")

    assert not User.objects.filter(email=email).exists()


@pytest.mark.django_db
@pytest.mark.usefixtures("patched_lru_cache")
def test_pipeline_save_keeps_a_relative_folder_inside_the_config_root(
    storage_roots: dict[str, pathlib.Path],
    tmp_path: pathlib.Path,
) -> None:
    client = _client_for("../../outside")
    before = _files_outside(storage_roots, tmp_path)

    response = client.post("/api/pipelines/user", {
        "name": "p",
        "config": ContentFile("- position_score: scores/pos1"),
    })

    assert response.status_code == 400
    assert _files_outside(storage_roots, tmp_path) == before


@pytest.mark.django_db
@pytest.mark.usefixtures("patched_lru_cache", "storage_roots")
def test_pipeline_save_keeps_an_absolute_folder_inside_the_config_root(
    tmp_path: pathlib.Path,
) -> None:
    folder = tmp_path / "abs"
    client = _client_for(str(folder))

    response = client.post("/api/pipelines/user", {
        "name": "p",
        "config": ContentFile("- position_score: scores/pos1"),
    })

    assert response.status_code == 400
    assert not folder.exists()


VCF_INPUT = textwrap.dedent("""
    ##fileformat=VCFv4.1
    ##contig=<ID=chr1>
    #CHROM	POS	ID	REF	ALT	QUAL	FILTER	INFO
    chr1	1	.	C	A	.	.	.
""").strip()

TABULAR_INPUT = textwrap.dedent("""
    chrom	pos	ref	alt
    chr1	1	C	A
""").strip()

JOB_REQUESTS: dict[str, dict[str, object]] = {
    "/api/jobs/annotate_vcf": {
        "data": ("test_input.vcf", VCF_INPUT),
    },
    "/api/jobs/annotate_tabular": {
        "data": ("test_input.tsv", TABULAR_INPUT),
        "separator": "\t",
        "col_chrom": "chrom",
        "col_pos": "pos",
        "col_ref": "ref",
        "col_alt": "alt",
    },
}


def _job_request(endpoint: str) -> dict[str, object]:
    params: dict[str, object] = {"pipeline_id": "pipeline/test_pipeline"}
    for key, value in JOB_REQUESTS[endpoint].items():
        if isinstance(value, tuple):
            name, content = value
            params[key] = ContentFile(content, name)
        else:
            params[key] = value
    return params


@pytest.mark.django_db
@pytest.mark.usefixtures("patched_lru_cache")
@pytest.mark.parametrize("endpoint", sorted(JOB_REQUESTS))
def test_job_keeps_a_relative_folder_inside_the_storage_roots(
    storage_roots: dict[str, pathlib.Path],
    tmp_path: pathlib.Path,
    mocker: MockerFixture,
    endpoint: str,
) -> None:
    mocker.patch(
        "web_annotation.annotation_base_view.AnnotationBaseView.JOB_EXECUTOR",
        new_callable=SequentialTaskExecutor,
    )
    client = _client_for("../../outside")
    before = _files_outside(storage_roots, tmp_path)

    response = client.post(endpoint, _job_request(endpoint))

    assert response.status_code == 400
    assert _files_outside(storage_roots, tmp_path) == before
    assert not Job.objects.filter(owner__email="../../outside").exists()


@pytest.mark.django_db
@pytest.mark.usefixtures("storage_roots")
def test_config_path_refuses_a_folder_outside_the_config_root() -> None:
    user = User.objects.create_user(
        "../../outside", "../../outside", "secret")

    with pytest.raises(UnsafeStoragePathError):
        AnnotationBaseView().get_config_path(7, user)


@pytest.mark.django_db
@pytest.mark.usefixtures("storage_roots")
def test_result_dir_refuses_a_folder_outside_the_result_root() -> None:
    user = User.objects.create_user(
        "../../outside", "../../outside", "secret")

    with pytest.raises(UnsafeStoragePathError):
        AnnotationBaseView().user_result_dir(UserWrapper(user, "session"))


@pytest.mark.django_db
@pytest.mark.usefixtures("patched_lru_cache")
@pytest.mark.parametrize("endpoint", sorted(JOB_REQUESTS))
def test_job_work_dir_is_the_checked_result_folder(
    storage_roots: dict[str, pathlib.Path],
    mocker: MockerFixture,
    endpoint: str,
) -> None:
    mocker.patch(
        "web_annotation.annotation_base_view.AnnotationBaseView.JOB_EXECUTOR",
        new_callable=SequentialTaskExecutor,
    )
    result_dir = mocker.spy(AnnotationBaseView, "user_result_dir")
    build = mocker.spy(jobs_views, "build_annotation_pipeline")
    client = _client_for("worker@example.com")

    response = client.post(endpoint, _job_request(endpoint))

    assert response.status_code == 200
    assert result_dir.call_count == 1
    assert build.call_args.kwargs["work_dir"] == result_dir.spy_return
    assert result_dir.spy_return == \
        storage_roots["JOB_RESULT_STORAGE_DIR"] / "worker@example.com"


@pytest.mark.django_db
def test_cleanup_keeps_the_files_outside_the_storage_roots(
    storage_roots: dict[str, pathlib.Path],
    tmp_path: pathlib.Path,
) -> None:
    planted = tmp_path / "level1" / "level2" / "outside"
    planted.mkdir()
    for filename in ("data-7.vcf", "config-7.yaml"):
        (planted / filename).write_text("kept")
    before = _files_outside(storage_roots, tmp_path)

    AnnotationBaseView()._cleanup(7, "../../outside")

    assert _files_outside(storage_roots, tmp_path) == before


@pytest.mark.django_db
@pytest.mark.usefixtures("patched_lru_cache")
def test_a_registered_user_saves_a_pipeline_and_runs_a_job(
    storage_roots: dict[str, pathlib.Path],
    mocker: MockerFixture,
) -> None:
    mocker.patch(
        "web_annotation.annotation_base_view.AnnotationBaseView.JOB_EXECUTOR",
        new_callable=SequentialTaskExecutor,
    )
    client = Client()
    email = "new.user+tag@example.com"
    registered = client.post(
        "/api/register",
        {"email": email, "password": "secret"},
        content_type="application/json",
    )
    assert registered.status_code == 200
    assert client.login(email=email, password="secret")

    saved = client.post("/api/pipelines/user", {
        "name": "p",
        "config": ContentFile("- position_score: scores/pos1"),
    })
    endpoint = "/api/jobs/annotate_vcf"
    submitted = client.post(endpoint, _job_request(endpoint))

    assert saved.status_code == 200
    assert (storage_roots["ANNOTATION_CONFIG_STORAGE_DIR"] / email
            / "p.yaml").is_file()
    assert submitted.status_code == 200
    job = Job.objects.get(owner__email=email)
    assert pathlib.Path(job.input_path).parent == \
        storage_roots["JOB_INPUT_STORAGE_DIR"] / email
    assert pathlib.Path(job.result_path).is_file()
