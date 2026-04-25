"""Module with views for job operations."""
import gzip
import logging
import time
from pathlib import Path
from subprocess import CalledProcessError
from typing import Any, ClassVar, cast

from django.core.files.uploadedfile import UploadedFile
from django.db.models import ObjectDoesNotExist, QuerySet
from django.http import FileResponse, QueryDict
from django.shortcuts import get_object_or_404
from gain.annotation.annotation_factory import build_annotation_pipeline
from gain.annotation.record_to_annotatable import build_record_to_annotatable
from pysam import VariantFile
from rest_framework import generics, permissions, views
from rest_framework.parsers import MultiPartParser
from rest_framework.request import MultiValueDict
from rest_framework.views import Request, Response

from web_annotation.annotate_helpers import (
    columns_file_preview,
    extract_head,
    is_compressed_filename,
)
from web_annotation.annotation_base_view import (
    AnnotationBaseView,
    count_input_variants,
)
from web_annotation.authentication import WebAnnotationAuthentication
from web_annotation.models import (
    AnonymousJob,
    BaseUser,
    Job,
    User,
    UserWrapper,
    WebAnnotationAnonymousUser,
)
from web_annotation.permissions import has_job_permission
from web_annotation.serializers import JobSerializer
from web_annotation.tasks import (
    get_args_columns,
    get_args_vcf,
    run_columns_job,
    run_vcf_job,
    specify_job,
)
from web_annotation.utils import bytes_to_readable, validate_vcf

logger = logging.getLogger(__name__)


ANNOTATABLES = {
    "RecordToCNVAllele": "RecordToCNVAllele",
    "RecordToRegion": "RecordToRegion",
    "RecordToVcfAllele": "RecordToVcfAllele",
    "VcfLikeRecordToVcfAllele": "VcfLikeRecordToVcfAllele",
    "DaeAlleleRecordToAnnotatable": "DaeAlleleRecordToAnnotatable",
    "CSHLAlleleRecordToAnnotatable": "CSHLAlleleRecordToAnnotatable",
    "RecordToPosition": "RecordToPosition",
}


class ListGenomePipelines(AnnotationBaseView):
    """View for listing available single annotation genomes."""

    def get(self, _request: Request) -> Response:
        """Return list of genome pipelines for single annotation."""
        return Response(
            self.grr_genomes,
            status=views.status.HTTP_200_OK,
        )


class JobAll(generics.ListAPIView):
    """Generic view for listing all jobs."""
    authentication_classes: ClassVar = [WebAnnotationAuthentication]
    queryset = Job.objects.filter(is_active=True)
    serializer_class = JobSerializer
    permission_classes: ClassVar = [permissions.IsAdminUser]


class JobList(generics.ListAPIView):
    """Generic view for listing jobs for the user."""
    authentication_classes: ClassVar = [WebAnnotationAuthentication]

    def get_queryset(self) -> QuerySet:
        assert isinstance(
            self.request.user, (UserWrapper, WebAnnotationAnonymousUser))
        return Job.objects \
            .order_by("name") \
            .filter(
                owner=cast(User, self.request.user.as_owner), is_active=True)

    serializer_class = JobSerializer
    permission_classes: ClassVar = [permissions.IsAuthenticated]


class JobDetail(AnnotationBaseView):
    """View for listing job details."""

    authentication_classes: ClassVar = [WebAnnotationAuthentication]

    def get_job(
        self,
        user: User | WebAnnotationAnonymousUser,
        job_pk: int,
    ) -> Job | AnonymousJob:
        """Return a job by primary key."""
        return user.job_class.objects.get(
            pk=job_pk,
            is_active=True,
        )

    def get(self, request: Request, pk: int) -> Response:
        """
        Get job details.

        Returns extra column information for TSV/CSV jobs.
        """

        try:
            job = self.get_job(request.user, pk)
        except ObjectDoesNotExist:
            return Response(status=views.status.HTTP_404_NOT_FOUND)

        if not request.user.is_owner(job):
            return Response(status=views.status.HTTP_403_FORBIDDEN)

        response = {
            "id": job.pk,
            "name": job.name
                if isinstance(job, Job) else "anonymous_job",
            "owner": request.user.identifier,
            "created": str(job.created),
            "duration": job.duration,
            "command_line": job.command_line,
            "status": Job.Status(job.status).name.lower(),
            "result_filename": Path(job.result_path).name
                if isinstance(job, Job) else "result",
            "error": job.error,
            "size": bytes_to_readable(int(job.disk_size)),
        }
        try:
            details = job.get_job_details()
        except ObjectDoesNotExist:
            return Response(response, status=views.status.HTTP_200_OK)

        if job.annotation_type == "columns":
            response["columns"] = details.columns.split(";")
            file_head = extract_head(
                str(job.input_path),
                details.separator,
                n_lines=5,
            )
            response["head"] = file_head

        return Response(response, status=views.status.HTTP_200_OK)

    def delete(self, request: Request, pk: int) -> Response:
        """
        Delete job details.

        Returns extra column information for TSV/CSV jobs.
        """

        try:
            job = self.get_job(request.user, pk)
        except ObjectDoesNotExist:
            return Response(status=views.status.HTTP_404_NOT_FOUND)

        if job.owner != cast(User, request.user.as_owner):
            return Response(status=views.status.HTTP_403_FORBIDDEN)

        job.deactivate()

        return Response(status=views.status.HTTP_200_OK)


class AnnotateVCF(AnnotationBaseView):
    """View for creating jobs."""

    authentication_classes: ClassVar = [WebAnnotationAuthentication]

    parser_classes: ClassVar = [MultiPartParser]

    def _validate_vcf(
        self,
        file_path: str,
        user: User,
    ) -> bool:
        """Check if a variants file does not exceed the variants limit."""
        limit = self.max_variants if not user.is_superuser else None

        return validate_vcf(file_path, limit)

    def post(self, request: Request) -> Response:
        """Run VCF annotation job."""
        job_or_response = self._create_job(request, "vcf")
        if isinstance(job_or_response, Response):
            return job_or_response
        job_name, pipeline, job = job_or_response

        work_folder_name = request.user.identifier

        try:
            if not self._validate_vcf(
                job.input_path,
                request.user,
            ):
                self._cleanup(job_name, work_folder_name)
                return Response(
                    status=views.status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)
        except CalledProcessError as e:
            self._cleanup(job_name, work_folder_name)
            return Response(
                {"reason": str(e.stderr)},
                status=views.status.HTTP_400_BAD_REQUEST)

        job.save()
        work_dir = self.result_storage_dir / work_folder_name
        pipeline = build_annotation_pipeline(pipeline.raw, pipeline.repository)
        args = get_args_vcf(
            job, pipeline, str(work_dir))
        start_time = time.time()

        def on_success() -> None:
            """Callback when annotation is done."""
            job.duration = time.time() - start_time
            job.disk_size += Path(job.result_path).stat().st_size
            job.update_job_success(str(args))
            self._notify_user_job(request.user, str(job.pk), job.status)

            assert isinstance(request.user, BaseUser)
            attributes_count = sum(
                1 for annotator in pipeline.annotators
                for attr in annotator.attributes
                if not attr.internal
            )
            variants_count = count_input_variants(
                job.input_path, job.annotation_type,
            )
            request.user.get_quota().job_complete(
                variants_count, attributes_count,
            )

        def on_failure(exception: BaseException) -> None:
            """Callback when annotation fails."""
            logger.error(
                "VCF annotation job failed with exception: %s", exception,
            )
            job.duration = time.time() - start_time
            reason = (
                f"Unexpected error, {type(exception)}\n"
                f"{exception!s}"
            )
            if isinstance(exception, CalledProcessError):
                reason = (
                    "annotate_vcf failed internally:\n"
                    f"{exception.stderr}"
                )
            if isinstance(
                exception, (OSError, TypeError, ValueError),
            ):
                reason = (
                    "Failed to execute annotate_vcf\n"
                    f"{exception!s}"
                )
            logger.error("VCF annotation job failed!\n%s", reason)
            job.update_job_failed(str(args), str(exception))
            self._notify_user_job(request.user, str(job.pk), job.status)

        def run_vcf_wrapper(*args: Any, **kwargs: Any) -> None:
            """Wrapper to run VCF job."""
            job.update_job_in_progress()
            self._notify_user_job(request.user, str(job.pk), job.status)
            run_vcf_job(*args, **kwargs)

        self._notify_user_job(request.user, str(job.pk), job.status)

        self.JOB_EXECUTOR.execute(
            run_vcf_wrapper,
            callback_success=on_success,
            callback_failure=on_failure,
            **args,
        )

        return Response(
            {"job_id": str(job.pk)}, status=views.status.HTTP_200_OK,
        )


class AnnotateColumns(AnnotationBaseView):
    """View for creating jobs."""

    authentication_classes: ClassVar = [WebAnnotationAuthentication]

    parser_classes: ClassVar = [MultiPartParser]

    def is_vcf_file(self, file: UploadedFile, input_path: Path) -> bool:
        """Check if a file is a VCF file."""
        assert file.name is not None
        if file.name.endswith(".vcf"):
            return True
        if file.name.endswith(".tsv") or file.name.endswith(".csv"):
            return False

        try:
            VariantFile(str(input_path.absolute()), "r")
        except ValueError:
            return False
        except OSError:
            return False

        return True

    def check_variants_limit(self, filepath: Path, user: User) -> bool:
        """Check if a variants file does not exceed the variants limit."""
        if user.is_superuser:
            return True

        opener = (
            gzip.open(filepath, "rt")  # noqa: SIM115
            if is_compressed_filename(str(filepath))
            else filepath.open("rt")
        )
        with opener as file:
            for i, _ in enumerate(file, start=-1):
                if i >= self.max_variants:
                    logger.debug(
                        "User %s exceeded max variants limit: %d",
                        user.identifier, self.max_variants,
                    )
                    return False
            return True

    def post(self, request: Request) -> Response:
        """Run column annotation job."""

        job_or_response = self._create_job(request, "columns")
        if isinstance(job_or_response, Response):
            return job_or_response
        _, pipeline, job = job_or_response

        job.save()

        assert isinstance(request.data, QueryDict)
        if not any(param in self.tool_columns for param in request.data):
            logger.debug("No column options sent in request body!")
            job.delete()
            return Response(
                {"reason": "Invalid column specification!"},
                status=views.status.HTTP_400_BAD_REQUEST)

        if self.check_variants_limit(
                Path(job.input_path), request.user) is False:
            job.delete()
            return Response(
                status=views.status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)

        sep = request.data.get("separator")

        params = {"separator": sep}
        for col in self.tool_columns:
            params[col] = request.data.get(col, "")
            assert isinstance(params[col], str)

        grr_definition = self.get_grr_definition()
        assert grr_definition is not None
        work_dir = self.result_storage_dir / request.user.identifier

        try:
            specify_job(job, **cast(dict[str, str], params))
            details = job.get_job_details()
        except ObjectDoesNotExist:
            logger.exception("Job not found!")
            job.delete()
            return Response(status=views.status.HTTP_404_NOT_FOUND)

        pipeline = build_annotation_pipeline(pipeline.raw, pipeline.repository)
        args = get_args_columns(
            job, details, pipeline, str(work_dir))
        start_time = time.time()

        def on_success() -> None:
            job.duration = time.time() - start_time
            job.disk_size += Path(job.result_path).stat().st_size
            job.update_job_success(str(args))
            self._notify_user_job(request.user, str(job.pk), job.status)

            assert isinstance(request.user, BaseUser)
            attributes_count = sum(
                1 for annotator in pipeline.annotators
                for attr in annotator.attributes
                if not attr.internal
            )
            variants_count = count_input_variants(
                job.input_path, job.annotation_type,
            )
            request.user.get_quota().job_complete(
                variants_count, attributes_count,
            )

        def on_failure(exception: BaseException) -> None:
            job.duration = time.time() - start_time
            reason = (
                f"Unexpected error, {type(exception)}\n"
                f"{(exception)}"
            )
            if isinstance(exception, CalledProcessError):
                reason = (
                    "annotate_columns failed internally:\n"
                    f"{exception.stderr}"
                )
            if isinstance(
                exception, (OSError, TypeError, ValueError),
            ):
                reason = (
                    "Failed to execute annotate_vcf\n"
                    f"{exception!s}"
                )
            logger.error("columns annotation job failed!\n%s", reason)
            job.update_job_failed(str(args), str(exception))
            self._notify_user_job(request.user, str(job.pk), job.status)

        def run_columns_wrapper(*args: Any, **kwargs: Any) -> None:
            """Wrapper to run VCF job."""
            job.update_job_in_progress()
            self._notify_user_job(request.user, str(job.pk), job.status)
            run_columns_job(*args, **kwargs)

        self._notify_user_job(request.user, str(job.pk), job.status)

        self.JOB_EXECUTOR.execute(
            run_columns_wrapper,
            callback_success=on_success,
            callback_failure=on_failure,
            **args,
        )

        return Response(
            {"job_id": str(job.pk)}, status=views.status.HTTP_200_OK)


class ColumnValidation(AnnotationBaseView):
    """Validate if column selection returns annotatable."""

    authentication_classes: ClassVar = [WebAnnotationAuthentication]

    ANNOTATABLES: ClassVar = {
        "RecordToCNVAllele": "CNV Allele",
        "RecordToRegion": "Region",
        "RecordToVcfAllele": "VCF Allele",
        "VcfLikeRecordToVcfAllele": "VCF Like Allele",
        "DaeAlleleRecordToAnnotatable": "DAE Allele",
        "CSHLAlleleRecordToAnnotatable": "CSHL Allele",
        "RecordToPosition": "Position",
    }

    def post(self, request: Request) -> Response:
        """Validate columns selection."""
        data = request.data
        assert isinstance(data, dict)

        all_columns = data.get("file_columns")
        if not all_columns or all_columns == []:
            return Response({
                    "annotatable": "",
                    "errors": (
                        "File header must be provided "
                        "for column validation!"
                    ),
                },
                status=views.status.HTTP_200_OK)
        assert isinstance(all_columns, list)
        all_columns = [str(col) for col in all_columns]

        column_mapping = data.get("column_mapping", {})
        assert isinstance(column_mapping, dict)

        if any(param not in self.tool_columns for param in column_mapping):
            return Response(
                {"annotatable": "", "errors": "Invalid column specification!"},
                status=views.status.HTTP_200_OK)

        try:
            annotatable_name = type(
                build_record_to_annotatable(
                    column_mapping,
                    set(all_columns),
                ),
            ).__name__
            annotatable_name = self.ANNOTATABLES.get(
                annotatable_name,
                annotatable_name,
            )
        except ValueError:
            logger.exception("Annotatable error.\n")
            return Response(
                {
                    "annotatable": "",
                    "errors": (
                        "Cannot build annotatable from selected columns!"
                    ),
                },
                status=views.status.HTTP_200_OK)

        return Response(
            {"annotatable": ANNOTATABLES.get(
                annotatable_name,
                annotatable_name,
            ), "errors": ""},
            status=views.status.HTTP_200_OK,
        )


class JobGetFile(views.APIView):
    """View for downloading job files."""

    authentication_classes: ClassVar = [WebAnnotationAuthentication]

    def get(
        self, request: Request, pk: int, file: str,
    ) -> Response | FileResponse:
        """Download a file from a job."""
        job = get_object_or_404(request.user.job_class, id=pk, is_active=True)
        if not has_job_permission(job, request.user):
            return Response(status=views.status.HTTP_403_FORBIDDEN)

        if file == "input":
            file_path = Path(job.input_path)
        elif file == "config":
            file_path = Path(job.config_path)
        elif file == "result":
            file_path = Path(job.result_path)
        else:
            return Response(
                {"reason": "Not requesting input, config or result file!"},
                status=views.status.HTTP_400_BAD_REQUEST,
            )

        if not file_path.exists():
            return Response(status=views.status.HTTP_404_NOT_FOUND)

        if isinstance(job, Job):
            filename = file_path.name
        else:
            filename = file + file_path.suffix

        return FileResponse(
            open(file_path, "rb"),
            as_attachment=True,
            filename=filename,
        )


class PreviewFileUpload(AnnotationBaseView):
    """Try to determine the separator of a file split into columns"""

    authentication_classes: ClassVar = [WebAnnotationAuthentication]

    def post(self, request: Request) -> Response:
        """Determine the separator of a file split into columns."""
        assert isinstance(request.FILES, MultiValueDict)
        assert isinstance(request.data, QueryDict)

        file = request.FILES["data"]
        assert isinstance(file, UploadedFile)
        if file is None:
            return Response(
                {"reason": "No preview file provided!"},
                status=views.status.HTTP_400_BAD_REQUEST,
            )
        if not self.check_valid_upload_size(file, request.user):
            return Response(
                status=views.status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)

        assert file.name is not None

        if file.name.find(".vcf") > 0:
            return Response(
                {"reason": "VCF files cannot be previewed!"},
                status=views.status.HTTP_400_BAD_REQUEST,
            )

        preview_data = columns_file_preview(
            file, request.data.get("separator"))
        return Response(
            preview_data,
            status=views.status.HTTP_200_OK,
        )
