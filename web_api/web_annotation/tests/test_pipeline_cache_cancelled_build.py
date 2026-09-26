# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""A build the loader pool cancels on its own must not wedge the cache.

The loader pool's ``job_timeout`` sweep cancels any tracked build older than
``load_timeout`` when a new build is submitted; a build still *queued* behind
busy workers is really cancelled. The cache entry keeps that cancelled future
(iossifovlab/gain#1673).

The ``stalled_cache`` fixture produces that state through the real
``ThreadedTaskExecutor`` sweep: a one-worker cache whose only worker is held
by a gated build, a queued ``target`` build, and a later submission made
while ``job_timeout`` is dropped below zero, so its sweep treats every
tracked build as timed out and cancels the queued ``target``.
"""
import asyncio
import logging
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import cast

import pytest
from gain.genomic_resources.repository import GenomicResourceRepo

from web_annotation.annotation_base_view import (
    AnnotationBaseView,
    AnnotationMixin,
    AsyncAnnotationBaseView,
)
from web_annotation.executor import ThreadedTaskExecutor
from web_annotation.models import WebAnnotationAnonymousUser
from web_annotation.pipeline_cache import (
    LoadingDetails,
    LRUPipelineCache,
    PipelineNotCached,
    ThreadSafePipeline,
)

CONFIG = "- position_score: scores/pos1"


@dataclass
class GatedCache:
    cache: LRUPipelineCache
    gate: threading.Event
    builds: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)


def call_bounded(
    fn: Callable[[], ThreadSafePipeline],
) -> ThreadSafePipeline | Exception:
    """Run a blocking pipeline getter, failing the test if it never returns.

    Before #1673 the getters spun forever on a cancelled build; a thread
    keeps that failure mode from hanging the suite.
    """
    outcome: list[ThreadSafePipeline | Exception] = []

    def run() -> None:
        try:
            outcome.append(fn())
        except Exception as error:  # ruff: ignore[blind-except]
            outcome.append(error)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(timeout=10)
    assert not thread.is_alive(), "the getter spun on the cancelled build"
    return outcome[0]


def serving_target[ViewT: AnnotationMixin](
    view: ViewT, stalled: GatedCache,
) -> ViewT:
    """Point a view at the stalled cache, serving ``target`` as a GRR one."""
    view.lru_cache = stalled.cache
    view.grr_pipelines = {"target": {"id": "target", "content": CONFIG}}
    return view


def gated_cache(
    grr: GenomicResourceRepo,
    monkeypatch: pytest.MonkeyPatch,
) -> GatedCache:
    """A one-worker cache whose builds all wait for ``gate``."""
    gated = GatedCache(
        LRUPipelineCache(grr, 10, load_workers=1),
        threading.Event(),
    )
    build = LRUPipelineCache._load_pipeline_raw

    def gated_build(
        raw: str, grr: GenomicResourceRepo, pipeline_id: str = "unknown",
    ) -> ThreadSafePipeline:
        gated.builds.append(pipeline_id)
        assert gated.gate.wait(timeout=30)
        return build(raw, grr, pipeline_id)

    monkeypatch.setattr(
        LRUPipelineCache, "_load_pipeline_raw", staticmethod(gated_build))
    return gated


@pytest.fixture
def stalled_cache(
    test_grr: GenomicResourceRepo,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[GatedCache]:
    stalled = gated_cache(test_grr, monkeypatch)
    stalled.cache.put_pipeline("blocker", CONFIG)
    stalled.cache.put_pipeline(
        "target", CONFIG,
        delete_callback=lambda details: stalled.deleted.append(
            details.pipeline_id))
    # Every tracked build is now past job_timeout: the sweeper's
    # submission sweeps the queued target build into the cancelled state.
    pool = cast(ThreadedTaskExecutor, stalled.cache._load_executor)
    load_timeout, pool.job_timeout = pool.job_timeout, -1
    stalled.cache.put_pipeline("sweeper", CONFIG)
    pool.job_timeout = load_timeout
    assert stalled.cache.get_pipeline_future("target").cancelled()

    yield stalled
    stalled.gate.set()
    # Stops a reader still spinning on the cancelled build (red runs).
    stalled.cache.unload_pipeline("target")


def test_same_config_put_rebuilds_an_executor_cancelled_build(
    stalled_cache: GatedCache,
) -> None:
    cache = stalled_cache.cache
    cancelled = cache.get_pipeline_future("target")
    stalled_cache.gate.set()

    cache.put_pipeline("target", CONFIG)

    rebuilt = cache.get_pipeline_future("target")
    assert rebuilt is not cancelled
    assert rebuilt.result(timeout=30).raw == [
        {"position_score": "scores/pos1"}]
    assert "target" in stalled_cache.builds


def test_replacing_an_executor_cancelled_build_logs_no_error(
    stalled_cache: GatedCache,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A cancelled build has no pipeline to close; replacing it is routine."""
    stalled_cache.gate.set()

    stalled_cache.cache.put_pipeline("target", CONFIG)

    assert [
        r.getMessage() for r in caplog.records
        if r.name == "web_annotation.pipeline_cache"
        and r.levelno >= logging.ERROR
    ] == []


def test_unloading_an_executor_cancelled_build_still_notifies_deletion(
    stalled_cache: GatedCache,
) -> None:
    """Skipping the close of a cancelled build keeps its delete callback."""
    stalled_cache.gate.set()

    stalled_cache.cache.unload_pipeline("target")

    assert stalled_cache.deleted == ["target"]


def test_get_pipeline_on_an_executor_cancelled_build_reports_a_miss(
    stalled_cache: GatedCache,
) -> None:
    """The reader drops the stale entry instead of re-reading it forever.

    ``PipelineNotCached`` is what the view's reload-on-miss rebuilds from.
    """
    cache = stalled_cache.cache
    stalled_cache.gate.set()

    outcome = call_bounded(lambda: cache.get_pipeline("target"))

    assert isinstance(outcome, PipelineNotCached)
    assert not cache.has_pipeline("target")
    assert stalled_cache.deleted == ["target"]


@pytest.mark.asyncio
async def test_aget_pipeline_on_an_executor_cancelled_build_reports_a_miss(
    stalled_cache: GatedCache,
) -> None:
    cache = stalled_cache.cache
    stalled_cache.gate.set()

    with pytest.raises(PipelineNotCached):
        await asyncio.wait_for(cache.aget_pipeline("target"), timeout=10)

    assert not cache.has_pipeline("target")
    assert stalled_cache.deleted == ["target"]


def test_view_get_pipeline_rebuilds_an_executor_cancelled_build(
    stalled_cache: GatedCache,
) -> None:
    view = serving_target(AnnotationBaseView(), stalled_cache)
    user = WebAnnotationAnonymousUser(session_id="s")
    stalled_cache.gate.set()

    outcome = call_bounded(lambda: view.get_pipeline("target", user))

    assert isinstance(outcome, ThreadSafePipeline)
    assert outcome.raw == [{"position_score": "scores/pos1"}]
    assert "target" in stalled_cache.builds


@pytest.mark.asyncio
async def test_view_aget_pipeline_rebuilds_an_executor_cancelled_build(
    stalled_cache: GatedCache,
) -> None:
    view = serving_target(AsyncAnnotationBaseView(), stalled_cache)
    user = WebAnnotationAnonymousUser(session_id="s")
    stalled_cache.gate.set()

    pipeline = await asyncio.wait_for(
        view.aget_pipeline("target", user), timeout=10)

    assert pipeline.raw == [{"position_score": "scores/pos1"}]
    assert "target" in stalled_cache.builds


def test_reader_of_a_force_reloaded_build_resolves_the_replacement(
    test_grr: GenomicResourceRepo,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A reload-cancelled build is retried, not reported as a miss.

    The reader holds the queued build when a force re-put cancels it and
    caches a replacement; the cancelled build is no longer the cached one,
    so the reader must resolve the replacement.
    """
    gated = gated_cache(test_grr, monkeypatch)
    cache = gated.cache
    cache.put_pipeline("blocker", CONFIG)
    cache.put_pipeline("target", CONFIG)
    original = cache.get_pipeline_future("target")
    fetched = threading.Event()
    resolve = cache._resolve_entry

    def resolve_and_signal(pipeline_id: str) -> LoadingDetails:
        entry = resolve(pipeline_id)
        fetched.set()
        return entry

    monkeypatch.setattr(cache, "_resolve_entry", resolve_and_signal)

    def force_reload_once_fetched() -> None:
        try:
            assert fetched.wait(timeout=10)
            cache.put_pipeline("target", CONFIG, force=True)
        finally:
            gated.gate.set()

    threading.Thread(target=force_reload_once_fetched, daemon=True).start()
    outcome = call_bounded(lambda: cache.get_pipeline("target"))

    assert original.cancelled()
    assert isinstance(outcome, ThreadSafePipeline)
    assert outcome.raw == [{"position_score": "scores/pos1"}]
    assert gated.builds.count("target") == 1
