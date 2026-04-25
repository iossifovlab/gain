"""Module for thread-safe annotation utilities."""
import logging
import time
from collections.abc import Callable, Sequence
from concurrent.futures import CancelledError, Future
from dataclasses import dataclass
from threading import Lock, RLock
from types import TracebackType

from gain.annotation.annotatable import Annotatable
from gain.annotation.annotation_config import (
    AnnotationPreamble,
    AnnotatorInfo,
    AttributeInfo,
    RawPipelineConfig,
)
from gain.annotation.annotation_factory import load_pipeline_from_yaml
from gain.annotation.annotation_pipeline import AnnotationPipeline, Annotator
from gain.genomic_resources.repository import GenomicResourceRepo

from web_annotation.executor import ThreadedTaskExecutor

logger = logging.getLogger(__name__)


class ThreadSafePipeline(AnnotationPipeline):
    """Thread-safe annotation pipeline wrapper."""

    def __init__(
        self, pipeline: AnnotationPipeline,
    ):  # pylint: disable=super-init-not-called
        self.pipeline = pipeline
        self.lock = Lock()

    @property
    def annotators(self) -> list[Annotator]:  # type: ignore
        """Return the list of annotators in the pipeline."""
        return self.pipeline.annotators

    @property
    def preamble(self) -> AnnotationPreamble | None:  # type: ignore
        """Return the pipeline's preamble."""
        return self.pipeline.preamble

    @property
    def raw(self) -> RawPipelineConfig:  # type: ignore
        """Return the pipeline's raw configuration."""
        return self.pipeline.raw

    @property
    def repository(self) -> GenomicResourceRepo:  # type: ignore
        """Return the pipeline's repository"""
        return self.pipeline.repository

    @property
    def _is_open(self) -> bool:  # type: ignore
        """Return whether the pipeline is open."""
        return self.pipeline._is_open  # noqa: SLF001

    def get_info(self) -> list[AnnotatorInfo]:
        return self.pipeline.get_info()

    def get_attributes(self) -> list[AttributeInfo]:
        return self.pipeline.get_attributes()

    def get_attribute_info(
            self, attribute_name: str) -> AttributeInfo | None:
        return self.pipeline.get_attribute_info(attribute_name)

    def get_resource_ids(self) -> set[str]:
        return self.pipeline.get_resource_ids()

    def get_annotator_by_attribute_info(
        self, attribute_info: AttributeInfo,
    ) -> Annotator | None:
        return self.pipeline.get_annotator_by_attribute_info(attribute_info)

    def add_annotator(self, annotator: Annotator) -> None:
        with self.lock:
            self.pipeline.add_annotator(annotator)

    def annotate(
        self, annotatable: Annotatable | None,
        context: dict | None = None,
    ) -> dict:
        with self.lock:
            return self.pipeline.annotate(annotatable, context)

    def batch_annotate(
        self, annotatables: Sequence[Annotatable | None],
        contexts: list[dict] | None = None,
        batch_work_dir: str | None = None,
    ) -> list[dict]:
        with self.lock:
            return self.pipeline.batch_annotate(
                annotatables, contexts=contexts, batch_work_dir=batch_work_dir,
            )

    def open(self) -> AnnotationPipeline:
        with self.lock:
            return self.pipeline.open()

    def close(self) -> None:
        with self.lock:
            self.pipeline.close()

    def print(self) -> None:
        self.pipeline.print()

    def __enter__(self) -> AnnotationPipeline:
        return self

    def __exit__(
            self,
            exc_type: type[BaseException] | None,
            exc_value: BaseException | None,
            exc_tb: TracebackType | None) -> bool:
        if exc_type is not None:
            logger.error(
                "exception during annotation: %s, %s, %s",
                exc_type, exc_value, exc_tb)
        self.close()
        return exc_type is None


@dataclass
class LoadingDetails:
    """Utility for identifying which pipeline is being loaded."""
    time_started: float
    config_hash: int
    pipeline_id: str
    future: Future[ThreadSafePipeline]

    def __hash__(self) -> int:
        return hash(self.pipeline_id)


class LRUPipelineCache:
    """LRU cache that wraps and provides thread-safe annotation pipelines."""

    def __init__(
        self,
        grr: GenomicResourceRepo,
        capacity: int,
        load_workers: int = 8,
        load_timeout: float = 5 * 60,
    ):
        self._grr = grr
        self._load_executor = ThreadedTaskExecutor(
            max_workers=load_workers,
            job_timeout=load_timeout,
        )
        self._load_timeout = load_timeout

        self.capacity = capacity
        self._cache: dict[str, LoadingDetails] = {}
        self._pipeline_callbacks: dict[str, Callable | None] = {}
        self._cache_lock: RLock = RLock()
        self._order: list[str] = []

    def has_pipeline(
        self, pipeline_id: str,
    ) -> bool:
        """Check if a pipeline is in the cache."""
        with self._cache_lock:
            return pipeline_id in self._cache

    def is_pipeline_loaded(
        self, pipeline_id: str,
    ) -> bool:
        """Check if a pipeline is loaded."""
        with self._cache_lock:
            try:
                return self.get_pipeline_future(pipeline_id).done()
            except ValueError:
                return False

    @staticmethod
    def _load_pipeline_raw(
        raw: str,
        grr: GenomicResourceRepo,
    ) -> ThreadSafePipeline:
        pipeline = ThreadSafePipeline(load_pipeline_from_yaml(raw, grr))
        pipeline.open()
        return pipeline

    def put_pipeline(  # pylint: disable=too-many-arguments
        self,
        pipeline_id: str,
        pipeline_config: str,
        *,
        begin_load_callback: Callable[[], None] | None = None,
        finish_load_callback: Callable[[], None] | None = None,
        delete_callback: Callable[[LoadingDetails], None] | None = None,
        force: bool = False,
    ) -> None:
        """Put a pipeline into the cache."""
        pipeline_config_hash = hash(pipeline_config)
        started = time.time()
        with self._cache_lock:
            if pipeline_id in self._cache:
                details = self._cache[pipeline_id]
                if details.config_hash == pipeline_config_hash and not force:
                    return
                self.delete_pipeline(pipeline_id)

            pipeline_future = self._load_executor.execute(
                self._load_pipeline_raw,
                raw=pipeline_config,
                grr=self._grr,
                callback_start=begin_load_callback,
                callback_success=finish_load_callback,
            )

            loading_details = LoadingDetails(
                time_started=started,
                pipeline_id=pipeline_id,
                config_hash=pipeline_config_hash,
                future=pipeline_future,
            )

            if len(self._cache) >= self.capacity:
                last_pipeline_id = self._order[0]
                self.delete_pipeline(last_pipeline_id, do_cancel=False)
            self._pipeline_callbacks[pipeline_id] = delete_callback
            self._cache[pipeline_id] = loading_details
            self._order.append(pipeline_id)
        elapsed = time.time() - started
        logger.debug(
            "put pipeline %s in %.2f seconds", pipeline_id, elapsed)

    def clean_old_tasks(self) -> None:
        """Clean old tasks that have timed out"""
        to_remove = []
        now = time.time()
        with self._cache_lock:
            for pipeline_id, details in self._cache.items():
                if now - details.time_started > self._load_timeout:
                    logger.warning(
                        "Cancelling long-running task started at %s",
                        details.time_started,
                    )
                    to_remove.append(pipeline_id)
            for pipeline_id in to_remove:
                self.delete_pipeline(pipeline_id)

    def get_pipeline_future(
        self, pipeline_id: str,
    ) -> Future[ThreadSafePipeline]:
        """Get a pipeline future by its ID."""
        started = time.time()
        with self._cache_lock:
            if pipeline_id not in self._cache:
                raise ValueError(f"Pipeline {pipeline_id} not found")
            self._order.remove(pipeline_id)
            self._order.append(pipeline_id)
            elapsed = time.time() - started
            logger.debug(
                "get_pipeline_future %s in %.2f seconds", pipeline_id, elapsed)
            return self._cache[pipeline_id].future

    def get_pipeline(self, pipeline_id: str) -> ThreadSafePipeline:
        """Get a pipeline by its ID."""
        pipeline = None
        started = time.time()
        while pipeline is None:
            pipeline_future = self.get_pipeline_future(pipeline_id)
            try:
                pipeline = pipeline_future.result()
            except CancelledError:
                logger.debug("Retrying to get %s", pipeline_id)
        elapsed = time.time() - started
        logger.debug(
            "got pipeline %s in %.2f seconds", pipeline_id, elapsed)
        return pipeline

    def delete_pipeline(
        self, pipeline_id: str,
        *,
        do_cancel: bool = True,
    ) -> None:
        """Unload a pipeline from the cache."""
        with self._cache_lock:
            if pipeline_id in self._cache:
                details = self._cache[pipeline_id]
                future = details.future
                if future.done():
                    try:
                        future.result().close()
                    except Exception:  # pylint: disable=broad-except
                        logger.exception(
                            "Error during pipeline close for %s", pipeline_id)
                    delete_cb = self._pipeline_callbacks.get(pipeline_id)
                    if delete_cb:
                        try:
                            delete_cb(self._cache[pipeline_id])
                        except Exception:  # pylint: disable=broad-except
                            logger.exception(
                                "Error during pipeline deletion"
                                "callback for %s", pipeline_id)
                elif do_cancel:
                    future.cancel()

                del self._cache[pipeline_id]
                del self._pipeline_callbacks[pipeline_id]
                self._order.remove(pipeline_id)
