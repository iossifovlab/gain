"""Web annotation tasks"""
import logging
from datetime import timedelta
from typing import Any

from gain.annotation.annotate_columns import annotate_columns
from gain.annotation.annotate_vcf import annotate_vcf
from gain.annotation.annotation_pipeline import AnnotationPipeline
from gain.genomic_resources.reference_genome import (
    ReferenceGenome,
    build_reference_genome_from_resource,
)
from django.conf import settings

from django.utils import timezone

from .models import AnonymousJob, AnonymousJobDetails, BaseJob, Job, JobDetails

logger = logging.getLogger(__name__)


def specify_job(  # pylint: disable=too-many-arguments
    job: Job | AnonymousJob,
    *,
    separator: str | None = None,
    col_chrom: str = "",
    col_pos: str = "",
    col_ref: str = "",
    col_alt: str = "",
    col_pos_beg: str = "",
    col_pos_end: str = "",
    col_cnv_type: str = "",
    col_vcf_like: str = "",
    col_variant: str = "",
    col_location: str = "",
) -> Job| AnonymousJob:
    """Specify and update a job's annotation columns."""
    details = job.get_job_details()
    if job.status != Job.Status.WAITING:
        raise ValueError("Cannot specify details for jobs in execution!")
    details.separator = separator
    details.col_chr = col_chrom
    details.col_pos = col_pos
    details.col_ref = col_ref
    details.col_alt = col_alt
    details.col_pos_beg = col_pos_beg
    details.col_pos_end = col_pos_end
    details.col_cnv_type = col_cnv_type
    details.col_vcf_like = col_vcf_like
    details.col_variant = col_variant
    details.col_location = col_location
    job.status = Job.Status.WAITING
    job.save()
    details.save()

    return job


def get_args_vcf(
    job: BaseJob, pipeline: AnnotationPipeline, storage_dir: str,
) -> dict[str, Any]:
    """Prepare command line arguments for VCF annotation."""
    return {
        "input_path": str(job.input_path),
        "pipeline": pipeline,
        "output_path": str(job.result_path),
        "args": {
            "work_dir": storage_dir,
        }
    }


def run_vcf_job(
    input_path: str,
    pipeline: AnnotationPipeline,
    output_path: str,
    args: dict[str, Any],
) -> None:
    """Run a VCF annotation."""
    logger.debug("Running vcf job")
    logger.debug("%s, %s %s %s", input_path, pipeline, output_path, args)

    annotate_vcf(input_path, pipeline, output_path, args)


def delete_old_jobs(days_old: int = 0) -> None:
    """Delete old job resources and make jobs invalid"""
    time_delta = timezone.now() - timedelta(days=days_old)
    old_jobs = Job.objects.filter(
        created__lte=time_delta,
    )

    for job in list(old_jobs):
        job.deactivate()


def get_args_columns(
    job: Job | AnonymousJob,
    details: JobDetails | AnonymousJobDetails,
    pipeline: AnnotationPipeline, storage_dir: str,
) -> dict[str, Any]:
    """Prepare command line arguments for columnar annotation."""

    columns_args = {}
    columns = []
    if details.col_chr:
        columns_args["col_chrom"] = details.col_chr
        columns.append(details.col_chr)
    if details.col_pos:
        columns_args["col_pos"] = details.col_pos
        columns.append(details.col_pos)
    if details.col_ref:
        columns_args["col_ref"] = details.col_ref
        columns.append(details.col_ref)
    if details.col_alt:
        columns_args["col_alt"] = details.col_alt
        columns.append(details.col_alt)
    if details.col_pos_beg:
        columns_args["col_pos_beg"] = details.col_pos_beg
        columns.append(details.col_pos_beg)
    if details.col_pos_end:
        columns_args["col_pos_end"] = details.col_pos_end
        columns.append(details.col_pos_end)
    if details.col_cnv_type:
        columns_args["col_cnv_type"] = details.col_cnv_type
        columns.append(details.col_cnv_type)
    if details.col_vcf_like:
        columns_args["col_vcf_like"] = details.col_vcf_like
        columns.append(details.col_vcf_like)
    if details.col_variant:
        columns_args["col_variant"] = details.col_variant
        columns.append(details.col_variant)
    if details.col_location:
        columns_args["col_location"] = details.col_location
        columns.append(details.col_location)

    args: dict[str, Any] = {
        "work_dir": storage_dir,
        "columns_args": columns_args,
    }

    if details.separator is not None:
        args["input_separator"] = details.separator
        args["output_separator"] = details.separator
    else:
        args["input_separator"] = ","
        args["output_separator"] = ","

    fn_args = {
        "input_path": str(job.input_path),
        "pipeline": pipeline,
        "output_path": str(job.result_path),
        "args": args,
    }
    if job.reference_genome != "":
        fn_args["reference_genome"] = build_reference_genome_from_resource(
            pipeline.repository.get_resource(job.reference_genome)
        )

    return fn_args


def run_columns_job(  # pylint: disable=too-many-branches
    input_path: str,
    pipeline: AnnotationPipeline,
    output_path: str,
    args: dict[str, Any],
    reference_genome: ReferenceGenome | None = None,
) -> None:
    """Run a columnar annotation."""
    logger.debug("Running columns job")
    logger.debug(args)
    annotate_columns(
        input_path, pipeline, output_path,
        args, reference_genome=reference_genome)
def clean_old_jobs() -> None:
    """Task for running annotation."""
    delete_old_jobs(settings.JOB_CLEANUP_INTERVAL_DAYS)
