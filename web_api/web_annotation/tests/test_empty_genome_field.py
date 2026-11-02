# pylint: disable=W0621,C0114,C0116
"""An empty ``genome`` field means no genome, the same as an absent one.

``_validate_request`` let ``genome=""`` through as no genome, but
``get_genome`` refused it with a ``ValueError`` that ``_create_job`` turned
into a 500 (iossifovlab/gain#2109).
"""
import pathlib
import textwrap
from typing import Any

import pytest
import pytest_mock
from django.core.files.base import ContentFile
from django.test import Client

from web_annotation.executor import SequentialTaskExecutor
from web_annotation.models import Job

VCF = textwrap.dedent("""
    ##fileformat=VCFv4.1
    ##contig=<ID=chr1>
    #CHROM	POS	ID	REF	ALT	QUAL	FILTER	INFO
    chr1	1	.	C	A	.	.	.
""").strip()

TABULAR = textwrap.dedent("""
    chrom	pos	ref	alt
    chr1	1	C	A
""").strip()

UPLOADS = {
    "/api/jobs/annotate_vcf": (
        {}, VCF, "test_input.vcf"),
    "/api/jobs/annotate_tabular": (
        {
            "separator": "\t",
            "col_chrom": "chrom",
            "col_pos": "pos",
            "col_ref": "ref",
            "col_alt": "alt",
        },
        TABULAR, "test_input.tsv"),
}


@pytest.fixture(autouse=True)
def sequential_task_executor(mocker: pytest_mock.MockerFixture) -> None:
    mocker.patch(
        "web_annotation.annotation_base_view.AnnotationBaseView.JOB_EXECUTOR",
        new_callable=SequentialTaskExecutor,
    )


def _post_job(
    client: Client, url: str, extra: dict[str, str],
) -> tuple[int, dict[str, Any]]:
    fields, content, filename = UPLOADS[url]
    jobs_before = set(Job.objects.values_list("pk", flat=True))
    response = client.post(url, {
        "pipeline_id": "pipeline/test_pipeline",
        "data": ContentFile(content, filename),
        **fields,
        **extra,
    })
    new_jobs = Job.objects.exclude(pk__in=jobs_before)
    assert new_jobs.count() == 1, response.content
    job = new_jobs.get()
    return response.status_code, {
        "status": job.status,
        "error": job.error,
        "reference_genome": job.reference_genome,
        "annotation_type": job.annotation_type,
        "result": pathlib.Path(job.result_path).read_text(encoding="utf-8"),
    }


@pytest.mark.django_db
@pytest.mark.parametrize("url", list(UPLOADS))
def test_empty_genome_creates_the_same_job_as_no_genome(
    user_client: Client, url: str,
) -> None:
    expected = _post_job(user_client, url, {})

    actual = _post_job(user_client, url, {"genome": ""})

    assert expected[0] == 200
    assert expected[1]["status"] == Job.Status.SUCCESS
    assert actual == expected
