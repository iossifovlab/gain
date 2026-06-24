# pylint: disable=W0621,C0114,C0116,W0212,W0613
import asyncio
import time

import pytest
from django.test import AsyncClient
from pytest_mock import MockerFixture

from web_annotation import annotation_base_view
from web_annotation.annotation_base_view import (
    AnnotationBaseView,
    AsyncAnnotationBaseView,
)
from web_annotation.editor.views import (
    AsyncEditorView,
    EditorView,
    PipelineAttributes,
    PipelineStatus,
)
from web_annotation.pipeline_cache import LRUPipelineCache

STATUS_URL = "/api/editor/pipeline_status"
ATTRIBUTES_URL = "/api/editor/pipeline_attributes"


# ---------------------------------------------------------------------------
# Mixin split + async-view structure
# ---------------------------------------------------------------------------

def test_editor_mixin_split_keeps_sync_editor_view() -> None:
    """``EditorView`` stays sync; ``AsyncEditorView`` is the async sibling.

    Both editor bases share the same ``EditorMixin`` editor helpers, and each
    inherits its dispatch behaviour from the matching annotation base.
    """
    assert issubclass(EditorView, AnnotationBaseView)
    assert issubclass(AsyncEditorView, AsyncAnnotationBaseView)
    # Both editor bases see the editor-specific helpers from the shared mixin.
    assert hasattr(EditorView, "_get_annotator_types")
    assert hasattr(AsyncEditorView, "_get_annotator_types")
    assert (
        EditorView._get_annotator_types
        is AsyncEditorView._get_annotator_types
    )


def test_converted_editor_gets_are_async_views() -> None:
    """Both converted read GETs must expose only async handlers."""
    assert issubclass(PipelineAttributes, AsyncEditorView)
    assert issubclass(PipelineStatus, AsyncEditorView)
    assert asyncio.iscoroutinefunction(PipelineAttributes.get)
    assert asyncio.iscoroutinefunction(PipelineStatus.get)
    assert PipelineAttributes.view_is_async
    assert PipelineStatus.view_is_async


def test_editor_bases_share_one_cache() -> None:
    """The single-shared-cache invariant holds across editor bases.

    The cache/executors live on ``AnnotationMixin``; both editor bases inherit
    the very same objects, so a pipeline built through the async editor path is
    visible to every other (sync or async) view and vice-versa.
    """
    assert EditorView.lru_cache is AsyncEditorView.lru_cache
    assert EditorView.lru_cache is AnnotationBaseView.lru_cache
    assert EditorView.lru_cache is AsyncAnnotationBaseView.lru_cache
    assert PipelineAttributes.lru_cache is AnnotationBaseView.lru_cache
    assert PipelineStatus.lru_cache is AnnotationBaseView.lru_cache


# ---------------------------------------------------------------------------
# Async functional tests (full HTTP round-trip through adrf)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_async_pipeline_status_anonymous_returns_200() -> None:
    client = AsyncClient()
    response = await client.get(
        f"{STATUS_URL}?pipeline_id=t4c8/t4c8_pipeline",
    )
    assert response.status_code == 200, response.content
    body = response.json()
    assert body == {
        "attributes_count": 5,
        "annotators_count": 2,
        "annotatables": [],
        "gene_lists": ["gene_list"],
    }


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_async_pipeline_attributes_anonymous_returns_200() -> None:
    client = AsyncClient()
    response = await client.get(
        f"{ATTRIBUTES_URL}?pipeline_id=t4c8/t4c8_pipeline"
        "&attribute_type=gene_list",
    )
    assert response.status_code == 200, response.content
    assert response.json() == ["gene_list"]


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_async_pipeline_attributes_no_type_returns_all() -> None:
    client = AsyncClient()
    response = await client.get(
        f"{ATTRIBUTES_URL}?pipeline_id=pipeline/test_pipeline",
    )
    assert response.status_code == 200, response.content
    assert response.json() == ["position_1"]


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_async_pipeline_status_missing_pipeline_id_400() -> None:
    client = AsyncClient()
    response = await client.get(STATUS_URL)
    assert response.status_code == 400, response.content
    assert "pipeline_id is required" in response.content.decode()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_async_pipeline_attributes_missing_pipeline_id_400() -> None:
    client = AsyncClient()
    response = await client.get(ATTRIBUTES_URL)
    assert response.status_code == 400, response.content
    assert "pipeline_id is required" in response.content.decode()


# ---------------------------------------------------------------------------
# Pinned exception-mapping regression (unbuildable -> 400, missing -> 404)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_async_status_unbuildable_pipeline_maps_to_400() -> None:
    """A genuinely unbuildable (in-GRR) pipeline -> 400 (not 500).

    Validation is deferred to the background build (#150 H1), so an unbuildable
    saved pipeline first fails inside ``aget_pipeline``; that build failure must
    map to a 400, not escape as a 500.
    """
    bad_id = "broken/editor_bad_pipeline"
    bad_config = "- position_score: scores/does_not_exist"
    original = dict(annotation_base_view.GRR_PIPELINES)
    annotation_base_view.GRR_PIPELINES[bad_id] = {
        "id": bad_id, "content": bad_config,
    }
    try:
        client = AsyncClient()
        response = await client.get(f"{STATUS_URL}?pipeline_id={bad_id}")
        assert response.status_code == 400, response.content
    finally:
        annotation_base_view.GRR_PIPELINES.clear()
        annotation_base_view.GRR_PIPELINES.update(original)
        PipelineStatus.lru_cache.unload_pipeline(bad_id)


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_async_attributes_missing_pipeline_maps_to_404() -> None:
    """A pipeline id that resolves to nothing -> 404 (not 500)."""
    client = AsyncClient()
    response = await client.get(
        f"{ATTRIBUTES_URL}?pipeline_id=no/such/pipeline/exists",
    )
    assert response.status_code == 404, response.content


# ---------------------------------------------------------------------------
# Threaded non-blocking proof: a slow build must not park the event loop
# ---------------------------------------------------------------------------

@pytest.fixture
def slow_build(mocker: MockerFixture) -> float:
    """Make pipeline builds take ~SLOW seconds on the loader thread.

    No ``GPFWA_BUILD_DELAY_SECONDS`` hook on this branch (#164 not yet merged),
    so the delay is injected by monkeypatching ``_load_pipeline_raw`` -- the
    same point a slow real GRR build would block, on the loader thread.
    """
    slow_seconds = 0.4
    real_load = LRUPipelineCache._load_pipeline_raw

    def slow_load(raw, grr, pipeline_id="unknown"):  # type: ignore
        time.sleep(slow_seconds)
        return real_load(raw, grr, pipeline_id)

    mocker.patch.object(
        LRUPipelineCache, "_load_pipeline_raw", staticmethod(slow_load),
    )
    return slow_seconds


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_concurrent_slow_status_builds_do_not_park_event_loop(
    slow_build: float,
) -> None:
    """Concurrent slow GRR builds must leave the event loop responsive.

    Uses the REAL ThreadedTaskExecutor (production path). With the build wait
    awaited off the loop via ``aget_pipeline``, a cheap heartbeat coroutine
    keeps ticking while N status requests block on slow builds. If the build
    were resolved ON the loop thread (the bug this issue fixes), the heartbeat
    would stall for the whole slow-build window.
    """
    # Force a cold cache so each request actually waits on a build.
    PipelineStatus.lru_cache.unload_pipeline("t4c8/t4c8_pipeline")

    heartbeats: list[float] = []
    stop = asyncio.Event()

    async def heartbeat() -> None:
        while not stop.is_set():
            heartbeats.append(time.monotonic())
            await asyncio.sleep(0.02)

    async def fire_request() -> int:
        client = AsyncClient()
        response = await client.get(
            f"{STATUS_URL}?pipeline_id=t4c8/t4c8_pipeline",
        )
        return response.status_code

    hb_task = asyncio.ensure_future(heartbeat())
    requests = [asyncio.ensure_future(fire_request()) for _ in range(4)]
    statuses = await asyncio.gather(*requests)
    stop.set()
    await hb_task

    assert all(s == 200 for s in statuses), statuses

    # The event loop kept ticking throughout the slow builds: the heartbeat
    # fired many times and never went silent for longer than the slow-build
    # window. A loop parked on future.result() would show a single long gap.
    assert len(heartbeats) >= 5
    gaps = [
        heartbeats[i + 1] - heartbeats[i]
        for i in range(len(heartbeats) - 1)
    ]
    max_gap = max(gaps)
    assert max_gap < slow_build, (
        f"event loop stalled for {max_gap:.3f}s "
        f"(>= slow build {slow_build:.3f}s) -- build ran ON the loop"
    )
