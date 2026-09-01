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
from web_annotation.pipelines.views import PipelineDoc
from web_annotation.tests.loop_stall import (
    HEARTBEAT_INTERVAL_SECONDS,
    SABOTAGED_STALL_FLOOR_SECONDS,
    SLOW_BUILD_SECONDS,
    STALL_THRESHOLD_SECONDS,
    max_gap,
)

DOC_URL = "/api/pipelines/doc"


# ---------------------------------------------------------------------------
# Async-view structure + single-shared-cache invariant
# ---------------------------------------------------------------------------

def test_pipeline_doc_is_async_view() -> None:
    """``PipelineDoc`` must extend the async base and expose only async GET.

    adrf dispatches a view async iff *all* its handlers are coroutines
    (``view_is_async``); ``PipelineDoc`` has only ``get`` so converting it is
    sufficient (iossifovlab/gain#167).
    """
    assert issubclass(PipelineDoc, AsyncAnnotationBaseView)
    assert asyncio.iscoroutinefunction(PipelineDoc.get)
    assert PipelineDoc.view_is_async


def test_pipeline_doc_shares_one_cache() -> None:
    """The single-shared-cache invariant holds for the converted view.

    The cache/executors live on ``AnnotationMixin``; the converted view
    inherits the very same objects, so a pipeline built through any other
    (sync or async) path is visible here and vice-versa.
    """
    assert PipelineDoc.lru_cache is AnnotationBaseView.lru_cache
    assert PipelineDoc.lru_cache is AsyncAnnotationBaseView.lru_cache


# ---------------------------------------------------------------------------
# Async functional tests -- BOTH return paths
# ---------------------------------------------------------------------------
# Path A: the ``HttpResponse`` rendered-doc download (the happy path).
# Path B: the early-return DRF ``Response`` (missing pipeline_id -> 400).
# Both must survive adrf's async dispatch + DRF ``finalize_response``
# byte-for-byte identical to the prior sync behaviour.

@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_async_pipeline_doc_returns_html_download_anonymous() -> None:
    """HttpResponse path: a rendered HTML doc download (anonymous)."""
    client = AsyncClient()
    response = await client.get(
        f"{DOC_URL}?pipeline_id=t4c8/t4c8_pipeline",
    )
    assert response.status_code == 200, response.content
    assert response["Content-Type"].startswith("text/html")
    assert "attachment" in response["Content-Disposition"]
    assert "t4c8/t4c8_pipeline.html" in response["Content-Disposition"]
    assert len(response.content) > 0


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_async_pipeline_doc_returns_html_download_authenticated(
) -> None:
    """HttpResponse path resolves a GRR pipeline for an authenticated user."""
    user = await User.objects.acreate_user(
        "doc-user", "doc-user@example.com", "secret",
    )
    client = AsyncClient()
    await client.aforce_login(user)
    response = await client.get(
        f"{DOC_URL}?pipeline_id=t4c8/t4c8_pipeline",
    )
    assert response.status_code == 200, response.content
    assert response["Content-Type"].startswith("text/html")
    assert "attachment" in response["Content-Disposition"]
    assert "t4c8/t4c8_pipeline.html" in response["Content-Disposition"]
    assert len(response.content) > 0


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_async_pipeline_doc_does_not_expose_pipeline_path() -> None:
    """HttpResponse body content is byte-for-byte the prior sync output.

    The rendered doc must not leak the server-side pipeline path -- the same
    assertion the sync test pinned -- so the async render path produces the
    identical document content.
    """
    client = AsyncClient()
    response = await client.get(
        f"{DOC_URL}?pipeline_id=t4c8/t4c8_pipeline",
    )
    assert response.status_code == 200, response.content
    content = response.content.decode()
    assert "Pipeline path:" not in content


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_the_downloaded_doc_addresses_resources_on_the_public_url(
) -> None:
    """The served document links the GRR's public mirror (#841).

    The download is opened in the user's own browser, so an address only
    the server can resolve -- production's GRR is a group of directory
    repositories mounted read-only into the container -- resolves nowhere
    once the file leaves. What the deployment publishes its GRR as is the
    child repository's ``public_url``.

    Asserted on the response bytes rather than on the render helper.
    Since #952 the document comes from ``gain``'s one renderer, whose
    default policy is pinned in ``core``; what is this project's own, and
    what actually regressed in #841, is that the *endpoint* serves that
    document unaltered. A test one call further in cannot see a view that
    stops calling the shared renderer.
    """
    client = AsyncClient()
    response = await client.get(
        f"{DOC_URL}?pipeline_id=t4c8/t4c8_pipeline",
    )

    assert response.status_code == 200, response.content
    content = response.content.decode()
    assert 'href="http://test/t4c8/t4c8_genes/index.html"' in content
    assert 'href="http://test/t4c8/t4c8_genome/index.html"' in content


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_async_pipeline_doc_missing_pipeline_id_returns_400() -> None:
    """Response path: the early DRF ``Response`` 400 survives async dispatch.

    The bare-HttpResponse happy path and this DRF ``Response`` 400 take
    different ``finalize_response`` branches; both must round-trip correctly.
    """
    client = AsyncClient()
    response = await client.get(DOC_URL)
    assert response.status_code == 400, response.content
    assert "pipeline_id not provided" in response.content.decode()


# ---------------------------------------------------------------------------
# Pinned exception-mapping regression (unbuildable -> 400, missing -> 404)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_async_doc_unbuildable_pipeline_maps_to_400() -> None:
    """A genuinely unbuildable (in-GRR) pipeline -> 400 (not 500).

    Validation is deferred to the background build (#150 H1), so an unbuildable
    saved pipeline first fails inside ``aget_pipeline``; that build failure must
    map to a 400, not escape as a 500.
    """
    bad_id = "broken/doc_bad_pipeline"
    bad_config = "- position_score: scores/does_not_exist"
    original = dict(annotation_base_view.GRR_PIPELINES)
    annotation_base_view.GRR_PIPELINES[bad_id] = {
        "id": bad_id, "content": bad_config,
    }
    try:
        client = AsyncClient()
        response = await client.get(f"{DOC_URL}?pipeline_id={bad_id}")
        assert response.status_code == 400, response.content
    finally:
        annotation_base_view.GRR_PIPELINES.clear()
        annotation_base_view.GRR_PIPELINES.update(original)
        PipelineDoc.lru_cache.unload_pipeline(bad_id)


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_async_doc_missing_pipeline_maps_to_404() -> None:
    """A pipeline id that resolves to nothing -> 404 (not 500)."""
    client = AsyncClient()
    response = await client.get(
        f"{DOC_URL}?pipeline_id=no/such/pipeline/exists",
    )
    assert response.status_code == 404, response.content


def test_doc_exception_mapping_helper_validation_error() -> None:
    """The shared build->4xx mapper returns a ValidationError (400)."""
    view = PipelineDoc()
    err = ValueError("bad resource")
    mapped = view._build_error_to_drf(err)
    assert isinstance(mapped, ValidationError)
    assert "bad resource" in str(mapped)


def test_doc_exception_mapping_helper_not_found() -> None:
    """The shared mapper returns a NotFound (404) for a missing pipeline."""
    view = PipelineDoc()
    mapped = view._missing_pipeline_to_drf("some/pipeline")
    assert isinstance(mapped, NotFound)


# ---------------------------------------------------------------------------
# Threaded non-blocking proof: a slow build must not park the event loop
# ---------------------------------------------------------------------------

@pytest.fixture
def slow_build(mocker: MockerFixture) -> float:
    """Make builds take ~``SLOW_BUILD_SECONDS`` on the loader thread.

    Uses the ``GPFWA_BUILD_DELAY_SECONDS`` hook baked into
    ``_load_pipeline_raw`` (#164) -- the same place a slow real GRR build would
    block, on the loader thread -- by monkeypatching the env-reader to return a
    fixed delay. This keeps the delay deterministic without depending on the
    process environment.

    Returns the injected latency for diagnostics only. The assertion bound is
    the independent ``STALL_THRESHOLD_SECONDS`` -- see ``loop_stall`` for why
    the two must not be the same number (#454).
    """
    slow_seconds = SLOW_BUILD_SECONDS
    mocker.patch(
        "web_annotation.pipeline_cache._load_test_build_delay",
        return_value=slow_seconds,
    )
    return slow_seconds


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_concurrent_slow_doc_builds_do_not_park_event_loop(
    slow_build: float,
) -> None:
    """Concurrent slow GRR builds must leave the event loop responsive.

    Uses the REAL ThreadedTaskExecutor (production path). With the build wait
    awaited off the loop via ``aget_pipeline``, a cheap heartbeat coroutine
    keeps ticking while N doc requests block on slow builds. If the build were
    resolved ON the loop thread (the bug this issue fixes) the heartbeat would
    stall for the whole slow-build window.
    """
    # Force a cold cache so each request actually waits on a build.
    PipelineDoc.lru_cache.unload_pipeline("t4c8/t4c8_pipeline")

    heartbeats: list[float] = []
    stop = asyncio.Event()

    async def heartbeat() -> None:
        while not stop.is_set():
            heartbeats.append(time.monotonic())
            await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)

    async def fire_request() -> int:
        client = AsyncClient()
        response = await client.get(
            f"{DOC_URL}?pipeline_id=t4c8/t4c8_pipeline",
        )
        return response.status_code

    hb_task = asyncio.ensure_future(heartbeat())
    requests = [asyncio.ensure_future(fire_request()) for _ in range(4)]
    statuses = await asyncio.gather(*requests)
    stop.set()
    await hb_task

    assert all(s == 200 for s in statuses), statuses

    # The event loop kept ticking throughout the slow builds: the heartbeat
    # fired many times and never went silent for longer than the threshold.
    # A loop parked on future.result() would show a single long gap of about
    # one build's duration -- not N x it, because put_pipeline dedupes the
    # concurrent readers onto a shared build future.
    # The stall check comes first so that a regression reports the stall it
    # actually caused; the tick-count guard below would otherwise fire first
    # (a parked loop also ticks fewer times) and misreport the cause.
    assert len(heartbeats) >= 2, (
        f"heartbeat coroutine barely ran ({len(heartbeats)} ticks) -- "
        f"no gap to measure"
    )
    worst_gap = max_gap(heartbeats)
    assert worst_gap < STALL_THRESHOLD_SECONDS, (
        f"event loop stalled for {worst_gap:.3f}s "
        f"(>= threshold {STALL_THRESHOLD_SECONDS:.3f}s, injected build "
        f"latency {slow_build:.3f}s) -- build ran ON the loop"
    )
    assert len(heartbeats) >= 5, len(heartbeats)


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_slow_doc_build_heartbeat_proof_is_discriminating(
    slow_build: float,
    mocker: MockerFixture,
) -> None:
    """Sabotage check: resolving the build ON the loop FAILS the heartbeat.

    Proves the non-blocking test above is discriminating, not vacuously green.
    We leave the view's ``aget_pipeline`` intact (so ``put_pipeline`` still runs
    off-loop and its ``async_to_sync`` channel callbacks stay legal), but
    sabotage the cache's ``aget_pipeline`` to block the loop thread on the
    shared build future's ``result()`` -- exactly what the sync ``get_pipeline``
    would do on the loop. The heartbeat must then show a single gap reaching
    ``SABOTAGED_STALL_FLOOR_SECONDS`` -- which is above
    ``STALL_THRESHOLD_SECONDS``, so the real test's
    ``worst_gap < STALL_THRESHOLD_SECONDS`` assertion would FAIL under this
    sabotage.
    """
    PipelineDoc.lru_cache.unload_pipeline("t4c8/t4c8_pipeline")

    async def blocking_cache_aget(self, pipeline_id):  # type: ignore  # ruff: ignore[unused-async]
        # WRONG (and intentionally so): block the loop thread on the concurrent
        # future's result() instead of awaiting it off-loop via await_build.
        # Must stay `async def` to replace the async
        # LRUPipelineCache.aget_pipeline the converted view awaits.
        future = self.get_pipeline_future(pipeline_id)
        return future.result()

    mocker.patch.object(
        LRUPipelineCache, "aget_pipeline", blocking_cache_aget,
    )

    heartbeats: list[float] = []
    stop = asyncio.Event()

    async def heartbeat() -> None:
        while not stop.is_set():
            heartbeats.append(time.monotonic())
            await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)

    async def fire_request() -> int:
        client = AsyncClient()
        response = await client.get(
            f"{DOC_URL}?pipeline_id=t4c8/t4c8_pipeline",
        )
        return response.status_code

    hb_task = asyncio.ensure_future(heartbeat())
    request = asyncio.ensure_future(fire_request())
    await request
    stop.set()
    await hb_task

    # The sabotaged on-loop resolve parks the loop for a whole build window, so
    # the real test's assertion (worst_gap < STALL_THRESHOLD_SECONDS) would
    # FAIL here -- SABOTAGED_STALL_FLOOR_SECONDS sits above that bound, so
    # reaching it proves the breach. Deliberately NOT the full injected
    # latency: requiring the gap to reach every last millisecond of the sleep
    # would itself be a coin flip (#454).
    worst_gap = max_gap(heartbeats)
    assert worst_gap >= SABOTAGED_STALL_FLOOR_SECONDS, (
        f"expected an on-loop stall >= {SABOTAGED_STALL_FLOOR_SECONDS:.3f}s "
        f"(injected build latency {slow_build:.3f}s), "
        f"got max gap {worst_gap:.3f}s -- the sabotage may have stopped "
        f"biting, which would make the proof above vacuous"
    )
