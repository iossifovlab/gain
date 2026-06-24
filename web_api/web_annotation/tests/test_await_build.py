# pylint: disable=W0621,C0114,C0116,W0212,W0613
import asyncio
from concurrent.futures import Future
from typing import cast

import pytest

from web_annotation.executor import FakeFuture
from web_annotation.pipeline_cache import BuildCancelled, await_build


@pytest.mark.asyncio
async def test_await_build_resolves_real_future_result() -> None:
    """A real concurrent.futures.Future result is awaited off-loop."""
    future: Future = Future()
    future.set_result("done")
    assert await await_build(future) == "done"


@pytest.mark.asyncio
async def test_await_build_propagates_real_future_exception() -> None:
    """An exception in the shared build future surfaces to the awaiter."""
    future: Future = Future()
    future.set_exception(ValueError("boom"))
    with pytest.raises(ValueError, match="boom"):
        await await_build(future)


@pytest.mark.asyncio
async def test_await_build_cancelled_future_raises_build_cancelled() -> None:
    """A cancelled shared build is reported as BuildCancelled, not Cancelled.

    The build future is shared across readers; a reaper/force-reload cancel of
    it must be distinguishable from a per-request task cancellation so the
    caller can retry rather than abort.
    """
    future: Future = Future()
    future.cancel()
    with pytest.raises(BuildCancelled):
        await await_build(future)


@pytest.mark.asyncio
async def test_await_build_works_with_fake_future_result() -> None:
    """FakeFuture (test SequentialTaskExecutor path) resolves under a loop.

    FakeFuture is already done at submit and fires add_done_callback
    immediately; await_build must still settle its per-request waiter.
    """
    fake = FakeFuture("seq-result")
    assert await await_build(cast(Future, fake)) == "seq-result"


@pytest.mark.asyncio
async def test_await_build_works_with_fake_future_exception() -> None:
    fake = FakeFuture(None)
    fake.set_exception(RuntimeError("fake boom"))
    with pytest.raises(RuntimeError, match="fake boom"):
        await await_build(cast(Future, fake))


@pytest.mark.asyncio
async def test_await_build_does_not_park_event_loop() -> None:
    """Awaiting a slow build leaves the loop free for other coroutines.

    The shared build completes on a worker thread after a delay; while the
    awaiter is suspended a cheap coroutine must run to completion.
    """
    loop = asyncio.get_running_loop()
    future: Future[str] = Future()

    def complete() -> None:
        future.set_result("late")

    loop.call_later(0.2, complete)

    progressed: list[int] = []

    async def cheap() -> None:
        for _ in range(5):
            await asyncio.sleep(0.01)
            progressed.append(1)

    cheap_task = asyncio.ensure_future(cheap())
    result: str = await await_build(future)
    await cheap_task

    assert result == "late"
    # The cheap coroutine made progress while await_build was suspended.
    assert len(progressed) == 5


@pytest.mark.asyncio
async def test_await_build_cancelling_waiter_does_not_cancel_shared() -> None:
    """Cancelling the awaiting task must not cancel the shared build future."""
    future: Future[str] = Future()

    task: asyncio.Task[str] = asyncio.ensure_future(await_build(future))
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # The shared build future was NOT cancelled by the per-request cancel.
    assert not future.cancelled()
