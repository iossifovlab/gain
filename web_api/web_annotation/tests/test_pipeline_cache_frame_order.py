# pylint: disable=W0621,C0114,C0116,W0212
"""Per load, ``loading`` is announced before the terminal status (gain#176).

``put_pipeline`` announces ``loading`` on the request thread after it has
submitted the build, while the build's ``loaded`` / ``failed`` announcement
comes from the loader. These tests let the build finish -- and its loader
callbacks run -- before ``put_pipeline`` reaches ``loading``, and check the
order subscribers see.
"""
import threading
from collections.abc import Callable
from typing import cast

import pytest
from gain.genomic_resources.repository import GenomicResourceRepo

from web_annotation.executor import (
    SequentialTaskExecutor,
    ThreadedTaskExecutor,
)
from web_annotation.pipeline_cache import LRUPipelineCache

CONFIG = "- position_score: scores/pos1"
UNBUILDABLE_CONFIG = "- position_score: scores/NONEXISTENT"
TIMEOUT = 30


def _wait_for_build_callbacks(
    cache: LRUPipelineCache, pipeline_id: str,
) -> None:
    """Block until the build of ``pipeline_id`` has run its callbacks.

    The loader's success callback runs before the future resolves, and a
    future runs its done-callbacks in the order they were added, so a
    done-callback added now runs after the cache's own failure callback.
    """
    ran = threading.Event()
    cache.get_pipeline_future(pipeline_id).add_done_callback(
        lambda _future: ran.set())
    assert ran.wait(TIMEOUT), "build never finished"


def _put_recording(
    cache: LRUPipelineCache,
    config: str,
    *,
    before_loading: Callable[[], None] | None = None,
) -> list[tuple[str, object]]:
    """Put pipeline ``A`` and return the statuses it announced, in order."""
    statuses: list[tuple[str, object]] = []

    def begin() -> None:
        if before_loading is not None:
            before_loading()
        statuses.append(("loading", None))

    cache.put_pipeline(
        "A", config,
        begin_load_callback=begin,
        finish_load_callback=lambda: statuses.append(("loaded", None)),
        fail_load_callback=lambda exc: statuses.append(("failed", exc)),
    )
    cache._load_executor.wait_all(TIMEOUT)
    return statuses


def test_build_finishing_before_loading_is_announced_after_it(
    test_grr: GenomicResourceRepo,
) -> None:
    cache = LRUPipelineCache(test_grr, 2)

    statuses = _put_recording(
        cache, CONFIG,
        before_loading=lambda: _wait_for_build_callbacks(cache, "A"),
    )

    assert statuses == [("loading", None), ("loaded", None)]


def test_build_failing_before_loading_is_announced_after_it(
    test_grr: GenomicResourceRepo,
) -> None:
    cache = LRUPipelineCache(test_grr, 2)

    statuses = _put_recording(
        cache, UNBUILDABLE_CONFIG,
        before_loading=lambda: _wait_for_build_callbacks(cache, "A"),
    )

    error = cache.get_pipeline_future("A").exception()
    assert error is not None
    assert statuses == [("loading", None), ("failed", error)]


def _sequential_cache(test_grr: GenomicResourceRepo) -> LRUPipelineCache:
    """Return a cache whose builds and their callbacks run inside execute.

    The build's terminal callback then fires on the request thread, while
    ``put_pipeline`` still holds the cache lock and before it has announced
    ``loading``.
    """
    cache = LRUPipelineCache(test_grr, 2)
    cache._load_executor = cast(
        ThreadedTaskExecutor, SequentialTaskExecutor())
    return cache


@pytest.mark.parametrize(
    ("config", "terminal"),
    [(CONFIG, "loaded"), (UNBUILDABLE_CONFIG, "failed")],
    ids=["loaded", "failed"],
)
def test_terminal_status_fired_on_the_request_thread_does_not_hang(
    test_grr: GenomicResourceRepo,
    config: str,
    terminal: str,
) -> None:
    cache = _sequential_cache(test_grr)
    statuses: list[tuple[str, object]] = []

    put = threading.Thread(
        target=lambda: statuses.extend(_put_recording(cache, config)),
        daemon=True,
    )
    put.start()
    put.join(TIMEOUT)

    assert not put.is_alive(), "put_pipeline hung"
    assert [status for status, _ in statuses] == ["loading", terminal]


def test_terminal_status_is_announced_without_a_loading_callback(
    test_grr: GenomicResourceRepo,
) -> None:
    cache = _sequential_cache(test_grr)
    statuses: list[str] = []

    cache.put_pipeline(
        "A", CONFIG,
        finish_load_callback=lambda: statuses.append("loaded"),
    )

    assert statuses == ["loaded"]


def test_terminal_status_is_announced_when_the_loading_callback_raises(
    test_grr: GenomicResourceRepo,
) -> None:
    cache = _sequential_cache(test_grr)
    statuses: list[str] = []

    def begin() -> None:
        raise RuntimeError("channel layer down")

    with pytest.raises(RuntimeError, match="channel layer down"):
        cache.put_pipeline(
            "A", CONFIG,
            begin_load_callback=begin,
            finish_load_callback=lambda: statuses.append("loaded"),
        )

    assert statuses == ["loaded"]


def test_build_dropped_before_loading_is_announced_reports_no_outcome(
    test_grr: GenomicResourceRepo,
) -> None:
    cache = LRUPipelineCache(test_grr, 2)

    def finish_then_unload() -> None:
        _wait_for_build_callbacks(cache, "A")
        cache.unload_pipeline("A")

    statuses = _put_recording(
        cache, CONFIG, before_loading=finish_then_unload)

    assert statuses == [("loading", None)]
