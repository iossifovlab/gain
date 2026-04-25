# pylint: disable=W0621,C0114,C0116,W0212,W0613
import pytest
import threading
import time
from web_annotation.executor import (
    SequentialTaskExecutor,
    ThreadedTaskExecutor,
)
from unittest.mock import MagicMock
from web_annotation.executor import FakeFuture



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

    executor.execute(fn, callback_success=callback_success, key1="val1", key2="val2")

    fn.assert_called_once_with(key1="val1", key2="val2")
    callback_success.assert_called_once()


def test_sequential_task_executor_exception_propagation() -> None:
    executor = SequentialTaskExecutor()

    def raise_keyboard_interrupt() -> None:
        raise KeyboardInterrupt("User interrupted")

    callback_failure = MagicMock()

    executor.execute(raise_keyboard_interrupt, callback_failure=callback_failure)

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
