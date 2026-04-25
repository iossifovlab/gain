"""Module containing base view for annotation work."""
import gzip
import logging
from functools import partial
from pathlib import Path
from typing import Any, ClassVar, cast

import yaml
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.conf import settings
from django.core.files.uploadedfile import UploadedFile
from django.http import QueryDict
from gain.annotation.annotation_pipeline import AnnotationPipeline
from gain.genomic_resources.implementations.annotation_pipeline_impl import (
    AnnotationPipelineImplementation,
)
from gain.genomic_resources.repository import GenomicResourceRepo
from gain.genomic_resources.repository_factory import (
    build_genomic_resource_repository,
)
from rest_framework import views
from rest_framework.request import MultiValueDict
from rest_framework.views import Request, Response

from web_annotation.executor import (
    TaskExecutor,
    ThreadedTaskExecutor,
)
from web_annotation.models import (
    AnonymousJob,
    BasePipeline,
    BaseUser,
    Job,
    User,
)
from web_annotation.pipeline_cache import LRUPipelineCache, ThreadSafePipeline

logger = logging.getLogger(__name__)

GRR = build_genomic_resource_repository(file_name=settings.GRR_DEFINITION_PATH)


def get_grr_pipelines(grr: GenomicResourceRepo) -> dict[str, dict[str, str]]:
    """Return pipelines used for file annotation."""
    pipelines: dict[str, dict[str, str]] = {}
    for resource in grr.get_all_resources():
        if resource.get_type() == "annotation_pipeline":
            impl = AnnotationPipelineImplementation(resource)
            pipelines[resource.get_id()] = {
                "id": resource.get_id(),
                "content": impl.raw,
            }
    return pipelines


GRR_PIPELINES = get_grr_pipelines(GRR)


def count_input_variants(input_path: str, annotation_type: str) -> int:
    """Count variant lines in an annotation input file."""
    path = Path(input_path)
    if not path.exists():
        return 0
    open_fn = gzip.open if str(input_path).endswith((".gz", ".bgz")) else open
    count = sum(
        1 for line in open_fn(str(input_path), "rt")
        if line.strip() and not line.startswith("#")
    )
    # Columnar input files have one header line not prefixed with '#'
    if annotation_type == "columns":
        return max(0, count - 1)
    return count


def get_grr_genomes(grr: GenomicResourceRepo) -> list[str]:
    """Return pipelines used for file annotation."""
    return [
        resource.get_id()
        for resource in grr.get_all_resources()
        if resource.get_type() == "genome"
    ]


GRR_GENOMES = get_grr_genomes(GRR)


class AnnotationBaseView(views.APIView):
    """Base view for views which access annotation resources."""

    lru_cache = LRUPipelineCache(GRR, settings.PIPELINES_CACHE_SIZE)

    JOB_EXECUTOR: TaskExecutor = ThreadedTaskExecutor(
            max_workers=settings.ANNOTATION_MAX_WORKERS,
            job_timeout=settings.ANNOTATION_TASK_TIMEOUT)\

    """Base view for views which access annotation resources."""
    tool_columns: ClassVar = [
        "col_chrom",
        "col_pos",
        "col_ref",
        "col_alt",
        "col_pos_beg",
        "col_pos_end",
        "col_cnv_type",
        "col_vcf_like",
        "col_variant",
        "col_location",
    ]

    def __init__(self) -> None:
        super().__init__()
        self._grr = GRR
        self.grr_pipelines = GRR_PIPELINES
        self.grr_genomes = GRR_GENOMES
        self.result_storage_dir = Path(settings.JOB_RESULT_STORAGE_DIR)
        channel_layer = get_channel_layer()
        assert channel_layer is not None
        self.channel_layer = channel_layer

    @property
    def grr(self) -> GenomicResourceRepo:
        """Return annotation GRR."""
        return self.get_grr()

    def get_grr(self) -> GenomicResourceRepo:
        """Return annotation GRR."""
        return self._grr

    def get_grr_definition(self) -> Path | None:
        """Return annotation GRR definition."""
        path = settings.GRR_DEFINITION_PATH
        if path is None:
            return path
        return Path(path)

    @property
    def max_variants(self) -> int:
        """Return max variants allowed per job."""
        return cast(int, settings.QUOTAS["variant_count"])

    @staticmethod
    def _convert_size(filesize: str | int) -> int:
        """Convert a human readable filesize string to bytes."""
        if isinstance(filesize, int):
            return filesize
        filesize = filesize.upper()
        units: dict[str, int] = {
            "KB": 10**3, "MB": 10**6, "GB": 10**9, "TB": 10**12,
            "K": 10**3, "M": 10**6, "G": 10**9, "T": 10**12,
        }
        for unit, mult in units.items():
            if filesize.endswith(unit):
                return int(filesize.rstrip(f"{unit}")) * mult
        return int(filesize)

    def check_valid_upload_size(
        self,
        file: UploadedFile,
        user: User,
    ) -> bool:
        """Check if a file upload does not exceed the upload size limit."""
        if user.is_superuser:
            return True
        assert file.size is not None
        return file.size < self._convert_size(
            cast(str, settings.QUOTAS["filesize"]),
        )

    def _get_user_pipeline_yaml(
        self,
        user_pipeline: BasePipeline,
    ) -> str:
        return Path(user_pipeline.config_path).read_text(encoding="utf-8")

    def _notify_global_pipeline(
        self, pipeline_id: str, status: str,
    ) -> None:
        async_to_sync(self.channel_layer.group_send)(
            "global",
            {
                "type": "pipeline_status",
                "pipeline_id": pipeline_id,
                "status": status,
            },
        )

    def _notify_user_pipeline(
        self, user: BaseUser, pipeline_id: str, status: str,
    ) -> None:
        group_id = user.get_socket_group()

        async_to_sync(self.channel_layer.group_send)(
            group_id,
            {
                "type": "pipeline_status",
                "pipeline_id": pipeline_id,
                "status": status,
            },
        )

    def _notify_user_job(
        self, user: User, job_id: str, status: int,
    ) -> None:
        group_id = str(user.get_socket_group())

        async_to_sync(self.channel_layer.group_send)(
            group_id,
            {
                "type": "job_status",
                "job_id": job_id,
                "status": Job.Status(status).name.lower(),
            },
        )

    def put_pipeline(
        self, pipeline_id: str,
        user: BaseUser,
    ) -> None:
        """Load an annotation pipeline by ID and notify the user channel."""
        force = False
        if pipeline_id in self.grr_pipelines:
            pipeline_config = self.grr_pipelines[pipeline_id]["content"]
            notify_function = self._notify_global_pipeline
        else:
            pipeline = user.get_temporary_pipeline(pipeline_id)
            if pipeline is None:
                pipeline = user.get_pipeline(pipeline_id)
            else:
                force = True
            pipeline_config = self._get_user_pipeline_yaml(pipeline)
            notify_function = partial(self._notify_user_pipeline, user)

        def begin_load_callback() -> None:
            notify_function(pipeline_id, "loading")

        def finish_load_callback() -> None:
            notify_function(pipeline_id, "loaded")

        def delete_callback(*_args: Any) -> None:
            notify_function(pipeline_id, "unloaded")

        self.lru_cache.put_pipeline(
            pipeline_id,
            pipeline_config,
            begin_load_callback=begin_load_callback,
            finish_load_callback=finish_load_callback,
            delete_callback=delete_callback,
            force=force,
        )

    def get_pipeline(
        self, pipeline_id: str, user: BaseUser,
    ) -> ThreadSafePipeline:
        """Get an annotation pipeline by id."""
        if not self.lru_cache.has_pipeline(pipeline_id):
            self.put_pipeline(pipeline_id, user)
        return self.lru_cache.get_pipeline(pipeline_id)

    def get_genome(self, data: QueryDict) -> str:
        """Get genome from a request."""
        genome = data.get("genome")
        if genome is None:
            return ""
        if genome not in GRR_GENOMES:
            raise ValueError(
                "Genome not matching any id of grr genome resource!")
        return genome

    def _save_annotation_config(
        self,
        request: Request,
        config_path: Path,
    ) -> Response | AnnotationPipeline:
        assert isinstance(request.data, QueryDict)
        assert isinstance(request.FILES, MultiValueDict)
        if "pipeline_id" not in request.data:
            raise ValueError("Pipeline id not provided!")
        try:
            pipeline_id = request.data["pipeline_id"]
            if not isinstance(pipeline_id, str):
                raise TypeError("Pipeline id is not a string!")  # noqa: TRY301
            pipeline = self.get_pipeline(pipeline_id, request.user)
            if pipeline is None:
                raise KeyError(f"Pipeline {pipeline_id} not found!")
        except (ValueError, TypeError) as e:
            return Response(
                {"reason": str(e)},
                status=views.status.HTTP_400_BAD_REQUEST,
            )

        try:
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(yaml.safe_dump(
                pipeline.raw, sort_keys=False))
        except OSError:
            logger.exception("Could not write config file")
            return Response(
                {"reason": "Could not write file!"},
                status=views.status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return pipeline

    def _save_input_file(
        self,
        request: Request,
        input_path: Path,
    ) -> None:
        assert isinstance(request.data, QueryDict)
        assert isinstance(request.FILES, MultiValueDict)
        uploaded_file = request.FILES["data"]
        assert isinstance(uploaded_file, UploadedFile)

        input_path.parent.mkdir(parents=True, exist_ok=True)
        input_path.write_bytes(uploaded_file.read())

    def _cleanup(self, job_name: int, folder_name: str) -> None:
        """Cleanup the files of a failed job."""
        data_filename = f"data-{job_name}"
        inputs = Path(settings.JOB_INPUT_STORAGE_DIR).glob(
            f"{folder_name}/{data_filename}*")
        for in_file in inputs:
            in_file.unlink(missing_ok=True)
        config_filename = f"config-{job_name}.yaml"
        config_path = Path(
            settings.ANNOTATION_CONFIG_STORAGE_DIR,
            f"{folder_name}/{config_filename}",
        )
        config_path.unlink(missing_ok=True)
        results = Path(
            settings.JOB_RESULT_STORAGE_DIR).glob(
                f"{folder_name}/{data_filename}*")
        for out_file in results:
            out_file.unlink(missing_ok=True)

    def _validate_request(self, request: Request) -> Response | None:
        """Validate the request for creating a job."""
        if not request.user.can_create():
            return Response(
                {"reason": "Daily job limit reached!"},
                status=views.status.HTTP_403_FORBIDDEN,
            )
        assert isinstance(request.user, BaseUser)
        quota = request.user.get_quota()
        if not quota.check_job_quota():
            return Response(
                {"reason": "Job quota exceeded!"},
                status=views.status.HTTP_403_FORBIDDEN,
            )
        if not request.content_type.startswith("multipart/form-data"):
            return Response(
                {"reason": "Invalid content type!"},
                status=views.status.HTTP_400_BAD_REQUEST,
            )

        assert request.data is not None
        assert isinstance(request.data, QueryDict)
        assert isinstance(request.FILES, MultiValueDict)

        genome = request.data.get("genome")

        if genome and genome not in GRR_GENOMES:
            return Response(
                {"reason": f"Genome {genome} is not a valid option!"},
                status=views.status.HTTP_404_NOT_FOUND,
            )

        return None

    def _basic_file_extension(self, file: UploadedFile, separator: str) -> str:
        assert file.name is not None

        if separator == "\t":
            return ".tsv"
        if separator == ",":
            return ".csv"
        if file.name.find(".vcf") > 0:
            return ".vcf"
        return ".txt"

    def _file_extension(self, request: Request) -> str:
        assert isinstance(request.data, QueryDict)
        assert isinstance(request.FILES, MultiValueDict)

        uploaded_file = request.FILES["data"]
        assert isinstance(uploaded_file, UploadedFile)
        assert uploaded_file.name is not None
        separator = request.data.get("separator")
        ext = self._basic_file_extension(uploaded_file, cast(str, separator))

        if uploaded_file.name.endswith(".gz"):
            ext = f"{ext}.gz"
        if uploaded_file.name.endswith(".bgz"):
            ext = f"{ext}.bgz"

        return ext

    def get_config_path(self, job_name: int, user: User) -> Path:
        config_filename = f"config-{job_name}.yaml"
        return Path(
            settings.ANNOTATION_CONFIG_STORAGE_DIR,
            user.identifier,
            config_filename,
        )

    def _create_job(
        self,
        request: Request,
        annotation_type: str,
    ) -> Response | tuple[int, AnnotationPipeline, Job | AnonymousJob]:
        validation_response = self._validate_request(request)
        if validation_response is not None:
            return validation_response

        assert request.data is not None
        assert isinstance(request.data, QueryDict)
        assert isinstance(request.FILES, MultiValueDict)

        try:
            reference_genome = self.get_genome(request.data)
        except ValueError as e:
            return Response(
                {"reason": str(e)},
                status=views.status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        job_name = request.user.generate_job_name()

        config_path = self.get_config_path(job_name, request.user)

        save_response_or_pipeline = self._save_annotation_config(
            request, config_path)
        if isinstance(save_response_or_pipeline, Response):
            return save_response_or_pipeline
        pipeline = save_response_or_pipeline

        uploaded_file = request.FILES["data"]
        assert isinstance(uploaded_file, UploadedFile)
        if uploaded_file is None:
            return Response(
                {"reason": "No file uploaded!"},
                status=views.status.HTTP_400_BAD_REQUEST,
            )
        if not self.check_valid_upload_size(uploaded_file, request.user):
            return Response(
                status=views.status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)

        file_ext = self._file_extension(request)

        data_filename = f"data-{job_name}{file_ext}"
        input_path = Path(
            settings.JOB_INPUT_STORAGE_DIR,
            request.user.identifier,
            data_filename,
        )

        try:
            self._save_input_file(request, input_path)
        except OSError:
            logger.exception("Could not write input file")

            self._cleanup(job_name, request.user.identifier)
            return Response(
                {"reason": "File could not be identified"},
                status=views.status.HTTP_400_BAD_REQUEST,
            )

        result_filename = f"result-{job_name}{file_ext}"
        result_path = Path(
            settings.JOB_RESULT_STORAGE_DIR,
            request.user.identifier,
            result_filename,
        )
        result_path.parent.mkdir(parents=True, exist_ok=True)

        job_size = (
            input_path.stat().st_size + config_path.stat().st_size
        )

        job = request.user.create_job(
            name=job_name,
            input_path=input_path,
            config_path=config_path,
            result_path=result_path,
            reference_genome=reference_genome,
            annotation_type=annotation_type,
            disk_size=job_size,
        )
        return (job_name, pipeline, job)
