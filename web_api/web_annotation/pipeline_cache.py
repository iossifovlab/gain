"""Module for thread-safe annotation utilities."""
import logging
import threading
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
    Attribute,
    RawPipelineConfig,
)
from gain.annotation.annotation_factory import load_pipeline_from_yaml
from gain.annotation.annotation_pipeline import AnnotationPipeline, Annotator
from gain.genomic_resources.repository import GenomicResourceRepo

from web_annotation.executor import ThreadedTaskExecutor

logger = logging.getLogger(__name__)


class LoggedLock:
    """Lock wrapper that logs acquire and release events."""

    def __init__(self, name: str) -> None:
        self._lock = Lock()
        self._name = name

    def __enter__(self) -> "LoggedLock":
        thread = threading.current_thread().name
        logger.debug("[%s] thread %s requesting lock", self._name, thread)
        self._lock.acquire()
        logger.debug("[%s] thread %s acquired lock", self._name, thread)
        return self

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc_value: BaseException | None,
        _exc_tb: TracebackType | None,
    ) -> None:
        thread = threading.current_thread().name
        self._lock.release()
        logger.debug("[%s] thread %s released lock", self._name, thread)


class LoggedRLock:
    """RLock wrapper that logs acquire and release events."""

    def __init__(self, name: str) -> None:
        self._lock = RLock()
        self._name = name
        self._owner: int | None = None
        self._depth = 0

    def __enter__(self) -> "LoggedRLock":
        thread = threading.current_thread()
        reentrant = self._owner == thread.ident
        if reentrant:
            logger.debug(
                "[%s] thread %s re-entering lock (depth %d)",
                self._name, thread.name, self._depth + 1)
        else:
            logger.debug(
                "[%s] thread %s requesting lock", self._name, thread.name)
        self._lock.acquire()
        self._owner = thread.ident
        self._depth += 1
        if not reentrant:
            logger.debug(
                "[%s] thread %s acquired lock", self._name, thread.name)
        return self

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc_value: BaseException | None,
        _exc_tb: TracebackType | None,
    ) -> None:
        thread = threading.current_thread()
        self._depth -= 1
        if self._depth == 0:
            self._owner = None
            self._lock.release()
            logger.debug(
                "[%s] thread %s released lock", self._name, thread.name)
        else:
            self._lock.release()
            logger.debug(
                "[%s] thread %s exiting re-entrant lock (depth %d)",
                self._name, thread.name, self._depth)


class ThreadSafePipeline(AnnotationPipeline):
    """Thread-safe annotation pipeline wrapper."""

    def __init__(
        self, pipeline: AnnotationPipeline, pipeline_id: str = "unknown",
    ):  # pylint: disable=super-init-not-called
        self.pipeline = pipeline
        self.lock = LoggedLock(f"pipeline:{pipeline_id}")

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

    def get_attributes(self) -> list[Attribute]:
        return self.pipeline.get_attributes()

    def get_attribute_info(
            self, attribute_name: str) -> Attribute | None:
        return self.pipeline.get_attribute_info(attribute_name)

    def get_resource_ids(self) -> set[str]:
        return self.pipeline.get_resource_ids()

    def get_annotator_by_attribute_info(
        self, attribute_info: Attribute,
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
            thread_name_prefix="pipeline-loader",
        )
        self._load_timeout = load_timeout

        self.capacity = capacity
        self._cache: dict[str, LoadingDetails] = {}
        self._pipeline_callbacks: dict[str, Callable | None] = {}
        self._cache_lock: LoggedRLock = LoggedRLock("pipeline_cache")
        self._order: list[str] = []
        # Refcount of pipelines currently being resolved by a get_pipeline
        # caller, keyed by pipeline_id (the cache key / name) -- NOT by the
        # identity of a particular LoadingDetails entry. A pinned (in-use)
        # pipeline is never chosen for LRU eviction (#140), so it cannot be
        # evicted out from under an in-flight caller by capacity pressure.
        # The pin is name-scoped: a force/config-change delete+re-put of the
        # same id pops the pin, so a concurrent awaiter is no longer pinning
        # the replacement entry -- that residual case is recovered by the
        # reload-on-miss retry in AnnotationBaseView.get_pipeline, not here.
        self._in_use: dict[str, int] = {}

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
        pipeline_id: str = "unknown",
    ) -> ThreadSafePipeline:
        thread = threading.current_thread().name
        logger.debug(
            "thread %s loading pipeline %s", thread, pipeline_id)
        pipeline = ThreadSafePipeline(
            load_pipeline_from_yaml(raw, grr), pipeline_id)
        pipeline.open()
        logger.debug(
            "thread %s finished loading pipeline %s", thread, pipeline_id)
        return pipeline

    def _evictable_pipeline_id(self) -> str | None:
        """Return the LRU pipeline id that is not pinned in-use.

        Must be called while holding ``self._cache_lock``. Returns ``None``
        when every cached pipeline is currently being resolved by a
        ``get_pipeline`` caller; in that case the caller skips eviction and
        the cache is allowed to exceed capacity briefly rather than evict an
        in-flight pipeline out from under its requester (#140).
        """
        for pipeline_id in self._order:
            if self._in_use.get(pipeline_id, 0) == 0:
                return pipeline_id
        return None

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
        thread = threading.current_thread().name
        logger.debug(
            "thread %s calling put_pipeline for %s", thread, pipeline_id)
        with self._cache_lock:
            if pipeline_id in self._cache:
                details = self._cache[pipeline_id]
                if details.config_hash == pipeline_config_hash and not force:
                    if finish_load_callback is not None:
                        finish_load_callback()
                    return
                self.delete_pipeline(pipeline_id)

            while len(self._cache) >= self.capacity:
                evict_id = self._evictable_pipeline_id()
                if evict_id is None:
                    logger.warning(
                        "pipeline cache temporarily over capacity: all %d "
                        "entries are pinned in-use, cannot evict to make room "
                        "for %s",
                        len(self._cache), pipeline_id,
                    )
                    break
                self.delete_pipeline(evict_id, do_cancel=False)

            pipeline_future = self._load_executor.execute(
                self._load_pipeline_raw,
                raw=pipeline_config,
                grr=self._grr,
                pipeline_id=pipeline_id,
                callback_start=begin_load_callback,
                callback_success=finish_load_callback,
            )

            loading_details = LoadingDetails(
                time_started=started,
                pipeline_id=pipeline_id,
                config_hash=pipeline_config_hash,
                future=pipeline_future,
            )

            self._pipeline_callbacks[pipeline_id] = delete_callback
            self._cache[pipeline_id] = loading_details
            self._order.append(pipeline_id)
        elapsed = time.time() - started
        logger.debug(
            "put pipeline %s in %.2f seconds", pipeline_id, elapsed)

    def clean_old_tasks(self) -> None:
        """Clean old tasks that have timed out.

        Skips entries that are currently pinned in-use by an in-flight
        ``get_pipeline`` caller (#140): reaping such an entry would cancel the
        future the caller is awaiting and surface a spurious cache-miss. A
        pinned entry that is genuinely stuck past the timeout is left in place
        with a warning rather than force-deleted; it is reaped on a later pass
        once its caller unpins.
        """
        to_remove = []
        now = time.time()
        with self._cache_lock:
            for pipeline_id, details in self._cache.items():
                if now - details.time_started > self._load_timeout:
                    if self._in_use.get(pipeline_id, 0) > 0:
                        logger.warning(
                            "long-running pipeline %s (started at %s) is past "
                            "the load timeout but pinned in-use; deferring "
                            "reap so an in-flight caller is not broken",
                            pipeline_id, details.time_started,
                        )
                        continue
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
        logger.debug(
            "thread %s calling get_pipeline_future for %s",
            threading.current_thread().name, pipeline_id)
        with self._cache_lock:
            if pipeline_id not in self._cache:
                raise ValueError(f"Pipeline {pipeline_id} not found")
            self._order.remove(pipeline_id)
            self._order.append(pipeline_id)
            elapsed = time.time() - started
            logger.debug(
                "get_pipeline_future %s in %.2f seconds", pipeline_id, elapsed)
            return self._cache[pipeline_id].future

    def _pin_pipeline(self, pipeline_id: str) -> bool:
        """Mark a cached pipeline as in-use so it is skipped by eviction.

        Returns ``True`` if the pipeline was present and pinned, ``False`` if
        it is not (currently) in the cache. Pinning under ``_cache_lock`` is
        atomic with the cache-membership check, so the pin reliably prevents
        *capacity-driven* eviction of an in-flight pipeline. It does not by
        itself close every removal window (the view's check-then-act between
        put_pipeline and get_pipeline, the timeout reaper, or a force/config
        reload can still race); those residual windows are recovered by the
        reload-on-miss retry in AnnotationBaseView.get_pipeline.
        """
        with self._cache_lock:
            if pipeline_id not in self._cache:
                return False
            self._in_use[pipeline_id] = self._in_use.get(pipeline_id, 0) + 1
            return True

    def _unpin_pipeline(self, pipeline_id: str) -> None:
        """Release one in-use reference acquired by ``_pin_pipeline``."""
        with self._cache_lock:
            count = self._in_use.get(pipeline_id, 0)
            if count <= 1:
                self._in_use.pop(pipeline_id, None)
            else:
                self._in_use[pipeline_id] = count - 1

    def get_pipeline(self, pipeline_id: str) -> ThreadSafePipeline:
        """Get a pipeline by its ID."""
        pipeline = None
        started = time.time()
        logger.debug(
            "thread %s calling get_pipeline for %s",
            threading.current_thread().name, pipeline_id)
        # Pin the entry before resolving its future so a concurrent
        # capacity-pressure put_pipeline cannot evict it out from under us
        # (#140). The pin only prevents capacity-driven eviction; if the entry
        # is removed by another path (reaper, force/config reload, or it was
        # never cached because of the view's check-then-act window) the
        # awaiting result()/get_pipeline_future raises -- and the caller
        # (AnnotationBaseView.get_pipeline) recovers via reload-on-miss.
        pinned = self._pin_pipeline(pipeline_id)
        try:
            while pipeline is None:
                pipeline_future = self.get_pipeline_future(pipeline_id)
                try:
                    pipeline = pipeline_future.result()
                except CancelledError:
                    logger.debug("Retrying to get %s", pipeline_id)
        finally:
            if pinned:
                self._unpin_pipeline(pipeline_id)
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
        logger.debug(
            "thread %s calling delete_pipeline for %s",
            threading.current_thread().name, pipeline_id)
        future = None
        details = None
        delete_cb = None
        with self._cache_lock:
            if pipeline_id in self._cache:
                details = self._cache[pipeline_id]
                future = details.future
                delete_cb = self._pipeline_callbacks.get(pipeline_id)
                del self._cache[pipeline_id]
                del self._pipeline_callbacks[pipeline_id]
                self._order.remove(pipeline_id)
                self._in_use.pop(pipeline_id, None)
                if not future.done() and do_cancel:
                    future.cancel()
                    future = None

        if future is not None and future.done():
            try:
                future.result().close()
            except Exception:  # pylint: disable=broad-except
                logger.exception(
                    "Error during pipeline close for %s", pipeline_id)
            if delete_cb and details is not None:
                try:
                    delete_cb(details)
                except Exception:  # pylint: disable=broad-except
                    logger.exception(
                        "Error during pipeline deletion"
                        "callback for %s", pipeline_id)
