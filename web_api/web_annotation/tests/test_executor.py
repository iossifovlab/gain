# pylint: disable=W0621,C0114,C0116,W0212,W0613
import contextlib
import logging
import threading
import time
from collections.abc import Callable, Iterator
from typing import Any
from unittest.mock import MagicMock

import pytest

from web_annotation.executor import (
    FakeFuture,
    SequentialTaskExecutor,
    ThreadedTaskExecutor,
)


def test_sequential_task_executor_execute() -> None:
    executor = SequentialTaskExecutor()

    fn = MagicMock()
    callback_success = MagicMock()
    callback_failure = MagicMock()

    executor.execute(
        fn,
        callback_success=callback_success,
        callback_failure=callback_failure,
        args=(1, 2),
        key="value")

    assert fn.call_count == 1
    fn.assert_called_once_with(args=(1, 2), key="value")

    assert callback_success.call_count == 1


def test_sequential_task_executor_success_with_result() -> None:
    executor = SequentialTaskExecutor()

    fn = MagicMock(return_value="test_result")
    callback_success = MagicMock()
    callback_failure = MagicMock()

    executor.execute(
        fn,
        callback_success=callback_success,
        callback_failure=callback_failure)

    assert fn.call_count == 1
    assert callback_success.call_count == 1
    callback_success.assert_called_once()
    assert callback_failure.call_count == 0


def test_sequential_task_executor_failure() -> None:
    executor = SequentialTaskExecutor()

    def failing_fn() -> None:
        raise RuntimeError("Test error")

    callback_success = MagicMock()
    callback_failure = MagicMock()

    executor.execute(
        failing_fn,
        callback_success=callback_success,
        callback_failure=callback_failure)

    assert callback_success.call_count == 0
    assert callback_failure.call_count == 1

    # Verify the exception was passed to callback_failure
    args, _ = callback_failure.call_args
    assert isinstance(args[0], RuntimeError)
    assert str(args[0]) == "Test error"


def test_sequential_task_executor_failure_future_honors_exception() -> None:
    """A failed task's future exposes the exception (#154).

    SequentialTaskExecutor must be behaviorally interchangeable with
    ThreadedTaskExecutor: exception() returns the error and result() re-raises
    it, matching concurrent.futures.Future.
    """
    executor = SequentialTaskExecutor()

    error = RuntimeError("Test error")

    def failing_fn() -> None:
        raise error

    future = executor.execute(failing_fn)

    assert future.done() is True
    assert future.exception() is error
    with pytest.raises(RuntimeError, match="Test error"):
        future.result()


def test_sequential_task_executor_no_callbacks() -> None:
    executor = SequentialTaskExecutor()

    fn = MagicMock(return_value="result")

    # Should not raise an error when callbacks are None
    executor.execute(fn)

    assert fn.call_count == 1


def test_sequential_task_executor_failure_no_callbacks() -> None:
    executor = SequentialTaskExecutor()

    def failing_fn() -> None:
        raise ValueError("Error without callbacks")

    # Should not raise an error even when failure occurs and no callbacks
    executor.execute(failing_fn)


def test_sequential_task_executor_wait_all() -> None:
    executor = SequentialTaskExecutor()

    # wait_all should return immediately for sequential executor
    start = time.time()
    executor.wait_all(timeout=10)
    elapsed = time.time() - start

    # Should complete almost instantly
    assert elapsed < 0.1


def test_sequential_task_executor_shutdown() -> None:
    executor = SequentialTaskExecutor()

    # shutdown should be a no-op for sequential executor
    executor.shutdown()

    # Should still be able to execute after shutdown
    fn = MagicMock()
    executor.execute(fn)
    assert fn.call_count == 1


def test_sequential_task_executor_size() -> None:
    executor = SequentialTaskExecutor()

    # Size should always be 0 for sequential executor
    assert executor.size() == 0

    fn = MagicMock()
    executor.execute(fn)

    # Still 0 since tasks execute immediately
    assert executor.size() == 0


def test_sequential_task_executor_multiple_executions() -> None:
    executor = SequentialTaskExecutor()

    fn1 = MagicMock(return_value="result1")
    fn2 = MagicMock(return_value="result2")
    fn3 = MagicMock(return_value="result3")

    callback_success = MagicMock()

    executor.execute(fn1, callback_success=callback_success)
    executor.execute(fn2, callback_success=callback_success)
    executor.execute(fn3, callback_success=callback_success)

    # All should execute immediately in order
    assert fn1.call_count == 1
    assert fn2.call_count == 1
    assert fn3.call_count == 1
    assert callback_success.call_count == 3


def test_threaded_task_executor_execute() -> None:
    executor = ThreadedTaskExecutor(max_workers=1)

    fn = MagicMock(return_value="result")
    callback_success = MagicMock()
    callback_failure = MagicMock()

    executor.execute(
        fn,
        callback_success=callback_success,
        callback_failure=callback_failure,
        args=(1, 2),
        key="value")

    # Wait for all tasks to complete
    executor.wait_all(timeout=5)

    assert fn.call_count == 1
    fn.assert_called_once_with(args=(1, 2), key="value")

    assert callback_success.call_count == 1
    callback_success.assert_called_once_with()


def test_threaded_task_executor_timeout() -> None:
    executor = ThreadedTaskExecutor(max_workers=2)

    def fn() -> None:
        time.sleep(1)

    executor.execute(fn)

    with pytest.raises(TimeoutError):
        executor.wait_all(timeout=0.1)

    executor.shutdown()


def test_threaded_task_executor_failure() -> None:
    executor = ThreadedTaskExecutor(max_workers=1)

    def fn() -> None:
        raise ValueError("Test error")

    callback_success = MagicMock()
    callback_failure = MagicMock()

    executor.execute(
        fn,
        callback_success=callback_success,
        callback_failure=callback_failure)

    # Wait for all tasks to complete
    executor.wait_all(timeout=5)

    assert callback_success.call_count == 0
    assert callback_failure.call_count == 1

    # Check that the exception was passed to callback_failure
    args, _ = callback_failure.call_args
    assert isinstance(args[0], ValueError)
    assert str(args[0]) == "Test error"

    executor.shutdown()


def test_threaded_task_executor_multiple_tasks() -> None:
    executor = ThreadedTaskExecutor(max_workers=2)

    fn1 = MagicMock(return_value="result1")
    fn2 = MagicMock(return_value="result2")
    fn3 = MagicMock(return_value="result3")

    callback_success = MagicMock()

    executor.execute(fn1, callback_success=callback_success)
    executor.execute(fn2, callback_success=callback_success)
    executor.execute(fn3, callback_success=callback_success)

    # Wait for all tasks to complete
    executor.wait_all(timeout=5)

    assert fn1.call_count == 1
    assert fn2.call_count == 1
    assert fn3.call_count == 1
    assert callback_success.call_count == 3

    executor.shutdown()


def test_threaded_task_executor_size() -> None:
    executor = ThreadedTaskExecutor(max_workers=2)

    def slow_fn() -> None:
        time.sleep(0.5)

    assert executor.size() == 0

    executor.execute(slow_fn)
    executor.execute(slow_fn)

    # Check size before tasks complete
    assert executor.size() == 2

    # Wait for tasks to complete
    executor.wait_all(timeout=5)

    # Size should be 0 after completion
    assert executor.size() == 0

    executor.shutdown()


def test_threaded_task_executor_shutdown() -> None:
    executor = ThreadedTaskExecutor(max_workers=2)

    def slow_fn() -> None:
        time.sleep(0.1)

    executor.execute(slow_fn)
    executor.execute(slow_fn)

    # Shutdown should wait for tasks to complete
    executor.shutdown()

    # After shutdown, futures list should be cleared
    assert executor.size() == 0


def test_threaded_task_executor_mixed_results() -> None:
    executor = ThreadedTaskExecutor(max_workers=3)

    success_fn = MagicMock(return_value="success")

    def failure_fn() -> None:
        raise RuntimeError("Task failed")

    callback_success = MagicMock()
    callback_failure = MagicMock()

    executor.execute(
        success_fn,
        callback_success=callback_success,
        callback_failure=callback_failure)

    executor.execute(
        failure_fn,
        callback_success=callback_success,
        callback_failure=callback_failure)

    executor.execute(
        success_fn,
        callback_success=callback_success,
        callback_failure=callback_failure)

    # Wait for all tasks to complete
    executor.wait_all(timeout=5)

    assert callback_success.call_count == 2
    assert callback_failure.call_count == 1
    assert success_fn.call_count == 2

    executor.shutdown()


def test_threaded_task_executor_with_args_and_kwargs() -> None:
    executor = ThreadedTaskExecutor(max_workers=1)

    fn = MagicMock(return_value="result")
    callback_success = MagicMock()

    executor.execute(
        fn,
        callback_success=callback_success,
        key1="value1",
        key2="value2")

    executor.wait_all(timeout=5)

    fn.assert_called_once_with(key1="value1", key2="value2")
    callback_success.assert_called_once()

    executor.shutdown()


def test_threaded_task_executor_no_callbacks() -> None:
    executor = ThreadedTaskExecutor(max_workers=1)

    fn = MagicMock(return_value="result")

    # Should work without any callbacks
    executor.execute(fn, arg1="value1")

    executor.wait_all(timeout=5)

    fn.assert_called_once_with(arg1="value1")
    executor.shutdown()


def test_threaded_task_executor_failure_no_callbacks() -> None:
    executor = ThreadedTaskExecutor(max_workers=1)

    def failing_fn() -> None:
        raise ValueError("Error without callbacks")

    # Should not crash when failure occurs without callbacks
    executor.execute(failing_fn)

    executor.wait_all(timeout=5)
    executor.shutdown()


def test_threaded_task_executor_concurrent_execution() -> None:
    executor = ThreadedTaskExecutor(max_workers=3)

    results: list[int] = []
    lock = threading.Lock()

    def concurrent_fn(value: int) -> int:
        time.sleep(0.1)
        with lock:
            results.append(value)
        return value

    callback_success = MagicMock()

    # Execute 3 tasks concurrently
    executor.execute(concurrent_fn, callback_success=callback_success, value=1)
    executor.execute(concurrent_fn, callback_success=callback_success, value=2)
    executor.execute(concurrent_fn, callback_success=callback_success, value=3)

    executor.wait_all(timeout=5)

    # All tasks should have completed
    assert len(results) == 3
    assert set(results) == {1, 2, 3}
    assert callback_success.call_count == 3

    executor.shutdown()


def test_threaded_task_executor_wait_all_empty() -> None:
    executor = ThreadedTaskExecutor(max_workers=2)

    # wait_all on empty executor should return immediately
    start = time.time()
    executor.wait_all(timeout=5)
    elapsed = time.time() - start

    assert elapsed < 0.1
    executor.shutdown()


def test_threaded_task_executor_sequential_wait() -> None:
    executor = ThreadedTaskExecutor(max_workers=1)

    results: list[str] = []

    def task(name: str) -> str:
        time.sleep(0.1)
        results.append(name)
        return name

    executor.execute(task, name="task1")
    executor.wait_all(timeout=5)

    executor.execute(task, name="task2")
    executor.wait_all(timeout=5)

    # Tasks should execute sequentially
    assert results == ["task1", "task2"]
    executor.shutdown()


def test_threaded_task_executor_partial_failure() -> None:
    executor = ThreadedTaskExecutor(max_workers=2)

    success_count = 0
    failure_count = 0
    lock = threading.Lock()

    def on_success() -> None:
        nonlocal success_count
        with lock:
            success_count += 1

    def on_failure(exc: BaseException) -> None:
        nonlocal failure_count
        with lock:
            failure_count += 1

    def success_fn() -> str:
        return "success"

    def failure_fn() -> None:
        raise ValueError("Failed")

    executor.execute(
        success_fn,
        callback_success=on_success,
        callback_failure=on_failure)
    executor.execute(
        failure_fn,
        callback_success=on_success,
        callback_failure=on_failure)
    executor.execute(
        success_fn,
        callback_success=on_success,
        callback_failure=on_failure)
    executor.execute(
        failure_fn,
        callback_success=on_success,
        callback_failure=on_failure)

    executor.wait_all(timeout=5)

    assert success_count == 2
    assert failure_count == 2
    executor.shutdown()


def test_sequential_task_executor_with_kwargs_only() -> None:
    executor = SequentialTaskExecutor()

    fn = MagicMock(return_value="result")
    callback_success = MagicMock()

    executor.execute(
        fn, callback_success=callback_success, key1="val1", key2="val2",
    )

    fn.assert_called_once_with(key1="val1", key2="val2")
    callback_success.assert_called_once()


def test_sequential_task_executor_exception_propagation() -> None:
    executor = SequentialTaskExecutor()

    def raise_keyboard_interrupt() -> None:
        raise KeyboardInterrupt("User interrupted")

    callback_failure = MagicMock()

    executor.execute(
        raise_keyboard_interrupt, callback_failure=callback_failure,
    )

    assert callback_failure.call_count == 1
    args, _ = callback_failure.call_args
    assert isinstance(args[0], KeyboardInterrupt)


def test_threaded_task_executor_different_exception_types() -> None:
    executor = ThreadedTaskExecutor(max_workers=2)

    exceptions: list[BaseException] = []
    lock = threading.Lock()

    def on_failure(exc: BaseException) -> None:
        with lock:
            exceptions.append(exc)

    def raise_value_error() -> None:
        raise ValueError("Value error")

    def raise_runtime_error() -> None:
        raise RuntimeError("Runtime error")

    def raise_type_error() -> None:
        raise TypeError("Type error")

    executor.execute(raise_value_error, callback_failure=on_failure)
    executor.execute(raise_runtime_error, callback_failure=on_failure)
    executor.execute(raise_type_error, callback_failure=on_failure)

    executor.wait_all(timeout=5)

    assert len(exceptions) == 3
    assert any(isinstance(e, ValueError) for e in exceptions)
    assert any(isinstance(e, RuntimeError) for e in exceptions)
    assert any(isinstance(e, TypeError) for e in exceptions)

    executor.shutdown()


def test_threaded_task_executor_max_workers_limit() -> None:
    executor = ThreadedTaskExecutor(max_workers=2)

    active_tasks = 0
    max_concurrent = 0
    lock = threading.Lock()

    def track_concurrency() -> None:
        nonlocal active_tasks, max_concurrent
        with lock:
            active_tasks += 1
            max_concurrent = max(max_concurrent, active_tasks)

        time.sleep(0.2)

        with lock:
            active_tasks -= 1

    # Submit more tasks than max_workers
    for _ in range(5):
        executor.execute(track_concurrency)

    executor.wait_all(timeout=10)

    # Max concurrent tasks should not exceed max_workers
    assert max_concurrent <= 2
    executor.shutdown()


def test_threaded_task_executor_return_none() -> None:
    executor = ThreadedTaskExecutor(max_workers=1)

    def returns_none() -> None:
        pass

    callback_success = MagicMock()

    executor.execute(returns_none, callback_success=callback_success)
    executor.wait_all(timeout=5)

    callback_success.assert_called_once()
    executor.shutdown()


def test_sequential_task_executor_return_none() -> None:
    executor = SequentialTaskExecutor()

    def returns_none() -> None:
        pass

    callback_success = MagicMock()

    executor.execute(returns_none, callback_success=callback_success)

    callback_success.assert_called_once()


def test_threaded_task_executor_rapid_submission() -> None:
    executor = ThreadedTaskExecutor(max_workers=4)

    counter = 0
    lock = threading.Lock()

    def increment() -> None:
        nonlocal counter
        with lock:
            counter += 1

    # Rapidly submit many tasks
    for _ in range(50):
        executor.execute(increment)

    executor.wait_all(timeout=10)

    assert counter == 50
    executor.shutdown()


def test_threaded_task_executor_cancels_long_running_tasks() -> None:
    executor = ThreadedTaskExecutor(max_workers=4, job_timeout=0.5)

    def long_running_task() -> None:
        time.sleep(2)

    assert executor.size() == 0

    executor.execute(long_running_task)
    time.sleep(0.6)

    assert executor.size() == 1

    executor.execute(long_running_task)

    assert executor.size() == 1

    executor.wait_all(timeout=10)
    executor.shutdown()


def test_fake_future_result() -> None:
    future = FakeFuture("test_result")
    assert future.result() == "test_result"
    assert future.done() is True
    assert future.cancelled() is False
    assert future.running() is False


def test_fake_future_add_done_callback() -> None:
    future = FakeFuture("result")
    callback = MagicMock()

    future.add_done_callback(callback)

    callback.assert_called_once_with(future)


def test_fake_future_set_result() -> None:
    future = FakeFuture("initial")
    callback = MagicMock()

    future.add_done_callback(callback)
    future.set_result("new_result")

    assert future.result() == "new_result"
    assert callback.call_count == 2


def test_sequential_task_executor_callback_start() -> None:
    executor = SequentialTaskExecutor()

    fn = MagicMock(return_value="result")
    callback_start = MagicMock()

    executor.execute(fn, callback_start=callback_start)

    callback_start.assert_called_once()
    fn.assert_called_once()


def test_threaded_task_executor_callback_start() -> None:
    executor = ThreadedTaskExecutor(max_workers=1)

    fn = MagicMock(return_value="result")
    callback_start = MagicMock()

    executor.execute(fn, callback_start=callback_start)
    executor.wait_all(timeout=5)

    callback_start.assert_called_once()
    executor.shutdown()


def test_threaded_task_executor_future_result() -> None:
    executor = ThreadedTaskExecutor(max_workers=1)

    def test_fn() -> str:
        return "expected_result"

    future = executor.execute(test_fn)
    result = future.result(timeout=5)

    assert result == "expected_result"
    executor.shutdown()


def test_fake_future_cancel() -> None:

    future = FakeFuture("result")
    future.cancel()
    assert future.cancelled() is False


# ---------------------------------------------------------------------------
# A task's result is never rendered into a log record
# ---------------------------------------------------------------------------
# An executor runs whatever function it is handed, so a result's size is the
# caller's property, not the executor's -- there is no value it can assume is
# small. Rendering one into the completion record therefore writes an amount of
# data the executor cannot bound, on every completed task, and the shipped
# LOGGING runs the root logger at DEBUG behind both a console handler and a
# file handler. The record may say what shape a task returned; it may not say
# what.

#: Long enough that a truncating renderer would still leak it, and
#: distinctive enough that a match cannot be coincidence.
RESULT_CANARY = "cAnArY-task-result-that-must-not-be-logged"


def _canary_messages(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if RESULT_CANARY in record.getMessage()
    ]


def _drain(executor: ThreadedTaskExecutor, timeout: float = 10.0) -> None:
    """Wait until the pool's done-callback has run for every task.

    ``Future.result()`` can return before the done-callbacks fire -- waiters
    are notified inside the condition, callbacks invoked after it is released
    -- so the callback's own bookkeeping (``size()``) is the sync point, not
    the result.
    """
    deadline = time.time() + timeout
    while executor.size() > 0 and time.time() < deadline:
        time.sleep(0.01)
    assert executor.size() == 0, "tasks still pending"


def test_threaded_executor_does_not_log_the_task_result(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A completed task's result must not appear in any log record."""
    executor = ThreadedTaskExecutor(max_workers=1)
    try:
        with caplog.at_level(logging.DEBUG):
            future = executor.execute(lambda: RESULT_CANARY)
            assert future.result(timeout=10) == RESULT_CANARY
            _drain(executor)

        assert _canary_messages(caplog) == []
    finally:
        executor.shutdown()


def test_sequential_executor_does_not_log_the_task_result(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The sequential twin must not leak it either.

    It is behaviorally interchangeable with the threaded pool (#154) and is
    what the tests and the cache's sequential mode run on, so a fix applied
    to only one of them would be reintroduced by a configuration change.
    """
    executor = SequentialTaskExecutor()

    with caplog.at_level(logging.DEBUG):
        future = executor.execute(
            lambda: RESULT_CANARY, callback_success=MagicMock())
        assert future.result() == RESULT_CANARY

    assert _canary_messages(caplog) == []


def test_threaded_executor_still_reports_task_completion(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Withholding the value must not cost the diagnostic.

    The completion record still fires and still says what shape the task
    returned -- a bounded description that no request can grow.
    """
    executor = ThreadedTaskExecutor(max_workers=1)
    try:
        with caplog.at_level(logging.DEBUG):
            future = executor.execute(lambda: RESULT_CANARY)
            assert future.result(timeout=10) == RESULT_CANARY
            _drain(executor)

        completions = [
            record.getMessage()
            for record in caplog.records
            if "Task completed" in record.getMessage()
        ]
        assert len(completions) == 1, caplog.records
        assert "str" in completions[0]
    finally:
        executor.shutdown()


# ---------------------------------------------------------------------------
# A done task is untracked, whatever ended it -- a cancellation included
# ---------------------------------------------------------------------------
# ``size()`` is what the validate view's admission check reads, and the
# ``job_timeout`` sweep is the only other thing that ever drops an entry. A
# cancelled task that stays tracked therefore counts against the admission
# bound until a sweep runs -- and the sweep runs only inside ``execute()``,
# which a saturated view never reaches again (iossifovlab/gain#1699).

#: How long a test waits on anything that should happen at once. A regression
#: fails at this bound instead of hanging the suite.
PROMPT_SECONDS = 5.0


#: A ``job_timeout`` short enough that a test can outwait it, so the next
#: ``execute()`` sweeps every task submitted before the wait.
SHORT_JOB_TIMEOUT = 0.2


@contextlib.contextmanager
def busy_worker_pool(
    job_timeout: float = 2 * 60 * 60,
) -> Iterator[tuple[ThreadedTaskExecutor, threading.Event]]:
    """A single-worker pool whose worker is held busy until the event is set.

    Anything submitted while the event is clear can only be queued.
    """
    executor = ThreadedTaskExecutor(max_workers=1, job_timeout=job_timeout)
    started = threading.Event()
    release = threading.Event()

    def occupy() -> None:
        started.set()
        release.wait(timeout=PROMPT_SECONDS * 2)

    executor.execute(occupy)
    assert started.wait(timeout=PROMPT_SECONDS), "the worker never started"
    try:
        yield executor, release
    finally:
        release.set()
        _shutdown_even_if_deadlocked(executor)


def _shutdown_even_if_deadlocked(executor: ThreadedTaskExecutor) -> None:
    """Shut the pool down, prising its lock loose if a deadlock holds it.

    After a deadlock the lock is never released, so the pool's workers --
    non-daemon threads the interpreter joins at exit -- block on it forever,
    and a regression would hang the run instead of failing it. A
    ``threading.Lock`` may be released from any thread; doing so here, only
    once the orderly shutdown has already failed to finish, lets them go.
    """
    shutting_down = _call_in_a_thread(executor.shutdown)
    deadline = time.monotonic() + PROMPT_SECONDS
    while shutting_down.is_alive() and time.monotonic() < deadline:
        with contextlib.suppress(RuntimeError):
            executor._lock.release()
        shutting_down.join(timeout=0.1)


def _call_in_a_thread(fn: Callable[..., Any], *args: Any) -> threading.Thread:
    """Call ``fn`` off the test thread and wait a bounded time for it.

    A daemon thread, so a call that deadlocks fails the test instead of
    hanging the process. The caller checks ``is_alive()``.
    """
    caller = threading.Thread(target=fn, args=args, daemon=True)
    caller.start()
    caller.join(timeout=PROMPT_SECONDS)
    return caller


@pytest.fixture
def one_busy_worker() -> Iterator[tuple[ThreadedTaskExecutor, threading.Event]]:
    with busy_worker_pool() as pool:
        yield pool


def test_a_cancelled_queued_task_is_no_longer_tracked(
    one_busy_worker: tuple[ThreadedTaskExecutor, threading.Event],
) -> None:
    executor, release = one_busy_worker
    queued = executor.execute(lambda: None)
    assert queued.cancel(), "the task was not queued, so nothing is tested"

    release.set()
    executor.wait_all(timeout=PROMPT_SECONDS)

    assert executor.size() == 0


def test_the_timeout_sweep_cancels_a_queued_task_without_deadlocking() -> None:
    """The sweep's cancel runs the task's done-callback, which untracks it.

    ``cancel()`` on a queued future runs its done-callbacks synchronously, in
    the thread that called it. That is ``execute()``'s thread, mid-sweep, so
    the untracking must not wait on anything the sweep is holding.
    """
    with busy_worker_pool(job_timeout=SHORT_JOB_TIMEOUT) as (executor, _):
        swept = executor.execute(lambda: None)
        time.sleep(SHORT_JOB_TIMEOUT * 2)

        caller = _call_in_a_thread(executor.execute, lambda: None)

        assert not caller.is_alive(), "execute() deadlocked in its sweep"
        assert swept.cancelled()
        # The sweep drops the occupying task too: it is past ``job_timeout``,
        # and a running task is dropped from tracking though it runs on. What
        # remains is the task the sweeping call submitted.
        assert executor.size() == 1


def test_a_swept_running_task_finishes_without_a_callback_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The sweep drops a running task it cannot cancel; its finish is quiet.

    ``cancel()`` is a no-op on a running task, so it finishes later and its
    done-callback untracks an entry the sweep already dropped.
    ``concurrent.futures`` logs any exception a done-callback raises under
    its own logger, so that is where an untracking error would surface.
    """
    with busy_worker_pool(job_timeout=SHORT_JOB_TIMEOUT) as (
            executor, release):
        time.sleep(SHORT_JOB_TIMEOUT * 2)
        with caplog.at_level(logging.ERROR, logger="concurrent.futures"):
            executor.execute(lambda: None)

            release.set()
            executor.wait_all(timeout=PROMPT_SECONDS)
            _drain(executor)

        assert [
            record.getMessage()
            for record in caplog.records
            if record.name == "concurrent.futures"
        ] == []
        assert executor.size() == 0


#: How long a submission is held open, so a concurrent ``shutdown()`` is
#: well inside its cancelling pass before the submission reaches the pool.
SUBMIT_HOLD_SECONDS = 0.5


def test_shutdown_during_a_submission_does_not_deadlock() -> None:
    """``shutdown()`` cancels queued tasks while a submission is under way.

    The pool's shutdown cancels queued futures while holding the pool's own
    lock, and each cancel runs that task's done-callback, which untracks it.
    A submission that is still waiting for the pool must not be holding
    anything that untracking needs.
    """
    with busy_worker_pool() as (executor, release):
        executor.execute(lambda: None)  # queued: shutdown will cancel it
        submitting = threading.Event()
        submit = executor._executor.submit

        def held_submit(*args: Any, **kwargs: Any) -> Any:
            submitting.set()
            time.sleep(SUBMIT_HOLD_SECONDS)
            return submit(*args, **kwargs)

        executor._executor.submit = held_submit  # type: ignore[method-assign]

        def execute_after_shutdown_raises() -> None:
            with contextlib.suppress(RuntimeError):
                executor.execute(lambda: None)

        executing = threading.Thread(
            target=execute_after_shutdown_raises, daemon=True)
        executing.start()
        assert submitting.wait(timeout=PROMPT_SECONDS)

        release.set()
        shutting_down = _call_in_a_thread(executor.shutdown)
        executing.join(timeout=PROMPT_SECONDS)

        assert not shutting_down.is_alive(), "shutdown() deadlocked"
        assert not executing.is_alive(), "execute() deadlocked"
