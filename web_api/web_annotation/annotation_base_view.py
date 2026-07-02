"""Module containing base view for annotation work."""
import gzip
from functools import partial
from pathlib import Path
from typing import Any, ClassVar, cast

import adrf.views
import gain.logging as logging
import yaml
from asgiref.sync import async_to_sync, sync_to_async
from channels.layers import get_channel_layer
from django.conf import settings
from django.core.files.uploadedfile import UploadedFile
from django.http import QueryDict
from gain.annotation.annotation_config import AnnotationConfigurationError
from gain.annotation.annotation_pipeline import AnnotationPipeline
from gain.genomic_resources.implementations.annotation_pipeline_impl import (
    AnnotationPipelineImplementation,
)
from gain.genomic_resources.repository import GenomicResourceRepo
from gain.genomic_resources.repository_factory import (
    build_genomic_resource_repository,
)
from rest_framework import views
from rest_framework.exceptions import NotFound, ValidationError
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
from web_annotation.pipeline_cache import (
    LRUPipelineCache,
    PipelineNotCached,
    ThreadSafePipeline,
)

logger = logging.getLogger(__name__)

GRR = build_genomic_resource_repository(file_name=settings.GRR_DEFINITION_PATH)


def format_config_error(exc: BaseException) -> str:
    """Render a pipeline-config build failure as a user-facing message.

    Single source of truth shared by the synchronous ``PipelineValidation``
    endpoint and the deferred-load failure path (#155). Known configuration
    errors carry their reason; anything else degrades to a bare message so
    internal exception text (e.g. server filesystem paths) is not leaked.
    """
    if isinstance(exc, (AnnotationConfigurationError, KeyError)):
        reason = str(exc)
        if reason:
            return f"Invalid configuration, reason: {reason}"
    return "Invalid configuration"


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
    if annotation_type == "tabular":
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


class AnnotationMixin:
    """Shared annotation state + helpers for the sync and async base views.

    The cache and executors live on this mixin's class body so they are
    instantiated *once* and shared across BOTH ``AnnotationBaseView`` (sync,
    ``rest_framework.views.APIView``) and ``AsyncAnnotationBaseView`` (async,
    ``adrf.views.APIView``). A pipeline built through the async path is
    therefore visible to the sync path and vice-versa (single-shared-cache
    invariant -- see iossifovlab/gain#163).
    """

    lru_cache = LRUPipelineCache(GRR, settings.PIPELINES_CACHE_SIZE)

    JOB_EXECUTOR: TaskExecutor = ThreadedTaskExecutor(
            max_workers=settings.ANNOTATION_MAX_WORKERS,
            job_timeout=settings.ANNOTATION_TASK_TIMEOUT,
            thread_name_prefix="annotation-job")

    #: Dedicated bounded pool for *interactive* ``pipeline.annotate(...)`` calls
    #: from async views (#163). Kept separate from ``JOB_EXECUTOR`` (file-job
    #: annotation) and from asgiref's default ``sync_to_async`` thread pool, so
    #: a burst of single-allele annotates cannot starve file jobs or the shared
    #: ORM/auth thread. Same-pipeline annotates still serialize on the
    #: per-pipeline ``LoggedLock`` -- out of scope here.
    ANNOTATE_EXECUTOR: TaskExecutor = ThreadedTaskExecutor(
            max_workers=8,
            job_timeout=settings.ANNOTATION_TASK_TIMEOUT,
            thread_name_prefix="interactive-annotate")

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

    def check_throttles(self, request: Request) -> None:
        """Override to disable throttling.

        ``super()`` resolves to the concrete ``APIView`` base at runtime via
        the MRO of ``AnnotationBaseView`` / ``AsyncAnnotationBaseView``; the
        mixin itself does not statically inherit ``APIView``.
        """
        if (
            (request.user.is_authenticated and not request.user.is_unlimited)
            or not request.user.is_authenticated
        ):
            super().check_throttles(request)  # type: ignore[misc]

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
        if user.is_superuser or getattr(user, "is_unlimited", False):
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
        self, pipeline_id: str, status: str, error: str | None = None,
    ) -> None:
        async_to_sync(self.channel_layer.group_send)(
            "global",
            {
                "type": "pipeline_status",
                "pipeline_id": pipeline_id,
                "status": status,
                "error": error,
            },
        )

    def _notify_user_pipeline(
        self, user: BaseUser, pipeline_id: str, status: str,
        error: str | None = None,
    ) -> None:
        group_id = user.get_socket_group()

        async_to_sync(self.channel_layer.group_send)(
            group_id,
            {
                "type": "pipeline_status",
                "pipeline_id": pipeline_id,
                "status": status,
                "error": error,
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

        def fail_load_callback(exc: BaseException) -> None:
            # Resource-resolving validation is deferred to this background
            # load (#150 H1), so a build failure here is the user's first and
            # only signal that the config is bad -- surface it with an
            # actionable reason (#155) instead of a bare status that is
            # indistinguishable from a delete.
            logger.warning(
                "background load of pipeline %s failed: %s", pipeline_id, exc)
            notify_function(pipeline_id, "failed", error=format_config_error(
                exc))

        def delete_callback(*_args: Any) -> None:
            notify_function(pipeline_id, "unloaded")

        self.lru_cache.put_pipeline(
            pipeline_id,
            pipeline_config,
            begin_load_callback=begin_load_callback,
            finish_load_callback=finish_load_callback,
            fail_load_callback=fail_load_callback,
            delete_callback=delete_callback,
            force=force,
        )

    #: Bounded number of reload-on-miss attempts in ``get_pipeline``. Caps
    #: the retry loop so a genuinely-missing pipeline still raises (4xx) and
    #: cache thrash cannot spin forever.
    GET_PIPELINE_MAX_ATTEMPTS: ClassVar[int] = 3

    @staticmethod
    def _build_error_to_drf(build_error: BaseException) -> ValidationError:
        """Return a DRF 400 for a deferred build failure.

        Shared by the sync ``get_pipeline`` and the async ``aget_pipeline`` so
        the two cannot drift. Validation is deferred to the background load
        (#150 H1), so an unbuildable saved pipeline first fails here; retrying
        is futile (the config is deterministically unbuildable), so surface a
        4xx client error instead of letting it escape as a 500. Returns the
        exception so the caller can ``raise ... from`` with the original cause.
        """
        logger.warning("pipeline failed to build: %s", build_error)
        return ValidationError(
            f"Pipeline could not be loaded: {build_error}")

    @staticmethod
    def _missing_pipeline_to_drf(pipeline_id: str) -> NotFound:
        """Return a DRF 404 for a genuinely-missing pipeline.

        Shared by the sync and async pipeline getters. After the reload bound
        is exhausted (or source resolution fails) the pipeline is genuinely not
        available; surface a 404 (via DRF) instead of re-raising the bare
        cache-miss as a 500.
        """
        return NotFound(f"Pipeline {pipeline_id} could not be loaded")

    def get_pipeline(
        self, pipeline_id: str, user: BaseUser,
    ) -> ThreadSafePipeline:
        """Get an annotation pipeline by id, reloading on a cache-miss.

        Pinning in ``LRUPipelineCache`` prevents *capacity-driven* eviction of
        an in-flight pipeline (#140), but residual removal windows remain: the
        check-then-act gap between ``has_pipeline``/``put_pipeline`` here and
        the pin taken inside ``lru_cache.get_pipeline``, the timeout reaper, or
        a force/config reload of the same id. Any of those surfaces a
        ``ValueError`` cache-miss from the cache.

        Recover by re-loading from the same source the view would normally use
        (``put_pipeline`` -> GRR / user pipeline) and retrying, up to
        ``GET_PIPELINE_MAX_ATTEMPTS``. The reload/put goes through the existing
        locked cache methods and the await inside ``lru_cache.get_pipeline``
        stays lockless, so thread-safety is preserved and no new lock is held
        across the await. After the bound is exhausted the original
        ``ValueError`` is re-raised so a genuinely-missing pipeline still 4xx's.
        """
        # Known sync/async divergence: the async ``_aput_pipeline_or_404``
        # wraps this ``put_pipeline`` to map a missing-pipeline source-
        # resolution failure (``ValueError``/``NotImplementedError``) to 404 --
        # the target behavior. The sync path leaves it bare, so the same
        # ``ValueError`` escapes as a 500. This is left unchanged in #163 to
        # avoid altering existing sync callers; 404 is the eventual goal.
        last_error: PipelineNotCached | None = None
        for attempt in range(self.GET_PIPELINE_MAX_ATTEMPTS):
            if not self.lru_cache.has_pipeline(pipeline_id):
                self.put_pipeline(pipeline_id, user)
            try:
                return self.lru_cache.get_pipeline(pipeline_id)
            except PipelineNotCached as error:
                # The entry vanished between put and the cache's pin (residual
                # eviction window), or was reaped / force-reloaded while we
                # awaited. Reload from source and retry rather than emit a
                # spurious 4xx for a pipeline that is actually available.
                last_error = error
                logger.warning(
                    "pipeline %s missed in cache on attempt %d/%d; "
                    "reloading and retrying",
                    pipeline_id, attempt + 1, self.GET_PIPELINE_MAX_ATTEMPTS,
                )
                self.put_pipeline(pipeline_id, user)
            except Exception as build_error:
                # The deferred background build itself failed (missing/invalid
                # resource, bad config -- the annotation factory raises these
                # as ValueError/AnnotationConfigurationError/etc, distinct from
                # the PipelineNotCached cache-miss above).
                raise self._build_error_to_drf(build_error) from build_error
        # Exhausted the reload-on-miss bound: the pipeline is genuinely not
        # available.
        raise self._missing_pipeline_to_drf(pipeline_id) from last_error

    async def aget_pipeline(
        self, pipeline_id: str, user: BaseUser,
    ) -> ThreadSafePipeline:
        """Async mirror of ``get_pipeline``: await the build off the loop.

        The reload-on-miss orchestration matches the sync version exactly (same
        bound, same ``ValidationError``/``NotFound`` mapping via the shared
        helpers). Two things must stay off the event-loop thread:

        * ``put_pipeline`` -- it fires channel callbacks that call
          ``async_to_sync(...)``, which RAISES if run on the loop thread; it is
          invoked via ``sync_to_async`` so its ``async_to_sync`` stays legal on
          a worker thread.
        * the GRR build wait -- delegated to ``lru_cache.aget_pipeline``, which
          awaits the shared build future via ``await_build``.

        The pin/unpin and ``has_pipeline`` bookkeeping inside the cache are
        microsecond lock operations and stay on the loop thread.
        """
        last_error: BaseException | None = None
        for attempt in range(self.GET_PIPELINE_MAX_ATTEMPTS):
            if not self.lru_cache.has_pipeline(pipeline_id):
                await self._aput_pipeline_or_404(pipeline_id, user)
            try:
                return await self.lru_cache.aget_pipeline(pipeline_id)
            except PipelineNotCached as error:
                last_error = error
                logger.warning(
                    "pipeline %s missed in cache on attempt %d/%d; "
                    "reloading and retrying",
                    pipeline_id, attempt + 1, self.GET_PIPELINE_MAX_ATTEMPTS,
                )
                await self._aput_pipeline_or_404(pipeline_id, user)
            except Exception as build_error:  # pylint: disable=broad-except
                raise self._build_error_to_drf(build_error) from build_error
        raise self._missing_pipeline_to_drf(pipeline_id) from last_error

    async def _aput_pipeline_or_404(
        self, pipeline_id: str, user: BaseUser,
    ) -> None:
        """Resolve + schedule a build via ``put_pipeline``, off the loop.

        Source resolution (GRR / user-pipeline lookup) happens here, before
        the build. A pipeline id that resolves to nothing raises a lookup error
        (``ValueError`` from the user models'
        ``get_pipeline``/``get_temporary_pipeline`` ``.filter().first()`` miss,
        or ``NotImplementedError`` from the anonymous user) -- that is a
        genuinely-missing pipeline, mapped to 404, distinct from a build
        failure (400). The user models resolve pipelines via ``.filter(
        ...).first()`` + ``ValueError`` (never ``.get()``), so ``DoesNotExist``
        / ``ObjectDoesNotExist`` is not reachable here and is deliberately
        absent from the catch tuple -- catching it would only mask unrelated
        errors as 404. Note the missing-config-file case (``FileNotFoundError``,
        an ``OSError`` from ``_get_user_pipeline_yaml``) is intentionally *not*
        caught, so a present-row/missing-file stays a 500. ``put_pipeline`` runs
        via ``sync_to_async`` so its ``async_to_sync`` channel callbacks stay
        legal on a worker thread.
        """
        try:
            await sync_to_async(self.put_pipeline)(pipeline_id, user)
        except (ValueError, NotImplementedError) as lookup_error:
            raise self._missing_pipeline_to_drf(
                pipeline_id) from lookup_error

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
        assert isinstance(request.user, BaseUser)
        if not request.user.is_unlimited:
            if not request.user.can_create():
                return Response(
                    {"reason": "Daily job limit reached!"},
                    status=views.status.HTTP_403_FORBIDDEN,
                )
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


class AnnotationBaseView(AnnotationMixin, views.APIView):
    """Synchronous base view for views which access annotation resources.

    Dispatch is unchanged from the original ``rest_framework.views.APIView``;
    every existing sync view keeps working untouched. Shared cache/executors
    and helpers come from ``AnnotationMixin``.
    """


class AsyncAnnotationBaseView(AnnotationMixin, adrf.views.APIView):
    """Async base view (``adrf``) for read views that await the GRR build.

    ``adrf`` picks the sync-vs-async dispatch per view via ``view_is_async``
    (true iff *all* handlers are coroutines); converted views must expose ONLY
    async handlers. Shares the same cache/executors as ``AnnotationBaseView``
    via ``AnnotationMixin`` -- see the single-shared-cache invariant.
    """
