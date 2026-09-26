# pylint: disable=W0621,C0114,C0116,W0212
"""A cache entry dropped while its build is still running (#1677).

A started build cannot be cancelled -- ``Future.cancel()`` on a running
thread-pool task is a no-op -- so every path that drops an entry (capacity
eviction, force re-put, config-change re-put, unload) leaves its build
running. These tests hold the real build open behind a gate, drop the entry,
then release the gate and observe what the dropped build does.
"""
import asyncio
import logging
import threading
import time
from collections.abc import Callable, Iterator
from concurrent.futures import Future
from typing import Any

import pytest
from gain.genomic_resources.repository import GenomicResourceRepo
from pytest_mock import MockerFixture

from web_annotation.pipeline_cache import (
    LoadingDetails,
    LRUPipelineCache,
    ThreadSafePipeline,
)

CONFIG = "- position_score: scores/pos1"
UNBUILDABLE_CONFIG = "- position_score: scores/NONEXISTENT"
OTHER_CONFIG = "- position_score: scores/pos2"
GATE_TIMEOUT = 30


@pytest.fixture
def build_gate(mocker: MockerFixture) -> Iterator[threading.Event]:
    """Hold every pipeline build on its loader thread until the gate is set."""
    gate = threading.Event()
    real_load = LRUPipelineCache._load_pipeline_raw

    def gated_load(raw, grr, pipeline_id="unknown"):  # type: ignore
        assert gate.wait(GATE_TIMEOUT), "build gate was never released"
        return real_load(raw, grr, pipeline_id)

    mocker.patch.object(
        LRUPipelineCache, "_load_pipeline_raw", staticmethod(gated_load),
    )
    yield gate
    gate.set()


def _evict(cache: LRUPipelineCache) -> None:
    cache.put_pipeline("other", CONFIG)


def _force_reput(cache: LRUPipelineCache) -> None:
    cache.put_pipeline("A", CONFIG, force=True)


def _config_change_reput(cache: LRUPipelineCache) -> None:
    cache.put_pipeline("A", OTHER_CONFIG)


def _unload(cache: LRUPipelineCache) -> None:
    cache.unload_pipeline("A")


DROP_PATHS = pytest.mark.parametrize(
    "drop",
    [_evict, _force_reput, _config_change_reput, _unload],
    ids=["evict", "force", "config_change", "unload"],
)


def _finish_builds(cache: LRUPipelineCache, gate: threading.Event) -> None:
    gate.set()
    cache._load_executor.wait_all(GATE_TIMEOUT)


def _callbacks_ran(future: Future) -> threading.Event:
    """Return an event set once ``future`` has run its done-callbacks so far.

    A future runs its done-callbacks in the order they were added, so a
    callback added after the drop runs after anything the cache attached to
    the dropped build while dropping it.
    """
    ran = threading.Event()
    future.add_done_callback(lambda _future: ran.set())
    return ran


def _start_build(
    cache: LRUPipelineCache, pipeline_id: str, config: str, **callbacks: Any,
) -> Future:
    """Put a pipeline and return its build future once a loader runs it.

    A build still queued when it is dropped is cancelled instead, so every
    drop test starts from a build that is already running.
    """
    cache.put_pipeline(pipeline_id, config, **callbacks)
    future = cache.get_pipeline_future(pipeline_id)
    deadline = time.monotonic() + GATE_TIMEOUT
    while not future.running():
        assert time.monotonic() < deadline, "build never started running"
        time.sleep(0.01)
    return future


@DROP_PATHS
def test_dropped_in_flight_build_is_closed_when_it_finishes(
    test_grr: GenomicResourceRepo,
    build_gate: threading.Event,
    drop: Callable[[LRUPipelineCache], None],
    mocker: MockerFixture,
) -> None:
    cache = LRUPipelineCache(test_grr, 1)
    dropped_future = _start_build(cache, "A", CONFIG)
    close = mocker.spy(ThreadSafePipeline, "close")

    drop(cache)
    callbacks_ran = _callbacks_ran(dropped_future)
    _finish_builds(cache, build_gate)

    assert callbacks_ran.wait(GATE_TIMEOUT)
    dropped_pipeline = dropped_future.result()
    assert not dropped_pipeline._is_open
    assert close.call_args_list == [mocker.call(dropped_pipeline)]


@DROP_PATHS
def test_dropped_in_flight_build_reports_unloaded_at_drop_time(
    test_grr: GenomicResourceRepo,
    build_gate: threading.Event,
    drop: Callable[[LRUPipelineCache], None],
) -> None:
    cache = LRUPipelineCache(test_grr, 1)
    unloaded: list[str] = []
    dropped_future = _start_build(
        cache, "A", CONFIG,
        delete_callback=lambda details: unloaded.append(details.pipeline_id),
    )

    drop(cache)
    build_was_running = not dropped_future.done()
    unloaded_at_drop = list(unloaded)
    _finish_builds(cache, build_gate)

    assert build_was_running
    assert unloaded_at_drop == ["A"]


@DROP_PATHS
def test_dropped_in_flight_build_does_not_report_loaded(
    test_grr: GenomicResourceRepo,
    build_gate: threading.Event,
    drop: Callable[[LRUPipelineCache], None],
) -> None:
    cache = LRUPipelineCache(test_grr, 1)
    loaded: list[str] = []
    dropped_future = _start_build(
        cache, "A", CONFIG, finish_load_callback=lambda: loaded.append("A"))

    drop(cache)
    _finish_builds(cache, build_gate)

    assert dropped_future.exception(GATE_TIMEOUT) is None
    assert not loaded


def test_force_reput_of_in_flight_build_reports_one_load_sequence(
    test_grr: GenomicResourceRepo,
    build_gate: threading.Event,
) -> None:
    cache = LRUPipelineCache(test_grr, 1)
    statuses: list[str] = []

    def put(*, force: bool) -> None:
        _start_build(
            cache, "A", CONFIG,
            begin_load_callback=lambda: statuses.append("loading"),
            finish_load_callback=lambda: statuses.append("loaded"),
            fail_load_callback=lambda _exc: statuses.append("failed"),
            delete_callback=lambda _details: statuses.append("unloaded"),
            force=force,
        )

    put(force=False)
    put(force=True)
    _finish_builds(cache, build_gate)

    assert statuses == ["loading", "unloaded", "loading", "loaded"]


@DROP_PATHS
def test_dropped_in_flight_build_that_fails_reports_nothing(
    test_grr: GenomicResourceRepo,
    build_gate: threading.Event,
    drop: Callable[[LRUPipelineCache], None],
    caplog: pytest.LogCaptureFixture,
) -> None:
    cache = LRUPipelineCache(test_grr, 1)
    failed: list[BaseException] = []
    dropped_future = _start_build(
        cache, "A", UNBUILDABLE_CONFIG, fail_load_callback=failed.append)

    drop(cache)
    callbacks_ran = _callbacks_ran(dropped_future)
    _finish_builds(cache, build_gate)

    assert callbacks_ran.wait(GATE_TIMEOUT)
    assert dropped_future.exception() is not None
    assert not failed
    # The executor logs the build failure itself; nothing else may error.
    assert not [
        record for record in caplog.records
        if record.levelno >= logging.ERROR
        and record.name != "web_annotation.executor"
    ]


def _signal_first_resolve(cache: LRUPipelineCache) -> threading.Event:
    """Return an event set once a caller has resolved a cache entry."""
    resolved = threading.Event()
    original = cache._resolve_entry

    def resolve_entry(pipeline_id: str) -> LoadingDetails:
        entry = original(pipeline_id)
        resolved.set()
        return entry

    cache._resolve_entry = resolve_entry  # type: ignore[method-assign]
    return resolved


def test_waiter_on_force_replaced_build_gets_the_replacement(
    test_grr: GenomicResourceRepo,
    build_gate: threading.Event,
) -> None:
    cache = LRUPipelineCache(test_grr, 1)
    cache.put_pipeline("A", CONFIG)
    waiter_resolved = _signal_first_resolve(cache)
    got: list[object] = []
    waiter = threading.Thread(
        target=lambda: got.append(cache.get_pipeline("A")))
    waiter.start()
    assert waiter_resolved.wait(GATE_TIMEOUT)

    cache.put_pipeline("A", CONFIG, force=True)
    _finish_builds(cache, build_gate)
    waiter.join(GATE_TIMEOUT)

    replacement = cache.get_pipeline_future("A").result()
    assert got == [replacement]
    assert replacement._is_open


@pytest.mark.asyncio
async def test_async_waiter_on_force_replaced_build_gets_the_replacement(
    test_grr: GenomicResourceRepo,
    build_gate: threading.Event,
) -> None:
    cache = LRUPipelineCache(test_grr, 1)
    cache.put_pipeline("A", CONFIG)
    waiter_resolved = _signal_first_resolve(cache)
    waiter = asyncio.create_task(cache.aget_pipeline("A"))
    assert await asyncio.to_thread(waiter_resolved.wait, GATE_TIMEOUT)

    cache.put_pipeline("A", CONFIG, force=True)
    build_gate.set()
    got = await asyncio.wait_for(waiter, GATE_TIMEOUT)

    replacement = cache.get_pipeline_future("A").result(GATE_TIMEOUT)
    assert got is replacement
    assert replacement._is_open
