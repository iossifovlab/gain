# pylint: disable=W0621,C0114,C0116,W0212,W0613
import operator
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import cast

import pytest
from gain.annotation.annotatable import VCFAllele
from gain.annotation.annotation_factory import load_pipeline_from_yaml
from gain.annotation.annotation_pipeline import AnnotationPipeline
from gain.genomic_resources.repository import GenomicResourceRepo
from pytest_mock import MockerFixture

from web_annotation.executor import (
    SequentialTaskExecutor,
    ThreadedTaskExecutor,
)
from web_annotation.pipeline_cache import (
    LoadingDetails,
    LRUPipelineCache,
    ThreadSafePipeline,
)


@pytest.fixture
def sample_pipeline_factory(
    test_grr: GenomicResourceRepo,
) -> Callable[[], AnnotationPipeline]:
    def pipeline_factory() -> AnnotationPipeline:
        pipeline_config = "- position_score: scores/pos1"
        return load_pipeline_from_yaml(
            pipeline_config,
            test_grr,
        )
    return pipeline_factory


def test_thread_safe_pipeline(
    sample_pipeline_factory: Callable[[], AnnotationPipeline],
) -> None:
    pipeline = sample_pipeline_factory()
    cached_pipeline = ThreadSafePipeline(pipeline)

    cached_pipeline.open()
    assert pipeline._is_open  # pylint: disable=protected-access
    result = cached_pipeline.annotate(
        VCFAllele("chr1", 3, "A", "T"),
        {},
    )
    assert result == {"pos1": 0.1}
    cached_pipeline.close()
    assert not pipeline._is_open  # pylint: disable=protected-access


def test_thread_safe_pipeline_concurrent(
    sample_pipeline_factory: Callable[[], AnnotationPipeline],
) -> None:
    pipeline = ThreadSafePipeline(sample_pipeline_factory())

    pipeline.open()
    assert pipeline.pipeline._is_open  # pylint: disable=protected-access

    def annotate_allele(pos: int) -> dict:
        return pipeline.annotate(
            VCFAllele("chr1", pos, "A", "T"),
            {},
        )

    positions = [3, 4, 5, 6, 7]
    expected_results = [
        {"pos1": 0.1},
        {"pos1": 0.2},
        {"pos1": 0.2},
        {"pos1": 0.3},
        {"pos1": 0.4},
    ]

    executor = ThreadPoolExecutor(max_workers=5)

    futures = [executor.submit(annotate_allele, pos) for pos in positions]
    results = [
        future.result() for future in as_completed(futures)
    ]

    assert sorted(results, key=operator.itemgetter("pos1")) == expected_results

    pipeline.close()
    assert not pipeline._is_open  # pylint: disable=protected-access


def test_lru_pipeline_cache_uses_executor(
    test_grr: GenomicResourceRepo,
    mocker: MockerFixture,
) -> None:
    lru_cache = LRUPipelineCache(test_grr, 2)
    execute_spy = mocker.spy(lru_cache._load_executor, "execute")

    assert len(lru_cache._cache) == 0  # pylint: disable=protected-access

    lru_cache.put_pipeline(
        "pipeline1", "- position_score: scores/pos1")
    assert len(lru_cache._cache) == 1  # pylint: disable=protected-access
    assert len(execute_spy.call_args_list) == 1
    call_args = execute_spy.call_args_list[0]
    assert call_args[0][0] == \
        lru_cache._load_pipeline_raw  # pylint: disable=comparison-with-callable
    assert call_args[1]["raw"] == \
        "- position_score: scores/pos1"
    assert call_args[1]["grr"] == test_grr


def test_lru_pipeline_cache_basic_sources(
    test_grr: GenomicResourceRepo,
) -> None:
    lru_cache = LRUPipelineCache(test_grr, 2)

    assert len(lru_cache._cache) == 0  # pylint: disable=protected-access

    lru_cache.put_pipeline(
        "pipeline1", "- position_score: scores/pos1")
    assert len(lru_cache._cache) == 1  # pylint: disable=protected-access
    pipeline_ids = set(
        lru_cache._cache.keys())  # pylint: disable=protected-access
    assert pipeline_ids == {"pipeline1"}

    lru_cache.put_pipeline(
        "pipeline2", "- position_score: scores/pos1")
    assert len(lru_cache._cache) == 2  # pylint: disable=protected-access
    pipeline_ids = set(
        lru_cache._cache.keys())  # pylint: disable=protected-access
    assert pipeline_ids == {"pipeline1", "pipeline2"}

    lru_cache.put_pipeline(
        "pipeline3", "- position_score: scores/pos1")
    assert len(lru_cache._cache) == 2  # pylint: disable=protected-access
    pipeline_ids = set(
        lru_cache._cache.keys())  # pylint: disable=protected-access
    assert pipeline_ids == {"pipeline2", "pipeline3"}

    lru_cache.get_pipeline("pipeline2")
    lru_cache.put_pipeline(
        "pipeline4", "- position_score: scores/pos1")
    assert len(lru_cache._cache) == 2  # pylint: disable=protected-access
    pipeline_ids = set(
        lru_cache._cache.keys())  # pylint: disable=protected-access
    assert pipeline_ids == {"pipeline2", "pipeline4"}


def test_lru_pipeline_cache_pipeline_loaded_check(
    test_grr: GenomicResourceRepo,
) -> None:
    lru_cache = LRUPipelineCache(test_grr, 2)

    assert len(lru_cache._cache) == 0  # pylint: disable=protected-access

    assert lru_cache.has_pipeline("pipeline1") is False
    lru_cache.put_pipeline(
        "pipeline1", "- position_score: scores/pos1")
    assert lru_cache.has_pipeline("pipeline1") is True
    assert len(lru_cache._cache) == 1  # pylint: disable=protected-access
    pipeline_ids = set(
        lru_cache._cache.keys())  # pylint: disable=protected-access
    assert pipeline_ids == {"pipeline1"}


def test_lru_pipeline_cache_callbacks(
    test_grr: GenomicResourceRepo,
) -> None:
    lru_cache = LRUPipelineCache(test_grr, 1)
    lru_cache._load_executor = cast(
        ThreadedTaskExecutor,
        SequentialTaskExecutor(),
    )
    deleted_pipelines = []

    def delete_callback(pipeline: LoadingDetails) -> None:
        deleted_pipelines.append(pipeline)

    lru_cache.put_pipeline(
        "pipeline1",
        "- position_score: scores/pos1",
        delete_callback=delete_callback,
    )
    assert len(lru_cache._cache) == 1  # pylint: disable=protected-access

    lru_cache.put_pipeline(
        "pipeline2",
        "- position_score: scores/pos1",
        delete_callback=delete_callback,
    )
    assert len(lru_cache._cache) == 1  # pylint: disable=protected-access

    assert len(deleted_pipelines) == 1
    assert deleted_pipelines[0].pipeline_id == "pipeline1"


def test_lru_pipeline_cache_finish_callback_on_already_loaded(
    test_grr: GenomicResourceRepo,
) -> None:
    lru_cache = LRUPipelineCache(test_grr, 2)
    lru_cache._load_executor = cast(  # pylint: disable=protected-access
        ThreadedTaskExecutor,
        SequentialTaskExecutor(),
    )
    finish_calls: list[None] = []

    def finish_callback() -> None:
        finish_calls.append(None)

    lru_cache.put_pipeline(
        "pipeline1",
        "- position_score: scores/pos1",
        finish_load_callback=finish_callback,
    )
    assert len(finish_calls) == 1

    lru_cache.put_pipeline(
        "pipeline1",
        "- position_score: scores/pos1",
        finish_load_callback=finish_callback,
    )
    assert len(finish_calls) == 2


def test_in_flight_pipeline_not_evicted_under_capacity_pressure(
    test_grr: GenomicResourceRepo,
) -> None:
    """A pipeline a caller is actively getting must not be evicted.

    Reproduces iossifovlab/gain#140: under capacity pressure a concurrent
    ``put_pipeline`` for a different id evicts the LRU entry. If that entry
    is one another thread has just put and is currently resolving via
    ``get_pipeline``, the getter races into a ``ValueError`` (surfaced as a
    spurious HTTP 400 "Pipeline ... not found" in the view).

    The interleaving is made deterministic with two gates: the getter is
    paused after entering ``get_pipeline`` but before it resolves the entry,
    the evictor is released to perform the capacity eviction of pipelineA,
    and only then is the getter allowed to continue.
    """
    config = "- position_score: scores/pos1"
    lru_cache = LRUPipelineCache(test_grr, 1)

    # pipelineA is put and fully loaded; it is the only (LRU) cache entry.
    lru_cache.put_pipeline("pipelineA", config)
    lru_cache.get_pipeline_future("pipelineA").result()

    getter_parked = threading.Event()
    eviction_done = threading.Event()

    original_get_future = lru_cache.get_pipeline_future

    def gated_get_future(pipeline_id: str):  # type: ignore[no-untyped-def]
        # Park the getter for pipelineA right at the start of resolution,
        # then wait until the concurrent eviction has happened.
        if pipeline_id == "pipelineA":
            getter_parked.set()
            assert eviction_done.wait(timeout=30)
        return original_get_future(pipeline_id)

    lru_cache.get_pipeline_future = gated_get_future  # type: ignore[method-assign]

    errors: list[BaseException] = []
    result: list[object] = []

    def getter() -> None:
        try:
            result.append(lru_cache.get_pipeline("pipelineA"))
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    getter_thread = threading.Thread(target=getter)
    getter_thread.start()

    # Wait until the getter is parked inside get_pipeline, then evict
    # pipelineA via a capacity-pressure put for a different pipeline.
    assert getter_parked.wait(timeout=30)
    lru_cache.put_pipeline("pipelineB", config)
    eviction_done.set()

    getter_thread.join(timeout=30)
    assert not getter_thread.is_alive(), "getter hung (possible deadlock)"

    assert not errors, (
        f"get_pipeline for an actively-requested pipeline failed: {errors}"
    )
    assert result and result[0] is not None
