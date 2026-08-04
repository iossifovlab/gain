from __future__ import annotations

import abc
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, cast

from gain import logging

logger = logging.getLogger(__name__)


def _result_type_name(result: Any) -> str:
    """Describe a completed task's result without rendering it.

    The completion log must never ``%s`` a task result. Executors run
    functions whose results are, or are derived from, untrusted request
    input -- ``PipelineValidation`` awaits the DRF body parse and the
    expansion-gate parse on a pool (#659), so the results in flight are the
    whole parsed request body and the whole expanded annotator list, on an
    endpoint that is anonymous and whose body is size-unbounded (DRF's
    parsers do not consult ``DATA_UPLOAD_MAX_MEMORY_SIZE``).

    Rendering those turned every completed task into a log write the size of
    whatever the client sent, doubled -- once to the console handler, once to
    the ``WatchedFileHandler`` on ``gpfwa-debug.log`` -- because the shipped
    ``LOGGING`` runs the root logger at DEBUG and ``settings_daphne``
    inherits it unchanged. It happened *before* ``MAX_CONFIG_LENGTH`` could
    refuse the request, so a refusal cost the server more than it cost the
    client: at the endpoint's throttle allowance one unauthenticated IP could
    drive gigabytes a minute onto the volume that also holds job results and
    uploads.

    The type name is what the record was worth diagnostically anyway ("did
    this task return something, and what shape"), and it is bounded and not
    attacker-controlled. Anything wanting a task's actual value should log it
    at the call site, where its size is known.
    """
    return type(result).__name__


class TaskExecutor(abc.ABC):
    """Abstract base class for job executors."""

    @abc.abstractmethod
    def execute(
        self, fn: Callable, *,
        callback_success: Callable[[], None] | None = None,
        callback_failure: Callable[[BaseException], None] | None = None,
        **kwargs: Any,
    ) -> Future[Any]:
        """Run a given function with provided arguments."""

    @abc.abstractmethod
    def wait_all(self, timeout: float) -> None:
        """Wait for given number of seconds."""

    @abc.abstractmethod
    def shutdown(self) -> None:
        """Shutdown the executor, cleaning up resources if needed."""

    @abc.abstractmethod
    def size(self) -> int:
        """Return the number of pending tasks."""


class FakeFuture:
    """Create fake future that can be used in sequentialy"""
    def __init__(self, result: Any) -> None:
        """Initializes fake future."""
        self._result = result
        self._exception: BaseException | None = None
        self._done_callbacks: Any = []

    def _invoke_callbacks(self) -> None:
        for callback in self._done_callbacks:
            try:
                callback(self)
            except Exception:
                logger.exception("exception calling callback for %r", self)

    def cancel(self) -> None:
        return

    def cancelled(self) -> bool:
        return False

    def running(self) -> bool:
        return False

    def done(self) -> bool:
        return True

    def add_done_callback(self, fn: Any) -> None:
        self._done_callbacks.append(fn)
        self._invoke_callbacks()

    def result(self, timeout: Any = None) -> Any:  # noqa: ARG002
        # Match concurrent.futures.Future: a failed future re-raises on
        # result() rather than returning a stale/None value (#154).
        if self._exception is not None:
            raise self._exception
        return self._result

    def exception(self, timeout: Any = None) -> BaseException | None:  # noqa: ARG002
        return self._exception

    def set_running_or_notify_cancel(self) -> None:
        return

    def set_result(self, result: Any) -> None:
        self._result = result
        self._invoke_callbacks()

    def set_exception(self, exception: Any) -> None:
        self._exception = exception
        self._invoke_callbacks()


class SequentialTaskExecutor(TaskExecutor):
    """Synchronous job executor."""
    def execute(
        self, fn: Callable, *,
        callback_start: Callable[[], None] | None = None,
        callback_success: Callable[[], None] | None = None,
        callback_failure: Callable[[BaseException], None] | None = None,
        **kwargs: Any,
    ) -> Future[Any]:
        future = FakeFuture(None)
        try:
            if callback_start is not None:
                callback_start()
            result = fn(**kwargs)
            future.set_result(result)
            if callback_success is not None:
                logger.debug(
                    "Task completed with a result of type: %s",
                    _result_type_name(result),
                )
                callback_success()
        except BaseException as e:
            logger.exception("Task failed with exception")
            future.set_exception(e)
            if callback_failure is not None:
                callback_failure(e)
        return cast(Future, future)

    def wait_all(self, timeout: float) -> None:  # noqa: ARG002
        return

    def shutdown(self) -> None:
        return

    def size(self) -> int:
        return 0


class ThreadedTaskExecutor(TaskExecutor):
    """Thread pool based job executor."""
    def __init__(
        self,
        max_workers: int = 4,
        job_timeout: float = 2 * 60 * 60,
        thread_name_prefix: str = "worker",
    ) -> None:
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix=thread_name_prefix,
        )
        self._futures: list[tuple[float, Future]] = []
        self._lock = threading.Lock()
        self.job_timeout = job_timeout

    def size(self) -> int:
        with self._lock:
            return len(self._futures)

    def _callback_wrapper(
        self,
        start_time: float,
        future: Future[Any],
        callback_failure: Callable[[BaseException], None] | None = None,
    ) -> None:
        if future.cancelled():
            return
        exception = future.exception()
        if exception is not None:
            logger.error("Task failed with exception: %s", exception)
            if callback_failure is not None:
                callback_failure(exception)
        else:
            logger.debug(
                "Task completed with a result of type: %s",
                _result_type_name(future.result()),
            )
        with self._lock:
            self._futures.remove((start_time, future))
            logger.debug("Remaining tasks: %d", len(self._futures))

    def execute(
        self, fn: Callable, *,
        callback_start: Callable[[], None] | None = None,
        callback_success: Callable[[], None] | None = None,
        callback_failure: Callable[[BaseException], None] | None = None,
        **kwargs: Any,
    ) -> Future[Any]:
        now = time.time()
        to_remove = []
        with self._lock:
            for time_started, future in self._futures:
                if now - time_started > self.job_timeout:
                    logger.warning(
                        "Cancelling long-running task started at %s",
                        time_started,
                    )
                    future.cancel()
                    to_remove.append((time_started, future))
            for time_started, future in to_remove:
                self._futures.remove((time_started, future))

        def wrapped_fn(**kw: Any) -> Any:
            result = fn(**kw)
            if callback_success is not None:
                callback_success()
            return result

        with self._lock:
            future = self._executor.submit(wrapped_fn, **kwargs)
            if callback_start is not None:
                callback_start()
            self._futures.append((now, future))

        def wrapper(fut: Future) -> None:
            self._callback_wrapper(
                now,
                fut,
                callback_failure=callback_failure,
            )
        future.add_done_callback(wrapper)
        return future

    def wait_all(self, timeout: float) -> None:
        start = time.time()
        elapsed = 0.0
        while self.size() > 0:
            with self._lock:
                if not self._futures:
                    return
                _, future = self._futures[0]
            try:
                future.result(timeout=timeout - elapsed)
            except TimeoutError as ex:
                raise TimeoutError("Task timed out") from ex
            except BaseException:  # noqa: BLE001, S110
                # Task error already surfaces via the callback_failure
                # path; wait_all only blocks on completion.
                pass
            elapsed = time.time() - start
            if elapsed >= timeout:
                raise TimeoutError("Waiting for tasks timed out")

    def shutdown(self) -> None:
        self._executor.shutdown(wait=True, cancel_futures=True)
        with self._lock:
            self._futures.clear()
