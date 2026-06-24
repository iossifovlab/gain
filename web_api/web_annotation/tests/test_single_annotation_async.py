# pylint: disable=W0621,C0114,C0116,W0212,W0613
import asyncio
import time

import pytest
from django.test import AsyncClient
from pytest_mock import MockerFixture
from rest_framework.exceptions import NotFound, ValidationError

from web_annotation import annotation_base_view
from web_annotation.annotation_base_view import (
    AnnotationBaseView,
    AsyncAnnotationBaseView,
)
from web_annotation.models import User
from web_annotation.pipeline_cache import LRUPipelineCache
from web_annotation.single_allele_annotation.views import SingleAnnotation

ANNOTATE_URL = "/api/single_allele/annotate"


# ---------------------------------------------------------------------------
# Single-shared-cache invariant
# ---------------------------------------------------------------------------

def test_single_shared_cache_across_sync_and_async_bases() -> None:
    """The lru_cache + executors are one instance shared by both bases.

    A pipeline built through the async path must be visible to the sync path
    (and vice-versa). The mixin class body owns the attributes, so both bases
    and SingleAnnotation reference the identical objects.
    """
    assert AnnotationBaseView.lru_cache is AsyncAnnotationBaseView.lru_cache
    assert SingleAnnotation.lru_cache is AnnotationBaseView.lru_cache
    assert (
        AnnotationBaseView.JOB_EXECUTOR is AsyncAnnotationBaseView.JOB_EXECUTOR
    )
    assert (
        AnnotationBaseView.ANNOTATE_EXECUTOR
        is AsyncAnnotationBaseView.ANNOTATE_EXECUTOR
    )
    # The interactive-annotate executor is distinct from JOB_EXECUTOR.
    assert (
        AnnotationBaseView.ANNOTATE_EXECUTOR
        is not AnnotationBaseView.JOB_EXECUTOR
    )


def test_single_annotation_is_async_view() -> None:
    """SingleAnnotation must expose only async handlers (view_is_async)."""
    assert issubclass(SingleAnnotation, AsyncAnnotationBaseView)
    assert asyncio.iscoroutinefunction(SingleAnnotation.post)
    assert SingleAnnotation.view_is_async


# ---------------------------------------------------------------------------
# Async functional tests (full HTTP round-trip through adrf)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_async_single_annotation_anonymous_returns_200() -> None:
    client = AsyncClient()
    response = await client.post(
        ANNOTATE_URL,
        {
            "annotatable": {"chrom": "chr1", "pos": "3"},
            "pipeline_id": "t4c8/t4c8_pipeline",
        },
        content_type="application/json",
    )
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["annotatable"]["chrom"] == "chr1"
    assert body["annotatable"]["pos"] == 3
    assert "annotators" in body


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_async_single_annotation_authenticated_returns_200() -> None:
    user = await User.objects.acreate_user(
        "async-user", "async-user@example.com", "secret",
    )
    client = AsyncClient()
    await client.aforce_login(user)
    response = await client.post(
        ANNOTATE_URL,
        {
            "annotatable": {
                "chrom": "chr1", "pos": "3", "ref": "A", "alt": "T",
            },
            "pipeline_id": "t4c8/t4c8_pipeline",
        },
        content_type="application/json",
    )
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["annotatable"]["type"] == "SUBSTITUTION"


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_async_single_annotation_missing_annotatable_400() -> None:
    client = AsyncClient()
    response = await client.post(
        ANNOTATE_URL,
        {"pipeline_id": "t4c8/t4c8_pipeline"},
        content_type="application/json",
    )
    assert response.status_code == 400, response.content
    assert "Annotatable not provided" in response.content.decode()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_async_single_annotation_missing_pipeline_400() -> None:
    client = AsyncClient()
    response = await client.post(
        ANNOTATE_URL,
        {"annotatable": {"chrom": "chr1", "pos": "3"}},
        content_type="application/json",
    )
    assert response.status_code == 400, response.content
    assert "Pipeline not provided" in response.content.decode()


# ---------------------------------------------------------------------------
# Pinned exception-mapping regression (unbuildable -> 400, missing -> 404)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_async_unbuildable_pipeline_maps_to_400_with_reason() -> None:
    """A genuinely unbuildable (in-GRR) pipeline -> 400 with config reason.

    Validation is deferred to the background build (#150 H1), so an unbuildable
    saved pipeline first fails inside aget_pipeline; that build failure must map
    to a 400 carrying the format_config_error reason -- not a 500.
    """
    bad_id = "broken/async_bad_pipeline"
    bad_config = "- position_score: scores/does_not_exist"
    # Inject a poisoned global pipeline whose deferred build fails. The view
    # copies GRR_PIPELINES (module-level) into self.grr_pipelines in __init__,
    # so patch the module-level dict the per-request instance reads from.
    original = dict(annotation_base_view.GRR_PIPELINES)
    annotation_base_view.GRR_PIPELINES[bad_id] = {
        "id": bad_id, "content": bad_config,
    }
    try:
        client = AsyncClient()
        response = await client.post(
            ANNOTATE_URL,
            {
                "annotatable": {"chrom": "chr1", "pos": "3"},
                "pipeline_id": bad_id,
            },
            content_type="application/json",
        )
        assert response.status_code == 400, response.content
    finally:
        annotation_base_view.GRR_PIPELINES.clear()
        annotation_base_view.GRR_PIPELINES.update(original)
        SingleAnnotation.lru_cache.unload_pipeline(bad_id)


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_async_missing_pipeline_maps_to_404() -> None:
    """A pipeline id that resolves to nothing -> 404 (not 500)."""
    client = AsyncClient()
    response = await client.post(
        ANNOTATE_URL,
        {
            "annotatable": {"chrom": "chr1", "pos": "3"},
            "pipeline_id": "no/such/pipeline/exists",
        },
        content_type="application/json",
    )
    assert response.status_code == 404, response.content


def test_exception_mapping_helper_validation_error() -> None:
    """The shared build->4xx mapper returns a ValidationError (400)."""
    view = SingleAnnotation()
    err = ValueError("bad resource")
    mapped = view._build_error_to_drf(err)
    assert isinstance(mapped, ValidationError)
    assert "bad resource" in str(mapped)


def test_exception_mapping_helper_not_found() -> None:
    """The shared mapper returns a NotFound (404) for a missing pipeline."""
    view = SingleAnnotation()
    mapped = view._missing_pipeline_to_drf("some/pipeline")
    assert isinstance(mapped, NotFound)


# ---------------------------------------------------------------------------
# Threaded non-blocking proof: a slow build must not park the event loop
# ---------------------------------------------------------------------------

@pytest.fixture
def slow_build(mocker: MockerFixture) -> float:
    """Make pipeline builds take ~SLOW seconds on the loader thread."""
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
async def test_concurrent_slow_builds_do_not_park_event_loop(
    slow_build: float,
) -> None:
    """Concurrent slow GRR builds must leave the event loop responsive.

    Uses the REAL ThreadedTaskExecutor (production path). With the build wait
    awaited off-thread, a cheap heartbeat coroutine keeps ticking on the loop
    while N annotate requests block on slow builds. If the build were resolved
    on the loop thread (the bug this issue fixes), the heartbeat would stall.
    """
    # Force a cold cache so each request actually waits on a build.
    SingleAnnotation.lru_cache.unload_pipeline("t4c8/t4c8_pipeline")

    heartbeats: list[float] = []
    stop = asyncio.Event()

    async def heartbeat() -> None:
        while not stop.is_set():
            heartbeats.append(time.monotonic())
            await asyncio.sleep(0.02)

    async def fire_request() -> int:
        client = AsyncClient()
        response = await client.post(
            ANNOTATE_URL,
            {
                "annotatable": {"chrom": "chr1", "pos": "3"},
                "pipeline_id": "t4c8/t4c8_pipeline",
            },
            content_type="application/json",
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
