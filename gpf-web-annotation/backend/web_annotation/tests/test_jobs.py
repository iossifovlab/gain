# pylint: disable=W0621,C0114,C0116,W0212,W0613
import datetime
import gzip
import pathlib
import textwrap
from typing import Any
from unittest.mock import MagicMock
from asgiref.sync import sync_to_async
from pysam import tabix_compress

import pytest
import pytest_mock
from gain.genomic_resources.repository import GenomicResourceRepo
from django.conf import LazySettings, settings
from django.core.files.base import ContentFile
from django.test import Client
from django.utils import timezone
from pytest_mock import MockerFixture

from web_annotation.consumers import AnnotationStateConsumer
from web_annotation.executor import SequentialTaskExecutor
from web_annotation.pipeline_cache import LRUPipelineCache
from web_annotation.models import (
    AnonymousJob,
    Job,
    TemporaryPipeline,
    User,
    Pipeline,
    WebAnnotationAnonymousUser,
)
from web_annotation.mail import send_email
from web_annotation.tasks import clean_old_jobs
from web_annotation.testing import CustomWebsocketCommunicator
from web_annotation.tests.mailhog_client import (
    MailhogClient,
)


@pytest.fixture(autouse=True)
def sequential_task_executor(
    mocker: pytest_mock.MockerFixture,
) -> None:
    mocker.patch(
        "web_annotation.annotation_base_view.AnnotationBaseView.JOB_EXECUTOR",
        new_callable=SequentialTaskExecutor,
    )


@pytest.fixture
def mock_lru_cache(
    mocker: pytest_mock.MockerFixture,
    test_grr: GenomicResourceRepo,
) -> LRUPipelineCache:
    cache = LRUPipelineCache(test_grr, 16)
    mocker.patch(
        "web_annotation.annotation_base_view"
        ".AnnotationBaseView.lru_cache",
        new=cache,
    )
    return cache


@pytest.mark.django_db
def test_job_update_new() -> None:
    test_job = Job(
        owner_id=1,
        input_path="input", config_path="config", result_path="result",
    )

    assert test_job.status == Job.Status.WAITING
    with pytest.raises(ValueError):
        test_job.update_job_failed("", "")
    with pytest.raises(ValueError):
        test_job.update_job_success("")

    assert test_job.status == Job.Status.WAITING
    test_job.update_job_in_progress()
    assert test_job.status == Job.Status.IN_PROGRESS


@pytest.mark.django_db
def test_job_update_in_progress() -> None:
    test_job = Job(
        owner_id=1,
        input_path="input", config_path="config", result_path="result",
        status=Job.Status.IN_PROGRESS,
    )

    with pytest.raises(ValueError):
        test_job.update_job_in_progress()

    assert test_job.status == Job.Status.IN_PROGRESS

    test_job.update_job_success("")
    assert test_job.status == Job.Status.SUCCESS

    test_job.status = Job.Status.IN_PROGRESS

    test_job.update_job_failed("", "")

    assert test_job.status == Job.Status.FAILED


@pytest.mark.django_db
def test_job_update_failed() -> None:
    test_job = Job(
        owner_id=1,
        input_path="input", config_path="config", result_path="result",
        status=Job.Status.FAILED,
    )
    with pytest.raises(ValueError):
        test_job.update_job_in_progress()
    with pytest.raises(ValueError):
        test_job.update_job_failed("", "")
    with pytest.raises(ValueError):
        test_job.update_job_success("")

    assert test_job.status == Job.Status.FAILED


@pytest.mark.django_db
def test_job_update_success() -> None:
    test_job = Job(
        owner_id=1,
        input_path="input", config_path="config", result_path="result",
        status=Job.Status.SUCCESS,
    )
    with pytest.raises(ValueError):
        test_job.update_job_in_progress()
    with pytest.raises(ValueError):
        test_job.update_job_failed("", "")
    with pytest.raises(ValueError):
        test_job.update_job_success("")

    assert test_job.status == Job.Status.SUCCESS


@pytest.mark.django_db
def test_send_email(mail_client: MailhogClient) -> None:
    email_result = send_email(
        "TEST SUBJECT",
        "TEST MESSAGE",
        ["recipient1@mail.com", "recipient2@mail.com"],
        "sender@mail.com"
    )

    assert email_result == 1


@pytest.mark.django_db
def test_job_failure_starts_email_task(
    monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    mocked = MagicMock()
    monkeypatch.setattr("web_annotation.models.send_email", mocked)

    assert mocked.call_count == 0

    test_job = Job(
        owner_id=1,
        input_path="input", config_path="config", result_path="result",
        status=Job.Status.IN_PROGRESS,
    )
    test_job.update_job_failed("", "")

    subject, message, recipient = mocked.call_args_list[0].args
    assert "GPFWA" in subject
    assert "try running it again: http://testserver//jobs" in message
    assert ['user@example.com'] == recipient


@pytest.mark.django_db
def test_job_success_starts_email_task(
    monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    mocked = MagicMock()
    monkeypatch.setattr("web_annotation.models.send_email", mocked)

    assert mocked.call_count == 0

    test_job = Job(
        owner_id=1,
        input_path="input", config_path="config", result_path="result",
        status=Job.Status.IN_PROGRESS,
    )

    test_job.update_job_success("")

    subject, message, recipient = mocked.call_args_list[0].args
    assert "GPFWA" in subject
    assert "results: http://testserver//jobs" in message
    assert ['user@example.com'] == recipient


@pytest.mark.django_db
def test_clean_old_jobs_removes_old_jobs(
    tmp_path: pathlib.Path,
) -> None:
    user = User.objects.get(email="user@example.com")

    # Create two jobs with old creation date
    for i in [1, 2]:
        user_input = tmp_path / f"user-input{i}.vcf"
        user_input.write_text("mock vcf data")
        user_config = tmp_path / f"user-config{i}.yaml"
        user_config.write_text("mock annotation config")
        user_result = tmp_path / f"user-result{i}.vcf"
        user_result.write_text("mock annotated vcf")
        job = Job(
            input_path=user_input,
            config_path=user_config,
            result_path=user_result,
            owner=user,
            created=timezone.now() - datetime.timedelta(days=10),
        )
        job.created = timezone.now() - datetime.timedelta(days=10)
        job.save()

    assert Job.objects.filter(is_active=True, owner=user).count() == 3

    clean_old_jobs()

    # Old jobs should be inactive and files deleted
    old_jobs = Job.objects.filter(is_active=False, owner=user)
    assert old_jobs.count() == 2

    assert not pathlib.Path(old_jobs[0].input_path).exists()
    assert not pathlib.Path(old_jobs[0].config_path).exists()
    assert not pathlib.Path(old_jobs[0].result_path).exists()
    assert not pathlib.Path(old_jobs[1].input_path).exists()
    assert not pathlib.Path(old_jobs[1].config_path).exists()
    assert not pathlib.Path(old_jobs[1].result_path).exists()

    recent_job = Job.objects.filter(is_active=True, owner=user).get()
    assert pathlib.Path(recent_job.input_path).exists()
    assert pathlib.Path(recent_job.config_path).exists()
    assert pathlib.Path(recent_job.result_path).exists()


@pytest.mark.django_db
def test_annotate_vcf(
    user_client: Client, test_grr: GenomicResourceRepo,
) -> None:
    user = User.objects.get(email="user@example.com")

    annotation_config = textwrap.dedent("""
        - position_score:
            resource_id: scores/pos1
            attributes:
            - name: position_1
              source: pos1
    """).lstrip()
    vcf = textwrap.dedent("""
        ##fileformat=VCFv4.1
        ##contig=<ID=chr1>
        #CHROM	POS	ID	REF	ALT	QUAL	FILTER	INFO
        chr1	1	.	C	A	.	.	.
    """).strip()

    response = user_client.post(
        "/api/jobs/annotate_vcf",
        {
            "pipeline_id": "pipeline/test_pipeline",
            "data": ContentFile(vcf, "test_input.vcf"),
        },
    )
    assert response.status_code == 200

    assert Job.objects.filter(owner=user).count() == 2

    job = Job.objects.last()

    assert job is not None

    saved_input = pathlib.Path(job.input_path)

    assert job.duration is not None
    assert job.duration < 7.0
    assert saved_input.exists()
    assert saved_input.read_text(encoding="utf-8") == vcf

    saved_config = pathlib.Path(job.config_path)
    assert saved_config.exists()
    assert saved_config.read_text(encoding="utf-8") == annotation_config

    result_path = pathlib.Path(job.result_path)
    assert result_path.parent == \
        pathlib.Path(settings.JOB_RESULT_STORAGE_DIR) / user.email
    assert result_path.exists()
    assert job.result_path.endswith(".vcf") is True


@pytest.mark.django_db
def test_annotate_vcf_anonymous_user(
    anonymous_client: Client, test_grr: GenomicResourceRepo,
) -> None:
    annotation_config = textwrap.dedent("""
        - position_score:
            resource_id: scores/pos1
            attributes:
            - name: position_1
              source: pos1
    """).lstrip()
    vcf = textwrap.dedent("""
        ##fileformat=VCFv4.1
        ##contig=<ID=chr1>
        #CHROM	POS	ID	REF	ALT	QUAL	FILTER	INFO
        chr1	1	.	C	A	.	.	.
    """).strip()

    assert AnonymousJob.objects.count() == 0

    response = anonymous_client.post(
        "/api/jobs/annotate_vcf",
        {
            "pipeline_id": "pipeline/test_pipeline",
            "data": ContentFile(vcf, "test_input.vcf"),
        },
    )
    assert response.status_code == 200

    assert AnonymousJob.objects.count() == 1

    job = AnonymousJob.objects.last()

    assert job is not None

    saved_input = pathlib.Path(job.input_path)

    assert job.duration is not None
    assert job.duration < 7.0
    assert saved_input.exists()
    assert saved_input.read_text(encoding="utf-8") == vcf

    saved_config = pathlib.Path(job.config_path)
    assert saved_config.exists()
    assert saved_config.read_text(encoding="utf-8") == annotation_config

    result_path = pathlib.Path(job.result_path)
    assert result_path.exists()
    assert job.result_path.endswith(".vcf") is True


@pytest.mark.django_db
def test_annotate_vcf_bad_input_data(user_client: Client) -> None:
    user = User.objects.get(email="user@example.com")

    assert Job.objects.filter(owner=user).count() == 1

    with open(str(
            pathlib.Path(__file__).parent /
            "fixtures" / "GIMP_Pepper.png"), "rb") as image:
        raw_img = image.read()

    assert len(Job.objects.all()) == 2
    response = user_client.post(
        "/api/jobs/annotate_vcf",
        {
            "pipeline_id": "pipeline/test_pipeline",
            "data": ContentFile(raw_img)
         },
    )
    assert len(Job.objects.all()) == 2
    assert response.status_code == 400


@pytest.mark.django_db
def test_annotate_vcf_non_vcf_input_data(user_client: Client) -> None:
    user = User.objects.get(email="user@example.com")

    assert Job.objects.filter(owner=user).count() == 1

    assert len(Job.objects.all()) == 2
    response = user_client.post(
        "/api/jobs/annotate_vcf",
        {
            "pipeline_id": "pipeline/test_pipeline",
            "data": ContentFile("blabla random text")
         },
    )
    assert len(Job.objects.all()) == 2
    assert response.status_code == 400


@pytest.mark.django_db
def test_annotate_vcf_disk_size(
    user_client: Client, test_grr: GenomicResourceRepo,
) -> None:

    vcf = textwrap.dedent("""
        ##fileformat=VCFv4.1
        ##contig=<ID=chr1>
        #CHROM	POS	ID	REF	ALT	QUAL	FILTER	INFO
        chr1	1	.	C	A	.	.	.
    """).strip()

    response = user_client.post(
        "/api/jobs/annotate_vcf",
        {
            "pipeline_id": "pipeline/test_pipeline",
            "data": ContentFile(vcf, "test_input.vcf"),
        },
    )
    assert response.status_code == 200

    job = Job.objects.last()
    assert job is not None
    assert job.disk_size == (
        pathlib.Path(job.config_path).stat().st_size
        + pathlib.Path(job.input_path).stat().st_size
        + pathlib.Path(job.result_path).stat().st_size
    )
    assert job.disk_size == 494


@pytest.mark.django_db
@pytest.mark.parametrize(
    "data, response_body",
    [
        (
            {
                "file_columns": ["chrom", "pos", "ref", "alt"],
                "column_mapping": {
                    "col_chrom": "chrom",
                    "col_pos": "pos",
                    "col_ref": "ref",
                    "col_alt": "alt",
                },
            },
            {
                "annotatable": "VCF Allele",
                "errors": "",
            },
        ),
        (
            {
                "file_columns": ["var", "loc"],
                "column_mapping": {
                    "col_location": "loc",
                    "col_variant": "var",
                },
            },
            {
                "annotatable": "CSHL Allele",
                "errors": "",
            },
        ),
        (
            {
                "file_columns": ["chr", "ps"],
                "column_mapping": {
                    "col_chrom": "chr",
                    "col_pos": "ps",
                },
            },
            {
                "annotatable": "Position",
                "errors": "",
            },
        ),
        (
            {
                "file_columns": ["chr", "pos", "vr"],
                "column_mapping": {
                    "col_chrom": "chr",
                    "col_pos": "pos",
                    "col_variant": "vr",
                },
            },
            {
                "annotatable": "DAE Allele",
                "errors": "",
            },
        ),
        (
            {
                "file_columns": ["chr", "beg", "end"],
                "column_mapping": {
                    "col_chrom": "chr",
                    "col_pos_beg": "beg",
                    "col_pos_end": "end",
                },
            },
            {
                "annotatable": "Region",
                "errors": "",
            },
        ),
        (
            {
                "file_columns": ["chr", "pos_beg", "pos_end", "type"],
                "column_mapping": {
                    "col_chrom": "chr",
                    "col_pos_beg": "pos_beg",
                    "col_cnv_type": "type",
                },
            },
            {
                "annotatable": "CNV Allele",
                "errors": "",
            },
        ),
        (
            {
                "file_columns": ["vcf"],
                "column_mapping": {
                    "col_vcf_like": "vcf",
                },
            },
            {
                "annotatable": "VCF Like Allele",
                "errors": "",
            },
        ),
        (
            {
                "file_columns": ["chr", "pos_beg", "pos_end"],
                "column_mapping": {},
            },
            {
                "annotatable": "",
                "errors": (
                    "Cannot build annotatable from selected columns!"),
            },
        ),
        (
            {
                "file_columns": ["chr", "pos_beg", "pos_end"],
                "column_mapping": {"col_special": "chr"},
            },
            {
                "annotatable": "",
                "errors": "Invalid column specification!",
            },
        ),
        (
            {
                "file_columns": [],
                "column_mapping": {
                    "col_vcf_like": "vcf",
                },
            },
            {
                "annotatable": "",
                "errors": "File header must be provided for column validation!",
            },
        ),
        (
            {
                "file_columns": ["chr", "pos_beg"],
                "column_mapping": {
                    "col_chrom": "chr",
                    "col_pos_beg": "pos_beg",
                },
            },
            {
                "annotatable": "",
                "errors": (
                    "Cannot build annotatable from selected columns!"),
            },
        ),
    ],
)
def test_validate_columns(
    admin_client: Client,
    data: dict[str, Any],
    response_body: dict[str, str],
) -> None:
    response = admin_client.post(
        "/api/jobs/validate_columns",
        data,
        content_type="application/json",
    )
    assert response.json() == response_body


def test_validate_columns_with_partial_mapping(
    admin_client: Client,
) -> None:
    response = admin_client.post(
        "/api/jobs/validate_columns",
        {
            "file_columns": ["chrom", "pos", "ref", "alt"],
            "column_mapping": {
                "col_chrom": "chrom",
                "col_pos": "pos",
                "col_ref": "ref",
                # "col_alt": "alt",
            },
        },
        content_type="application/json",
    )
    assert response.json() == {
        "annotatable": "VCF Allele",
        "errors": "",
    }
    response = admin_client.post(
        "/api/jobs/validate_columns",
        {
            "file_columns": ["chrom", "pos", "ref"],
            "column_mapping": {},
        },
        content_type="application/json",
    )
    assert response.json() == {
        "annotatable": "Position",
        "errors": "",
    }



@pytest.mark.django_db
@pytest.mark.parametrize(
    "input_file, separator, specification, expected_lines, file_extension",
    [
        (
            textwrap.dedent("""
                chrom	pos	ref	alt
                chr1	1	C	A
            """),
            "\t",
            {
                "col_chrom": "chrom",
                "col_pos": "pos",
                "col_ref": "ref",
                "col_alt": "alt",
            },
            [
                ["chrom", "pos", "ref", "alt", "position_1"],
                ["chr1", "1", "C", "A", "0.1"],
            ],
            ".tsv",
        ),
        (
            textwrap.dedent("""
                chrom,pos,ref,alt
                chr1,1,C,A
            """),
            ",",
            {
                "col_chrom": "chrom",
                "col_pos": "pos",
                "col_ref": "ref",
                "col_alt": "alt",
            },
            [
                ["chrom", "pos", "ref", "alt", "position_1"],
                ["chr1", "1", "C", "A", "0.1"],
            ],
            ".csv",
        ),
        (
            textwrap.dedent("""
                loc,var
                chr1:999,del(3)
            """),
            ",",
            {
                "col_location": "loc",
                "col_variant": "var",
                "genome": "hg38/GRCh38-hg38/genome",
            },
            [
                ['loc', 'var', 'position_1'],
                ['chr1:999', 'del(3)', '']
            ],
            ".csv",
        ),
        (
            textwrap.dedent("""
                chr,ps
                chr1,666
            """),
            ",",
            {
                "col_chrom": "chr",
                "col_pos": "ps",
            },
            [
                ['chr', 'ps', 'position_1'],
                ['chr1', '666', '']
            ],
            ".csv",
        ),
        (
            textwrap.dedent("""
                chr,pos,vr
                chr1,999,sub(T->C)
            """),
            ",",
            {
                "col_chrom": "chr",
                "col_pos": "pos",
                "col_variant": "vr",
                "genome": "hg38/GRCh38-hg38/genome",
            },
            [
                ['chr', 'pos', 'vr', 'position_1'],
                ['chr1', '999', 'sub(T->C)', '']
            ],
            ".csv",
        ),
        (
            textwrap.dedent("""
                chr,beg,end
                chr1,5,10
            """),
            ",",
            {
                "col_chrom": "chr",
                "col_pos_beg": "beg",
                "col_pos_end": "end",
            },
            [
                ['chr', 'beg', 'end', 'position_1'],
                ['chr1', '5', '10', '0.35']
            ],
            ".csv",
        ),
        (
            textwrap.dedent("""
                chr,pos_beg,pos_end,cnv
                chr1,7,20,cnv+
            """),
            ",",
            {
                "col_chrom": "chr",
                "col_pos_beg": "pos_beg",
                "col_pos_end": "pos_end",
                "col_cnv_type": "end",
            },
            [
                ['chr', 'pos_beg', 'pos_end', 'cnv', 'position_1'],
                ['chr1', '7', '20', 'cnv+', '0.483']
            ],
            ".csv",
        ),
        (
            textwrap.dedent("""
                vcf
                chr1:5:C:CT
            """),
            None,
            {
                "col_vcf_like": "vcf",
            },
            [
                ['vcf,position_1'],
                ['chr1:5:C:CT,0.25']
            ],
            ".txt",
        ),
    ],
)
def test_annotate_columns(
    admin_client: Client,
    input_file: str,
    separator: str | None,
    specification: dict[str, str],
    expected_lines: list[list[str]],
    file_extension: str,
) -> None:
    file = input_file.strip()

    params = {
        "pipeline_id": "pipeline/test_pipeline",
        "data": ContentFile(file, "test_input.tsv"),
        **specification,
    }
    if separator is not None:
        params["separator"] = separator
    if "col_variant" in specification or "col_location" in specification:
        params["genome"] = "hg38/GRCh38-hg38/genome"

    response = admin_client.post("/api/jobs/annotate_columns", params)

    assert response is not None
    assert response.status_code == 200

    user = User.objects.get(email="admin@example.com")
    assert Job.objects.filter(owner=user).count() == 2
    job = Job.objects.last()
    assert job is not None

    assert job.status == Job.Status.SUCCESS
    assert job.duration is not None
    assert job.duration < 6.0

    assert job.result_path.endswith(file_extension) is True

    output = pathlib.Path(job.result_path).read_text(encoding="utf-8")
    lines = [line.split(separator) for line in output.strip().split("\n")]
    assert lines == expected_lines


@pytest.mark.django_db
def test_annotate_columns_t4c8(
    admin_client: Client,
) -> None:
    file = textwrap.dedent("""
        chrom,pos,var
        chr1,9,del(3)
    """).strip()

    params = {
        "genome": "t4c8/t4c8_genome",
        "pipeline_id": "pipeline/test_pipeline",
        "data": ContentFile(file, "test_input.tsv"),
        "col_chrom": "chrom",
        "col_pos": "pos",
        "col_variant": "var",
    }
    params["separator"] = ","

    response = admin_client.post("/api/jobs/annotate_columns", params)

    assert response is not None
    assert response.status_code == 200

    user = User.objects.get(email="admin@example.com")
    assert Job.objects.filter(owner=user).count() == 2
    job = Job.objects.last()
    assert job is not None

    assert job.status == Job.Status.SUCCESS
    assert job.duration is not None
    assert job.duration < 7.0

    output = pathlib.Path(job.result_path).read_text(encoding="utf-8")
    lines = [line.split(",") for line in output.strip().split("\n")]
    assert lines == [
        ['chrom', 'pos', 'var', 'position_1'],
        ['chr1', '9', 'del(3)', '0.425']
    ]


@pytest.mark.django_db
def test_annotate_columns_anonymous_t4c8(
    anonymous_client: Client,
) -> None:
    file = textwrap.dedent("""
        chrom,pos,var
        chr1,9,del(3)
    """).strip()

    params = {
        "genome": "t4c8/t4c8_genome",
        "pipeline_id": "pipeline/test_pipeline",
        "data": ContentFile(file, "test_input.tsv"),
        "col_chrom": "chrom",
        "col_pos": "pos",
        "col_variant": "var",
    }
    params["separator"] = ","

    response = anonymous_client.post("/api/jobs/annotate_columns", params)

    assert response is not None
    assert response.status_code == 200
    assert AnonymousJob.objects.count() == 1

    job = AnonymousJob.objects.last()
    assert job is not None
    assert job.status == Job.Status.SUCCESS
    assert job.duration is not None
    assert job.duration < 7.0

    output = pathlib.Path(job.result_path).read_text(encoding="utf-8")
    lines = [line.split(",") for line in output.strip().split("\n")]
    assert lines == [
        ['chrom', 'pos', 'var', 'position_1'],
        ['chr1', '9', 'del(3)', '0.425']
    ]


@pytest.mark.django_db
def test_annotate_columns_disk_size(
    user_client: Client, test_grr: GenomicResourceRepo,
) -> None:
    file = textwrap.dedent("""
        chrom,pos,var
        chr1,9,del(3)
    """).strip()

    params = {
        "genome": "t4c8/t4c8_genome",
        "pipeline_id": "pipeline/test_pipeline",
        "data": ContentFile(file, "test_input.tsv"),
        "col_chrom": "chrom",
        "col_pos": "pos",
        "col_variant": "var",
    }
    params["separator"] = ","

    response = user_client.post("/api/jobs/annotate_columns", params)
    assert response.status_code == 200

    job = Job.objects.last()
    assert job is not None
    assert job.disk_size == (
        pathlib.Path(job.config_path).stat().st_size
        + pathlib.Path(job.input_path).stat().st_size
        + pathlib.Path(job.result_path).stat().st_size
    )
    assert job.disk_size == 177


@pytest.mark.django_db
def test_annotate_vcf_returns_403_when_quota_exceeded(
    user_client: Client,
    mocker: MockerFixture,
) -> None:
    quota_mock = MagicMock()
    quota_mock.check_job_quota.return_value = False
    mocker.patch.object(User, "get_quota", return_value=quota_mock)

    vcf = textwrap.dedent("""
        ##fileformat=VCFv4.1
        #CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO
        chr1\t1\t.\tC\tA\t.\t.\t.
    """).strip()

    response = user_client.post(
        "/api/jobs/annotate_vcf",
        {
            "pipeline_id": "pipeline/test_pipeline",
            "data": ContentFile(vcf, "test_input.vcf"),
        },
    )

    assert response.status_code == 403
    quota_mock.job_complete.assert_not_called()


@pytest.mark.django_db
def test_annotate_vcf_records_quota_usage_on_success(
    user_client: Client,
    mocker: MockerFixture,
    test_grr: GenomicResourceRepo,
) -> None:
    quota_mock = MagicMock()
    quota_mock.check_job_quota.return_value = True
    mocker.patch.object(User, "get_quota", return_value=quota_mock)

    vcf = textwrap.dedent("""
        ##fileformat=VCFv4.1
        ##contig=<ID=chr1>
        #CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO
        chr1\t1\t.\tC\tA\t.\t.\t.
    """).strip()

    response = user_client.post(
        "/api/jobs/annotate_vcf",
        {
            "pipeline_id": "pipeline/test_pipeline",
            "data": ContentFile(vcf, "test_input.vcf"),
        },
    )

    assert response.status_code == 200
    quota_mock.job_complete.assert_called_once()


@pytest.mark.django_db
def test_annotate_columns_returns_403_when_quota_exceeded(
    user_client: Client,
    mocker: MockerFixture,
) -> None:
    quota_mock = MagicMock()
    quota_mock.check_job_quota.return_value = False
    mocker.patch.object(User, "get_quota", return_value=quota_mock)

    file = textwrap.dedent("""
        chrom,pos,ref,alt
        chr1,1,C,A
    """).strip()

    response = user_client.post(
        "/api/jobs/annotate_columns",
        {
            "pipeline_id": "pipeline/test_pipeline",
            "data": ContentFile(file, "test_input.csv"),
            "col_chrom": "chrom",
            "col_pos": "pos",
            "col_ref": "ref",
            "col_alt": "alt",
            "separator": ",",
        },
    )

    assert response.status_code == 403
    quota_mock.job_complete.assert_not_called()


@pytest.mark.django_db
def test_annotate_columns_records_quota_usage_on_success(
    user_client: Client,
    mocker: MockerFixture,
    test_grr: GenomicResourceRepo,
) -> None:
    quota_mock = MagicMock()
    quota_mock.check_job_quota.return_value = True
    mocker.patch.object(User, "get_quota", return_value=quota_mock)

    file = textwrap.dedent("""
        chrom,pos,ref,alt
        chr1,1,C,A
    """).strip()

    response = user_client.post(
        "/api/jobs/annotate_columns",
        {
            "pipeline_id": "pipeline/test_pipeline",
            "data": ContentFile(file, "test_input.csv"),
            "col_chrom": "chrom",
            "col_pos": "pos",
            "col_ref": "ref",
            "col_alt": "alt",
            "separator": ",",
        },
    )

    assert response.status_code == 200
    quota_mock.job_complete.assert_called_once()


@pytest.mark.django_db
def test_annotate_columns_t4c8_gzipped(
    admin_client: Client,
) -> None:
    file = gzip.compress(textwrap.dedent("""
        chrom,pos,var
        chr1,9,del(3)
    """).strip().encode("utf-8"))

    params = {
        "genome": "t4c8/t4c8_genome",
        "pipeline_id": "pipeline/test_pipeline",
        "data": ContentFile(file, "test_input.tsv.gz"),
        "col_chrom": "chrom",
        "col_pos": "pos",
        "col_variant": "var",
        "separator": ",",
    }

    response = admin_client.post("/api/jobs/annotate_columns", params)

    assert response is not None
    assert response.status_code == 200

    user = User.objects.get(email="admin@example.com")
    assert Job.objects.filter(owner=user).count() == 2
    job = Job.objects.last()
    assert job is not None

    assert job.status == Job.Status.SUCCESS
    assert job.duration is not None
    assert job.duration < 7.0

    result_path = pathlib.Path(job.result_path)
    assert job.result_path.endswith(".gz"), str(result_path)
    assert result_path.exists(), str(result_path)
    output = gzip.decompress(
        pathlib.Path(job.result_path).read_bytes()).decode("utf-8")
    lines = [line.split(",") for line in output.strip().split("\n")]
    assert lines == [
        ['chrom', 'pos', 'var', 'position_1'],
        ['chr1', '9', 'del(3)', '0.425']
    ]


@pytest.mark.django_db
def test_annotate_vcf_gzip_fails(
    user_client: Client, test_grr: GenomicResourceRepo,
) -> None:
    user = User.objects.get(email="user@example.com")

    assert Job.objects.filter(owner=user).count() == 1

    vcf = gzip.compress(textwrap.dedent("""
        ##fileformat=VCFv4.1
        ##contig=<ID=chr1>
        #CHROM	POS	ID	REF	ALT	QUAL	FILTER	INFO
        chr1	1	.	C	A	.	.	.
    """).strip().encode())

    response = user_client.post(
        "/api/jobs/annotate_vcf",
        {
        "pipeline_id": "pipeline/test_pipeline",
            "data": ContentFile(vcf, "test_input.vcf.gz")
        },
    )
    assert response.status_code == 400

    assert Job.objects.filter(owner=user).count() == 1


@pytest.mark.django_db
def test_annotate_vcf_bgzip(
    user_client: Client, test_grr: GenomicResourceRepo,
    tmp_path: pathlib.Path,
) -> None:
    user = User.objects.get(email="user@example.com")

    assert Job.objects.filter(owner=user).count() == 1

    annotation_config = textwrap.dedent("""
        - position_score:
            resource_id: scores/pos1
            attributes:
            - name: position_1
              source: pos1
    """).lstrip()
    test_dir = tmp_path / "vcf_gzip"
    test_dir.mkdir(parents=True, exist_ok=True)
    vcf_content = textwrap.dedent("""
        ##fileformat=VCFv4.1
        ##contig=<ID=chr1>
        #CHROM	POS	ID	REF	ALT	QUAL	FILTER	INFO
        chr1	1	.	C	A	.	.	.
    """).strip()
    input_path = test_dir / "test_input.vcf"
    input_path.write_text(vcf_content)
    output_path = test_dir / "test_out.vcf"
    tabix_compress(str(input_path), str(output_path))
    vcf = output_path.read_bytes()

    response = user_client.post(
        "/api/jobs/annotate_vcf",
        {
            "pipeline_id": "pipeline/test_pipeline",
            "data": ContentFile(vcf, "test_input.vcf.gz")
        },
    )
    assert response.status_code == 200

    assert Job.objects.filter(owner=user).count() == 2

    job = Job.objects.last()
    assert job is not None

    saved_input = pathlib.Path(job.input_path)

    assert job.duration is not None
    assert job.duration < 7.0
    assert saved_input.exists()
    assert gzip.decompress(saved_input.read_bytes()).decode() == vcf_content

    saved_config = pathlib.Path(job.config_path)
    assert saved_config.exists()
    assert saved_config.read_text(encoding="utf-8") == annotation_config

    result_path = pathlib.Path(job.result_path)
    assert result_path.parent == \
        pathlib.Path(settings.JOB_RESULT_STORAGE_DIR) / user.email
    assert result_path.exists(), result_path
    assert job.result_path.endswith(".vcf.gz")
    output = gzip.decompress(result_path.read_bytes()).decode("utf-8").strip()
    expected = textwrap.dedent("""
        ##fileformat=VCFv4.1
        ##FILTER=<ID=PASS,Description="All filters passed">
        ##contig=<ID=chr1>
        ##pipeline_annotation_tool=GPF variant annotation.
        ##INFO=<ID=position_1,Number=A,Type=String,Description="test position score">
        #CHROM	POS	ID	REF	ALT	QUAL	FILTER	INFO
        chr1	1	.	C	A	.	.	position_1=0.1
     """).strip()
    assert output == expected


@pytest.mark.django_db
def test_annotate_columns_bad_request(admin_client: Client) -> None:
    input_file = textwrap.dedent("""
        chrom;pos;ref;alt
        chr1;1;C;A
    """).strip()
    response = admin_client.post(
        "/api/jobs/annotate_columns",
        {
            "pipeline_id": "pipeline/test_pipeline",
            "data": ContentFile(input_file)
        },
    )

    assert response.status_code == 400
    assert Job.objects.filter(
        owner=User.objects.get(email="admin@example.com"),
    ).count() == 1


@pytest.mark.django_db
def test_annotate_columns_bad_genome(admin_client: Client) -> None:
    file = gzip.compress(textwrap.dedent("""
        chrom,pos,var
        chr1,9,del(3)
    """).strip().encode("utf-8"))

    params = {
        "genome": "goshonome",
        "pipeline_id": "pipeline/test_pipeline",
        "data": ContentFile(file, "test_input.tsv.gz"),
        "col_chrom": "chrom",
        "col_pos": "pos",
        "col_variant": "var",
        "separator": "\t",
    }

    response = admin_client.post("/api/jobs/annotate_columns", params)

    assert response.status_code == 404
    assert Job.objects.filter(
        owner=User.objects.get(email="admin@example.com"),
    ).count() == 1


@pytest.mark.django_db
def test_user_create_pipeline(
    user_client: Client,
) -> None:
    user = User.objects.get(email="user@example.com")
    pipeline_config = "- position_score: scores/pos1"

    params = {
        "config": ContentFile(pipeline_config),
        "name": "test_pipeline",
    }

    assert Pipeline.objects.filter(owner=user).count() == 0

    response = user_client.post("/api/pipelines/user", params)

    assert response is not None
    assert response.status_code == 200
    assert response.json() == {"id": "1"}
    assert Pipeline.objects.filter(owner=user).count() == 1
    pipeline = Pipeline.objects.last()
    assert pipeline is not None
    assert pipeline.name == "test_pipeline"
    output = pathlib.Path(pipeline.config_path).read_text(encoding="utf-8")
    assert output == "- position_score: scores/pos1"

@pytest.mark.django_db
def test_user_create_pipeline_does_not_duplicate(
    user_client: Client,
) -> None:
    user = User.objects.get(email="user@example.com")
    pipeline_config = "- position_score: scores/pos1"

    params = {
        "config": ContentFile(pipeline_config),
        "name": "test_pipeline",
    }

    assert Pipeline.objects.filter(owner=user).count() == 0

    response = user_client.post("/api/pipelines/user", params)

    assert response is not None
    assert response.status_code == 200
    assert response.json() == {"id": "1"}
    assert Pipeline.objects.filter(owner=user).count() == 1
    pipeline = Pipeline.objects.last()
    assert pipeline is not None
    assert pipeline.name == "test_pipeline"
    response = user_client.post("/api/pipelines/user", params)
    assert Pipeline.objects.filter(owner=user).count() == 1


@pytest.mark.django_db
def test_create_job_for_pipeline_with_preamble(
    user_client: Client,
) -> None:
    user = User.objects.get(email="user@example.com")
    pipeline_config = textwrap.dedent("""
        preamble:
          input_reference_genome: hg38/GRCh38-hg38/genome
          summary: test_annotation_pipeline
          description: some annotation pipeline
        annotators:
        - position_score: scores/pos1
    """)

    params = {
        "config": ContentFile(pipeline_config),
        "name": "test_pipeline",
    }

    assert Pipeline.objects.filter(owner=user).count() == 0

    response = user_client.post("/api/pipelines/user", params)

    assert response is not None
    assert response.status_code == 200
    assert response.json() == {"id": "1"}
    assert Pipeline.objects.filter(owner=user).count() == 1
    pipeline = Pipeline.objects.last()
    assert pipeline is not None
    assert pipeline.name == "test_pipeline"
    saved_pipeline = pathlib.Path(pipeline.config_path).read_text(
        encoding="utf-8")
    assert saved_pipeline == pipeline_config

    vcf = textwrap.dedent("""
        ##fileformat=VCFv4.1
        ##contig=<ID=chr1>
        #CHROM	POS	ID	REF	ALT	QUAL	FILTER	INFO
        chr1	1	.	C	A	.	.	.
    """).strip()

    response = user_client.post(
        "/api/jobs/annotate_vcf",
        {
            "pipeline_id": str(pipeline.pk),
            "data": ContentFile(vcf, "test_input.vcf")
        },
    )

    job = Job.objects.last()

    assert job is not None

    config_path = pathlib.Path(job.config_path)
    assert config_path.exists()

    assert config_path.read_text().strip() == saved_pipeline.strip()


@pytest.mark.django_db
def test_anonymous_user_create_anonymous_pipeline(
    anonymous_client: Client,
) -> None:
    pipeline_config = "- position_score: scores/pos1"

    params = {
        "config": ContentFile(pipeline_config),
    }

    response = anonymous_client.post("/api/pipelines/user", params)

    assert response is not None
    assert response.status_code == 200
    anon_pipeline_id = response.json()["id"]
    pipeline = TemporaryPipeline.objects.last()
    assert pipeline is not None
    assert str(pipeline.pk) == anon_pipeline_id
    assert pipeline.name.startswith("pipeline-")
    assert pipeline.name.endswith(".yaml")
    output = pathlib.Path(pipeline.config_path).read_text(encoding="utf-8")
    assert output == "- position_score: scores/pos1"


@pytest.mark.django_db
def test_user_create_anonymous_pipeline(
    user_client: Client,
) -> None:
    user = User.objects.get(email="user@example.com")
    pipeline_config = "- position_score: scores/pos1"

    params = {
        "config": ContentFile(pipeline_config),
    }

    assert Pipeline.objects.filter(owner=user).count() == 0

    response = user_client.post("/api/pipelines/user", params)

    assert response is not None
    assert response.status_code == 200
    anon_pipeline_id = response.json()["id"]
    assert anon_pipeline_id == user_client.session.session_key
    assert TemporaryPipeline.objects.filter(
        session_id=anon_pipeline_id).count() == 1
    pipeline = TemporaryPipeline.objects.last()
    assert pipeline is not None
    assert pipeline.name.startswith("pipeline-")
    assert pipeline.name.endswith(".yaml")
    output = pathlib.Path(pipeline.config_path).read_text(encoding="utf-8")
    assert output == "- position_score: scores/pos1"


@pytest.mark.django_db
def test_user_create_pipeline_with_unicode_error(
    user_client: Client,
) -> None:
    user = User.objects.get(email="user@example.com")
    pipeline_config = "\x80\x81".encode('latin-1')

    params = {
        "config": ContentFile(pipeline_config),
        "name": "test_pipeline",
    }

    assert Pipeline.objects.filter(owner=user).count() == 0

    response = user_client.post("/api/pipelines/user", params)

    assert response is not None
    assert response.status_code == 400
    assert response.json() == {
        "reason": (
            "Invalid pipeline configuration file: 'utf-8' codec can't decode "
            "byte 0x80 in position 0: invalid start byte"
        )
    }


@pytest.mark.django_db
def test_user_create_pipeline_with_os_error(
    user_client: Client,
    mocker: MockerFixture,
) -> None:
    mocker.patch(
        'pathlib.Path.write_text',
        side_effect=OSError("Permission denied")
    )
    user = User.objects.get(email="user@example.com")
    pipeline_config = "- position_score: scores/pos1"

    params = {
        "config": ContentFile(pipeline_config),
        "name": "test_pipeline",
    }

    assert Pipeline.objects.filter(owner=user).count() == 0

    response = user_client.post("/api/pipelines/user", params)

    assert response is not None
    assert response.status_code == 500
    assert response.json() == {
        "reason": "Could not write file!",
    }


@pytest.mark.django_db
def test_user_update_pipeline(
    user_client: Client,
) -> None:
    user = User.objects.get(email="user@example.com")
    pipeline_config = "- position_score: scores/pos1"

    assert Pipeline.objects.filter(owner=user).count() == 0
    params = {"config": ContentFile(pipeline_config), "name": "test_pipeline"}
    response = user_client.post("/api/pipelines/user", params)

    assert response is not None
    assert response.status_code == 200
    pipeline_id = response.json()["id"]
    assert Pipeline.objects.filter(owner=user).count() == 1
    pipeline = Pipeline.objects.last()
    assert pipeline is not None
    assert str(pipeline.pk) == pipeline_id
    assert pipeline is not None
    assert pipeline.name == "test_pipeline"
    output = pathlib.Path(pipeline.config_path).read_text(encoding="utf-8")
    assert output == "- position_score: scores/pos1"

    pipeline_config = "- position_score: scores/pos2"
    params = {"config": ContentFile(pipeline_config), "id": pipeline_id}
    response = user_client.post("/api/pipelines/user", params)

    assert response is not None
    assert response.status_code == 200
    assert response.json()["id"] == pipeline_id
    assert Pipeline.objects.filter(owner=user).count() == 1
    pipeline = Pipeline.objects.last()
    assert pipeline is not None
    assert pipeline.name == "test_pipeline"
    output = pathlib.Path(pipeline.config_path).read_text(encoding="utf-8")
    assert output == "- position_score: scores/pos2"

@pytest.mark.django_db
def test_user_delete_pipeline(
    user_client: Client,
) -> None:
    user = User.objects.get(email="user@example.com")
    pipeline_config = "- position_score: scores/pos1"

    params = {
        "config": ContentFile(pipeline_config),
        "name": "test-pipeline",
    }

    response = user_client.post("/api/pipelines/user", params)

    assert response is not None
    assert response.status_code == 200
    assert Pipeline.objects.filter(owner=user).count() == 1
    pipeline_id = response.json()["id"]

    response = user_client.delete(f"/api/pipelines/user?id={pipeline_id}")

    assert response is not None
    assert response.status_code == 204
    assert Pipeline.objects.filter(owner=user).count() == 0


@pytest.mark.django_db
def test_user_get_pipeline(
    user_client: Client,
) -> None:
    user = User.objects.get(email="user@example.com")
    pipeline_config = "- position_score: scores/pos1"

    params = {
        "config": ContentFile(pipeline_config),
        "name": "test-pipeline",
    }

    response = user_client.post("/api/pipelines/user", params)

    assert response is not None
    assert response.status_code == 200
    pipeline_id = response.json()["id"]
    assert Pipeline.objects.filter(owner=user).count() == 1

    response = user_client.get(f"/api/pipelines/user?id={pipeline_id}")

    assert response is not None
    assert response.status_code == 200
    assert response.json() == {
        "id": pipeline_id,
        "name": "test-pipeline",
        "owner": "user@example.com",
        "pipeline": "- position_score: scores/pos1",
    }


@pytest.mark.django_db
def test_user_get_pipeline_without_id(
    user_client: Client,
) -> None:
    response = user_client.get("/api/pipelines/user?id=")

    assert response is not None
    assert response.status_code == 400
    assert response.json() == {"reason": "Pipeline ID not provided!"}


@pytest.mark.django_db
def test_user_get_pipeline_with_bad_id(
    user_client: Client,
) -> None:
    response = user_client.get("/api/pipelines/user?id=123")

    assert response is not None
    assert response.status_code == 400
    assert response.json() == {"reason": "Pipeline name not recognized!"}


@pytest.mark.django_db
def test_user_create_pipeline_with_bad_name(
    user_client: Client,
) -> None:
    user = User.objects.get(email="user@example.com")
    pipeline_config = "- position_score: scores/pos1"

    params = {
        "config": ContentFile(pipeline_config),
        "name": "pipeline/test_pipeline",
    }
    response = user_client.post("/api/pipelines/user", params)
    assert response.json() == {
        "reason": "Pipeline with such name cannot be created or updated!",
    }
    assert response.status_code == 400
    assert Pipeline.objects.filter(owner=user).count() == 0

    params = {
        "config": ContentFile(pipeline_config),
        "name": "t4c8/t4c8_pipeline",
    }
    response = user_client.post("/api/pipelines/user", params)
    assert response.json() == {
        "reason": "Pipeline with such name cannot be created or updated!",
    }
    assert response.status_code == 400
    assert Pipeline.objects.filter(owner=user).count() == 0


@pytest.mark.django_db
def test_get_pipelines(
    user_client: Client,
    mock_lru_cache: LRUPipelineCache,
) -> None:
    user = User.objects.get(email="user@example.com")

    # Named pipeline
    pipeline_config = "- position_score: scores/pos1"
    params = {
        "config": ContentFile(pipeline_config),
        "name": "test-user-pipeline",
    }
    response = user_client.post("/api/pipelines/user", params)

    assert response is not None
    assert response.status_code == 200
    assert response.json() == {"id": "1"}
    assert Pipeline.objects.filter(owner=user).count() == 1

    # Anonymous pipeline
    pipeline_config = "- position_score: scores/pos2"
    params = {
        "config": ContentFile(pipeline_config),
    }
    response = user_client.post("/api/pipelines/user", params)

    assert response is not None
    assert response.status_code == 200
    assert isinstance(response.json()["id"], str)
    assert TemporaryPipeline.objects.filter(
        session_id=user_client.session.session_key).count() == 1

    response = user_client.get("/api/pipelines")
    pipelines = response.json()
    assert len(pipelines) == 3
    assert pipelines[0]["name"] == "pipeline/test_pipeline"
    assert pipelines[0]["status"] == "unloaded"
    assert pipelines[1]["name"] == "t4c8/t4c8_pipeline"
    assert pipelines[1]["status"] == "unloaded"
    assert pipelines[2]["name"] == "test-user-pipeline"
    assert pipelines[2]["status"] == "loaded"
    assert pipelines[2]["content"] == "- position_score: scores/pos1"


@pytest.mark.django_db
def test_get_pipelines_annonymous_user(
    user_client: Client,
    anonymous_client: Client,
) -> None:
    user = User.objects.get(email="user@example.com")
    pipeline_config = "- position_score: scores/pos1"
    params = {
        "config": ContentFile(pipeline_config),
        "name": "test-user-pipeline",
    }
    response = user_client.post("/api/pipelines/user", params)

    assert response is not None
    assert response.status_code == 200
    assert response.json() == {"id": "1"}
    assert Pipeline.objects.filter(owner=user).count() == 1

    response = anonymous_client.get("/api/pipelines")
    pipelines = response.json()
    assert len(pipelines) == 2
    assert pipelines[0]["id"] == "pipeline/test_pipeline"
    assert pipelines[1]["id"] == "t4c8/t4c8_pipeline"


@pytest.mark.django_db
def test_annotate_vcf_user_pipeline(
    user_client: Client, test_grr: GenomicResourceRepo,
) -> None:
    user = User.objects.get(email="user@example.com")
    pipeline_config = "- position_score: scores/pos1"
    params = {
        "config": ContentFile(pipeline_config),
        "name": "test-user-pipeline",
    }
    response = user_client.post("/api/pipelines/user", params)

    assert response is not None
    assert response.status_code == 200
    pipeline_id = response.json()["id"]
    assert Pipeline.objects.filter(owner=user).count() == 1

    user = User.objects.get(email="user@example.com")

    assert Job.objects.filter(owner=user).count() == 1

    vcf = textwrap.dedent("""
        ##fileformat=VCFv4.1
        ##contig=<ID=chr1>
        #CHROM	POS	ID	REF	ALT	QUAL	FILTER	INFO
        chr1	1	.	C	A	.	.	.
    """).strip()

    response = user_client.post(
        "/api/jobs/annotate_vcf",
        {
            "pipeline_id": pipeline_id,
            "data": ContentFile(vcf, "test_input.vcf")
        },
    )
    assert response.status_code == 200

    assert Job.objects.filter(owner=user).count() == 2
    job = Job.objects.last()
    assert job is not None

    saved_config = pathlib.Path(job.config_path)
    assert saved_config.exists()
    assert saved_config.read_text(encoding="utf-8").strip() == (
        "- position_score: scores/pos1"
    )

    result_path = pathlib.Path(job.result_path)
    assert result_path.exists()


def test_annotate_vcf_variant_quota(
    user_client: Client,
    settings: LazySettings,
) -> None:
    settings.QUOTAS["variant_count"] = 50

    vcf = textwrap.dedent("""
        ##fileformat=VCFv4.1
        ##contig=<ID=chr1>
        #CHROM	POS	ID	REF	ALT	QUAL	FILTER	INFO
    """).strip() + "\n" + "\n".join(
        f"chr1\t{i}\t.\tA\tT\t.\t.\t." for i in range(1, 50)
    )

    response = user_client.post(
        "/api/jobs/annotate_vcf",
        {
            "pipeline_id": "pipeline/test_pipeline",
            "data": ContentFile(vcf, "test_input.vcf")
        },
    )
    assert response.status_code == 200
    settings.QUOTAS["variant_count"] = 48

    vcf = textwrap.dedent("""
        ##fileformat=VCFv4.1
        ##contig=<ID=chr1>
        #CHROM	POS	ID	REF	ALT	QUAL	FILTER	INFO
    """).strip() + "\n" + "\n".join(
        f"chr1\t{i}\t.\tA\tT\t.\t.\t." for i in range(1, 50)
    )

    response = user_client.post(
        "/api/jobs/annotate_vcf",
        {
            "pipeline_id": "pipeline/test_pipeline",
            "data": ContentFile(vcf, "test_input.vcf")
        },
    )
    assert response.status_code == 413


def test_annotate_columns_variant_quota(
    user_client: Client,
    settings: LazySettings,
) -> None:
    settings.QUOTAS["variant_count"] = 50

    file = "chrom,pos,ref,alt\n" + "\n".join(
        f"chr1,{i},A,T" for i in range(1, 50)
    )

    params = {
        "pipeline_id": "pipeline/test_pipeline",
        "data": ContentFile(file, "test_input.tsv"),
        "col_chrom": "chrom",
        "col_pos": "pos",
        "col_ref": "ref",
        "col_alt": "alt",
    }

    response = user_client.post("/api/jobs/annotate_columns", params)
    assert response.status_code == 200

    settings.QUOTAS["variant_count"] = 48

    file = "chrom,pos,ref,alt\n" + "\n".join(
        f"chr1,{i},A,T" for i in range(1, 50)
    )

    params = {
        "pipeline_id": "pipeline/test_pipeline",
        "data": ContentFile(file, "test_input.tsv"),
        "col_chrom": "chrom",
        "col_pos": "pos",
        "col_ref": "ref",
        "col_alt": "alt",
    }

    response = user_client.post("/api/jobs/annotate_columns", params)
    assert response.status_code == 413


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_annotate_vcf_notifications(
    user_client: Client, mocker: MockerFixture,
    test_grr: GenomicResourceRepo,
) -> None:
    cache = LRUPipelineCache(test_grr, 16)
    mocker.patch(
        "web_annotation.jobs"
        ".views.AnnotateVCF.lru_cache",
        new=cache,
    )
    user = await sync_to_async(User.objects.get)(email="user@example.com")
    communicator = CustomWebsocketCommunicator(
            AnnotationStateConsumer.as_asgi(), "/ws/test/", user=user)

    connected, _ = await communicator.connect(timeout=1000000)
    assert connected

    vcf = textwrap.dedent("""
        ##fileformat=VCFv4.1
        ##contig=<ID=chr1>
        #CHROM	POS	ID	REF	ALT	QUAL	FILTER	INFO
        chr1	1	.	C	A	.	.	.
    """).strip()

    response = await sync_to_async(user_client.post)(
        "/api/jobs/annotate_vcf",
        {
            "pipeline_id": "pipeline/test_pipeline",
            "data": ContentFile(vcf, "test_input.vcf"),
        },
    )

    assert response.status_code == 200

    job_id = response.json()["job_id"]

    output = await communicator.receive_json_from()
    assert output == {
        "type": "pipeline_status",
        "pipeline_id": "pipeline/test_pipeline",
        "status": "loading",
    }
    output = await communicator.receive_json_from()
    assert output == {
        "type": "pipeline_status",
        "pipeline_id": "pipeline/test_pipeline",
        "status": "loaded",
    }
    output = await communicator.receive_json_from()
    assert output == {
        "type": "job_status",
        "job_id": job_id,
        "status": "waiting",
    }
    output = await communicator.receive_json_from()
    assert output == {
        "type": "job_status",
        "job_id": job_id,
        "status": "in_progress",
    }
    output = await communicator.receive_json_from()
    assert output == {
        "type": "job_status",
        "job_id": job_id,
        "status": "success",
    }


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_annotate_columns_notifications(
    user_client: Client, mocker: MockerFixture,
    test_grr: GenomicResourceRepo,
) -> None:
    cache = LRUPipelineCache(test_grr, 16)
    mocker.patch(
        "web_annotation.jobs"
        ".views.AnnotateColumns.lru_cache",
        new=cache,
    )
    user = await sync_to_async(User.objects.get)(email="user@example.com")
    communicator = CustomWebsocketCommunicator(
            AnnotationStateConsumer.as_asgi(), "/ws/test/", user=user)

    connected, _ = await communicator.connect()
    assert connected

    input_file = textwrap.dedent("""
        chrom	pos	ref	alt
        chr1	1	C	A
    """).strip()
    specification = {
        "col_chrom": "chrom",
        "col_pos": "pos",
        "col_ref": "ref",
        "col_alt": "alt",
    }
    params = {
        "pipeline_id": "pipeline/test_pipeline",
        "data": ContentFile(input_file, "test_input.tsv"),
        "separator": "\t",
        **specification,
    }

    response = await sync_to_async(user_client.post)(
        "/api/jobs/annotate_columns",
        params,
    )

    assert response.status_code == 200

    job_id = response.json()["job_id"]

    output = await communicator.receive_json_from()
    assert output == {
        "type": "pipeline_status",
        "pipeline_id": "pipeline/test_pipeline",
        "status": "loading",
    }
    output = await communicator.receive_json_from()
    assert output == {
        "type": "pipeline_status",
        "pipeline_id": "pipeline/test_pipeline",
        "status": "loaded",
    }
    output = await communicator.receive_json_from()
    assert output == {
        "type": "job_status",
        "job_id": job_id,
        "status": "waiting",
    }
    output = await communicator.receive_json_from()
    assert output == {
        "type": "job_status",
        "job_id": job_id,
        "status": "in_progress",
    }
    output = await communicator.receive_json_from()
    assert output == {
        "type": "job_status",
        "job_id": job_id,
        "status": "success",
    }


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_annotate_vcf_notifications_fail(
    user_client: Client, mocker: MockerFixture,
    test_grr: GenomicResourceRepo,
) -> None:
    cache = LRUPipelineCache(test_grr, 16)
    mocker.patch(
        "web_annotation.jobs"
        ".views.AnnotateVCF.lru_cache",
        new=cache,
    )
    mocker.patch(
        "web_annotation.jobs"
        ".views.run_vcf_job",
        side_effect=ValueError("some error"),
    )
    user = await sync_to_async(User.objects.get)(email="user@example.com")
    communicator = CustomWebsocketCommunicator(
            AnnotationStateConsumer.as_asgi(), "/ws/test/", user=user)

    connected, _ = await communicator.connect()
    assert connected

    vcf = textwrap.dedent("""
        ##fileformat=VCFv4.1
        ##contig=<ID=chr1>
        #CHROM	POS	ID	REF	ALT	QUAL	FILTER	INFO
        chr1	1	.	C	A	.	.	.
    """).strip()

    response = await sync_to_async(user_client.post)(
        "/api/jobs/annotate_vcf",
        {
            "pipeline_id": "pipeline/test_pipeline",
            "data": ContentFile(vcf, "test_input.vcf"),
        },
    )

    assert response.status_code == 200

    job_id = response.json()["job_id"]

    output = await communicator.receive_json_from()
    assert output == {
        "type": "pipeline_status",
        "pipeline_id": "pipeline/test_pipeline",
        "status": "loading",
    }
    output = await communicator.receive_json_from()
    assert output == {
        "type": "pipeline_status",
        "pipeline_id": "pipeline/test_pipeline",
        "status": "loaded",
    }
    output = await communicator.receive_json_from()
    assert output == {
        "type": "job_status",
        "job_id": job_id,
        "status": "waiting",
    }
    output = await communicator.receive_json_from()
    assert output == {
        "type": "job_status",
        "job_id": job_id,
        "status": "in_progress",
    }
    output = await communicator.receive_json_from()
    assert output == {
        "type": "job_status",
        "job_id": job_id,
        "status": "failed",
    }


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_annotate_columns_notifications_fail(
    user_client: Client, mocker: MockerFixture,
    test_grr: GenomicResourceRepo,
) -> None:
    cache = LRUPipelineCache(test_grr, 16)
    mocker.patch(
        "web_annotation.jobs"
        ".views.AnnotateColumns.lru_cache",
        new=cache,
    )
    mocker.patch(
        "web_annotation.jobs"
        ".views.run_columns_job",
        side_effect=ValueError("some error"),
    )
    user = await sync_to_async(User.objects.get)(email="user@example.com")
    communicator = CustomWebsocketCommunicator(
            AnnotationStateConsumer.as_asgi(), "/ws/notifications/", user=user)

    connected, _ = await communicator.connect()
    assert connected

    input_file = textwrap.dedent("""
        chrom	pos	ref	alt
        chr1	1	C	A
    """).strip()
    specification = {
        "col_chrom": "chrom",
        "col_pos": "pos",
        "col_ref": "ref",
        "col_alt": "alt",
    }
    params = {
        "pipeline_id": "pipeline/test_pipeline",
        "data": ContentFile(input_file, "test_input.tsv"),
        "separator": "\t",
        **specification,
    }

    response = await sync_to_async(user_client.post)(
        "/api/jobs/annotate_columns",
        params,
    )

    assert response.status_code == 200

    job_id = response.json()["job_id"]

    output = await communicator.receive_json_from()
    assert output == {
        "type": "pipeline_status",
        "pipeline_id": "pipeline/test_pipeline",
        "status": "loading",
    }
    output = await communicator.receive_json_from()
    assert output == {
        "type": "pipeline_status",
        "pipeline_id": "pipeline/test_pipeline",
        "status": "loaded",
    }
    output = await communicator.receive_json_from()
    assert output == {
        "type": "job_status",
        "job_id": job_id,
        "status": "waiting",
    }
    output = await communicator.receive_json_from()
    assert output == {
        "type": "job_status",
        "job_id": job_id,
        "status": "in_progress",
    }
    output = await communicator.receive_json_from()
    assert output == {
        "type": "job_status",
        "job_id": job_id,
        "status": "failed",
    }


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_annotate_notifications_unloading_pipeline(
    user_client: Client, mocker: MockerFixture,
    test_grr: GenomicResourceRepo,
) -> None:
    cache = LRUPipelineCache(test_grr, 1)
    mocker.patch(
        "web_annotation.jobs"
        ".views.AnnotateVCF.lru_cache",
        new=cache,
    )
    user = await sync_to_async(User.objects.get)(email="user@example.com")
    communicator = CustomWebsocketCommunicator(
            AnnotationStateConsumer.as_asgi(), "/ws/test/", user=user)

    connected, _ = await communicator.connect()
    assert connected

    vcf = textwrap.dedent("""
        ##fileformat=VCFv4.1
        ##contig=<ID=chr1>
        #CHROM	POS	ID	REF	ALT	QUAL	FILTER	INFO
        chr1	1	.	C	A	.	.	.
    """).strip()

    response = await sync_to_async(user_client.post)(
        "/api/jobs/annotate_vcf",
        {
            "pipeline_id": "pipeline/test_pipeline",
            "data": ContentFile(vcf, "test_input.vcf"),
        },
    )

    job_id = response.json()["job_id"]

    assert response.status_code == 200

    output = await communicator.receive_json_from()
    assert output == {
        "type": "pipeline_status",
        "pipeline_id": "pipeline/test_pipeline",
        "status": "loading",
    }
    output = await communicator.receive_json_from()
    assert output == {
        "type": "pipeline_status",
        "pipeline_id": "pipeline/test_pipeline",
        "status": "loaded",
    }
    output = await communicator.receive_json_from()
    assert output == {
        "type": "job_status",
        "job_id": job_id,
        "status": "waiting",
    }
    output = await communicator.receive_json_from()
    assert output == {
        "type": "job_status",
        "job_id": job_id,
        "status": "in_progress",
    }
    output = await communicator.receive_json_from()
    assert output == {
        "type": "job_status",
        "job_id": job_id,
        "status": "success",
    }

    response = await sync_to_async(user_client.post)(
        "/api/jobs/annotate_vcf",
        {
            "pipeline_id": "t4c8/t4c8_pipeline",
            "data": ContentFile(vcf, "test_input.vcf"),
        },
    )

    job_id = response.json()["job_id"]

    assert response.status_code == 200

    output = await communicator.receive_json_from()
    assert output == {
        "type": "pipeline_status",
        "pipeline_id": "t4c8/t4c8_pipeline",
        "status": "loading",
    }
    output = await communicator.receive_json_from()
    assert output == {
        "type": "pipeline_status",
        "pipeline_id": "pipeline/test_pipeline",
        "status": "unloaded",
    }
    output = await communicator.receive_json_from()
    assert output == {
        "type": "pipeline_status",
        "pipeline_id": "t4c8/t4c8_pipeline",
        "status": "loaded",
    }
    output = await communicator.receive_json_from()
    assert output == {
        "type": "job_status",
        "job_id": job_id,
        "status": "waiting",
    }
    output = await communicator.receive_json_from()
    assert output == {
        "type": "job_status",
        "job_id": job_id,
        "status": "in_progress",
    }
    output = await communicator.receive_json_from()
    assert output == {
        "type": "job_status",
        "job_id": job_id,
        "status": "success",
    }


def test_job_failure_stores_exception(
    user_client: Client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vcf = textwrap.dedent("""
        ##fileformat=VCFv4.1
        ##contig=<ID=chr1>
        #CHROM	POS	ID	REF	ALT	QUAL	FILTER	INFO
        chr1	1	.	C	A	.	.	.
    """).strip()

    def raise_exception(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("Simulated job failure.")

    monkeypatch.setattr(
        "web_annotation.tasks.annotate_vcf",
        raise_exception,
    )

    response = user_client.post(
        "/api/jobs/annotate_vcf",
        {
            "pipeline_id": "pipeline/test_pipeline",
            "data": ContentFile(vcf, "test_input.vcf")
        },
    )
    assert response.status_code == 200

    job = Job.objects.last()
    assert job is not None
    assert job.status == Job.Status.FAILED
    assert job.error is not None
    assert "Simulated job failure." in job.error


def test_job_failure_read_stored_exception(
    user_client: Client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vcf = textwrap.dedent("""
        ##fileformat=VCFv4.1
        ##contig=<ID=chr1>
        #CHROM	POS	ID	REF	ALT	QUAL	FILTER	INFO
        chr1	1	.	C	A	.	.	.
    """).strip()

    def raise_exception(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("Simulated job failure.")

    monkeypatch.setattr(
        "web_annotation.tasks.annotate_vcf",
        raise_exception,
    )

    response = user_client.post(
        "/api/jobs/annotate_vcf",
        {
            "pipeline_id": "pipeline/test_pipeline",
            "data": ContentFile(vcf, "test_input.vcf")
        },
    )
    assert response.status_code == 200
    job_id = response.json()["job_id"]

    response = user_client.get(
        f"/api/jobs/{job_id}"
    )

    assert response.status_code == 200
    assert "Simulated job failure." in response.json()["error"]

    response = user_client.get(
        "/api/jobs",
    )

    assert "Simulated job failure." in response.json()[-1]["error"]


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_annotate_anonymous_notifications(
    anonymous_client: Client, mocker: MockerFixture,
    test_grr: GenomicResourceRepo,
) -> None:
    cache = LRUPipelineCache(test_grr, 16)
    mocker.patch(
        "web_annotation.jobs"
        ".views.AnnotateVCF.lru_cache",
        new=cache,
    )
    session = await anonymous_client.asession()
    assert session.session_key is not None
    user = WebAnnotationAnonymousUser(session.session_key, ip="test")
    communicator = CustomWebsocketCommunicator(
        AnnotationStateConsumer.as_asgi(),
        "/ws/test/", user=user,
        session=session,
    )

    connected, _ = await communicator.connect(timeout=1000000)
    assert connected

    vcf = textwrap.dedent("""
        ##fileformat=VCFv4.1
        ##contig=<ID=chr1>
        #CHROM	POS	ID	REF	ALT	QUAL	FILTER	INFO
        chr1	1	.	C	A	.	.	.
    """).strip()

    response = await sync_to_async(anonymous_client.post)(
        "/api/jobs/annotate_vcf",
        {
            "pipeline_id": "pipeline/test_pipeline",
            "data": ContentFile(vcf, "test_input.vcf"),
        },
    )

    assert response.status_code == 200

    job_id = response.json()["job_id"]

    output = await communicator.receive_json_from(timeout=1000)
    assert output == {
        "type": "pipeline_status",
        "pipeline_id": "pipeline/test_pipeline",
        "status": "loading",
    }
    output = await communicator.receive_json_from()
    assert output == {
        "type": "pipeline_status",
        "pipeline_id": "pipeline/test_pipeline",
        "status": "loaded",
    }
    output = await communicator.receive_json_from()
    assert output == {
        "type": "job_status",
        "job_id": job_id,
        "status": "waiting",
    }
    output = await communicator.receive_json_from()
    assert output == {
        "type": "job_status",
        "job_id": job_id,
        "status": "in_progress",
    }
    output = await communicator.receive_json_from()
    assert output == {
        "type": "job_status",
        "job_id": job_id,
        "status": "success",
    }


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_clean_up_anonymous_jobs(
    anonymous_client: Client, mocker: MockerFixture,
    test_grr: GenomicResourceRepo,
) -> None:
    cache = LRUPipelineCache(test_grr, 16)
    mocker.patch(
        "web_annotation.jobs"
        ".views.AnnotateVCF.lru_cache",
        new=cache,
    )
    session = await anonymous_client.asession()
    assert session.session_key is not None
    user = WebAnnotationAnonymousUser(session.session_key, ip="test")

    # Create two websocket connections for the same anonymous user
    first_communicator = CustomWebsocketCommunicator(
        AnnotationStateConsumer.as_asgi(),
        "/ws/test/", user=user,
        session=session,
    )
    second_communicator = CustomWebsocketCommunicator(
        AnnotationStateConsumer.as_asgi(),
        "/ws/test/", user=user,
        session=session,
    )

    first_connected, _ = await first_communicator.connect(timeout=1000)
    assert first_connected
    second_connected, _ = await second_communicator.connect(timeout=1000)
    assert second_connected

    assert await sync_to_async(TemporaryPipeline.objects.count)() == 0
    assert await sync_to_async(user.job_class.objects.count)() == 0

    # Create anonymous pipeline and job
    pipeline_response = await sync_to_async(anonymous_client.post)(
        "/api/pipelines/user",
        {"config": ContentFile("- position_score: scores/pos1")},
    )
    assert pipeline_response is not None
    assert pipeline_response.status_code == 200

    annotate_response = await sync_to_async(anonymous_client.post)(
        "/api/jobs/annotate_columns",
        {
            "pipeline_id": pipeline_response.json()["id"],
            "data": ContentFile(
                "chrom,pos,ref,alt\n" + "\n".join(
                    f"chr1,{i},A,T" for i in range(1, 10)
                ),
                "test_input.tsv",
            ),
            "col_chrom": "chrom",
            "col_pos": "pos",
            "col_ref": "ref",
            "col_alt": "alt",
        }
    )
    assert annotate_response.status_code == 200

    # Check that pipeline and job are created
    assert await sync_to_async(TemporaryPipeline.objects.count)() == 1
    assert await sync_to_async(user.job_class.objects.count)() == 1

    await first_communicator.disconnect(timeout=1000)

    # Check that pipeline and job are still present
    assert await sync_to_async(TemporaryPipeline.objects.count)() == 1
    assert await sync_to_async(user.job_class.objects.count)() == 1

    await second_communicator.disconnect(timeout=1000)

    # Check that pipeline and job are cleaned up after all connections closed
    assert await sync_to_async(user.job_class.objects.count)() == 0


@pytest.mark.django_db
def test_load_annotation_pipeline(
    user_client: Client,
    mock_lru_cache: LRUPipelineCache,
) -> None:
    params = {
        "id": "pipeline/test_pipeline",
    }

    assert mock_lru_cache.has_pipeline(
        "pipeline/test_pipeline",
    ) is False

    response = user_client.post("/api/pipelines/load", params)
    assert response is not None
    assert response.status_code == 204

    assert mock_lru_cache.has_pipeline(
        "pipeline/test_pipeline",
    ) is True


@pytest.mark.django_db
def test_save_unloads_pipeline(
    user_client: Client,
    mock_lru_cache: LRUPipelineCache,
) -> None:
    # Pipeline doesn't exist yet
    assert mock_lru_cache.has_pipeline(
        "1",
    ) is False

    # Save pipeline
    response = user_client.post(
        "/api/pipelines/user",
        {
            "config": ContentFile("- position_score: scores/pos2"),
            "name": "test-pipeline",
        },
    )
    assert response is not None
    assert response.status_code == 200

    # Save should not load the pipeline
    assert mock_lru_cache.has_pipeline(
        "1",
    ) is True

    # Save again, with updated config
    response = user_client.post(
        "/api/pipelines/user",
        {
            "config": ContentFile("- position_score: scores/pos1"),
            "name": "test-pipeline",
            "id": "1",
        },
    )
    assert response is not None
    assert response.status_code == 200

    assert mock_lru_cache.has_pipeline(
        "1",
    ) is True
