# pylint: disable=W0621,C0114,C0116,W0212,W0613
import asyncio
import logging
import threading
import time
from typing import Any

import pytest
import pytest_mock
from asgiref.sync import sync_to_async
from django.test import AsyncClient
from rest_framework.request import Request

from web_annotation.annotation_base_view import (
    AnnotationBaseView,
    AsyncAnnotationBaseView,
)
from web_annotation.pipelines import views
from web_annotation.pipelines.views import PipelineValidation
from web_annotation.tests.loop_stall import (
    HEARTBEAT_INTERVAL_SECONDS,
    SABOTAGED_STALL_FLOOR_SECONDS,
    SLOW_BUILD_SECONDS,
    STALL_THRESHOLD_SECONDS,
    max_gap,
)

VALIDATE_URL = "/api/pipelines/validate"
VERSION_URL = "/api/version"

# A config that builds against the fixture GRR, and one that does not. Both
# answer 200 -- an invalid config is a validation *result*, not a bad request.
VALID_CONFIG = "- position_score: scores/pos1"
INVALID_CONFIG = "- position_score: scores/does_not_exist"

# The exact ``errors`` text INVALID_CONFIG produced before the async
# conversion (#659). Pinned verbatim: the pipeline editor shows this string to
# the user, and the whole point of the conversion is that nothing observable
# changes.
INVALID_CONFIG_ERRORS = (
    "Invalid configuration, reason: The A0 annotator configuration is "
    "incorrect:  resource <scores/does_not_exist> (None) not found"
)

#: ``ThreadedTaskExecutor`` names its workers after this, which is how a test
#: can tell the validation pool's threads from the loop thread and from the
#: ``thread_sensitive`` sync-view thread by name alone.
VALIDATE_THREAD_PREFIX = "pipeline-validate"


# ---------------------------------------------------------------------------
# Async-view structure + single-shared-cache invariant
# ---------------------------------------------------------------------------

def test_pipeline_validation_is_async_view() -> None:
    """``PipelineValidation`` must extend the async base and only async POST.

    adrf dispatches a view async iff *all* its handlers are coroutines
    (``view_is_async``); a view converted halfway silently stays sync.
    ``PipelineValidation`` has only ``post``, so converting it is total
    (iossifovlab/gain#659).
    """
    assert issubclass(PipelineValidation, AsyncAnnotationBaseView)
    assert asyncio.iscoroutinefunction(PipelineValidation.post)
    assert PipelineValidation.view_is_async


def test_pipeline_validation_shares_one_cache() -> None:
    """The single-shared-cache invariant holds for the converted view.

    The cache/executors live on ``AnnotationMixin``; the converted view
    inherits the very same objects, so a pipeline built through any other
    (sync or async) path is visible here and vice-versa.
    """
    assert PipelineValidation.lru_cache is AnnotationBaseView.lru_cache
    assert PipelineValidation.lru_cache is AsyncAnnotationBaseView.lru_cache


def test_validation_builds_have_their_own_bounded_pool() -> None:
    """Validation builds must not draw on the annotate or job pools.

    ``/api/pipelines/validate`` is anonymous; the interactive-annotate
    endpoint is authenticated and quota'd, and the job pool runs users' file
    annotations. One pool shared between them would let unauthenticated
    traffic occupy every worker the paid-for endpoints need -- the same
    starvation this issue removes from the sync-view thread. Like the other
    pools it lives on the shared mixin, so both bases see one instance.
    """
    assert (
        PipelineValidation.VALIDATE_EXECUTOR
        is not AnnotationBaseView.ANNOTATE_EXECUTOR
    )
    assert (
        PipelineValidation.VALIDATE_EXECUTOR
        is not AnnotationBaseView.JOB_EXECUTOR
    )
    assert (
        AnnotationBaseView.VALIDATE_EXECUTOR
        is AsyncAnnotationBaseView.VALIDATE_EXECUTOR
    )


# ---------------------------------------------------------------------------
# Contract equivalence -- every response is byte-for-byte what the sync view
# returned. Captured from the pre-conversion endpoint and pinned here.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_async_validate_accepts_a_valid_config() -> None:
    """A buildable config: 200 with an EMPTY ``errors`` string.

    The editor keys ``isConfigValid`` off exactly this -- an empty string,
    not a missing key and not a null.
    """
    client = AsyncClient()
    response = await client.post(VALIDATE_URL, {"config": VALID_CONFIG})

    assert response.status_code == 200, response.content
    assert response.json() == {"errors": ""}


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_async_validate_reports_an_invalid_config_unchanged() -> None:
    """An unbuildable config: 200 with the same ``errors`` text as before."""
    client = AsyncClient()
    response = await client.post(VALIDATE_URL, {"config": INVALID_CONFIG})

    assert response.status_code == 200, response.content
    assert response.json() == {"errors": INVALID_CONFIG_ERRORS}


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_async_validate_refuses_an_oversized_body_unchanged() -> None:
    """Bound 1 (body size) still refuses with 400 and its message."""
    oversized = "#" * (PipelineValidation.MAX_CONFIG_LENGTH + 1)
    client = AsyncClient()
    response = await client.post(VALIDATE_URL, {"config": oversized})

    assert response.status_code == 400, response.content
    assert response.json() == {"error": (
        f"annotation config is too long: {len(oversized)} characters, "
        f"at most {PipelineValidation.MAX_CONFIG_LENGTH} are accepted"
    )}


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_async_validate_refuses_too_many_annotators_unchanged() -> None:
    """Bound 2 (declared annotators) still refuses with 400 and its message."""
    declared = PipelineValidation.MAX_ANNOTATORS + 1
    client = AsyncClient()
    response = await client.post(
        VALIDATE_URL, {"config": "- position_score: 'score_*'\n" * declared})

    assert response.status_code == 400, response.content
    assert response.json() == {"error": (
        f"annotation config declares too many annotators: {declared}, "
        f"at most {PipelineValidation.MAX_ANNOTATORS} are accepted"
    )}


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_async_validate_refuses_too_large_an_expansion_unchanged(
    mocker: pytest_mock.MockerFixture,
) -> None:
    """Bound 3 (expansion) still refuses with 400, before any build.

    The limit is lowered because the fixture GRR is too small to reach the
    configured one -- the same device the sync test uses.
    """
    mocker.patch.object(PipelineValidation, "MAX_EXPANDED_ANNOTATORS", 4)
    build = mocker.patch(
        "web_annotation.pipelines.views.load_pipeline_from_yaml")
    client = AsyncClient()
    response = await client.post(
        VALIDATE_URL, {"config": "- position_score: '*'\n" * 3})

    assert response.status_code == 400, response.content
    assert response.json() == {"error": (
        "annotation config expands to too many annotators: 9, "
        "at most 4 are accepted"
    )}
    # Still refused before the expensive half, and before anything reaches a
    # worker pool.
    build.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_async_validate_refuses_a_request_with_no_config() -> None:
    """A missing ``config`` is still a 400 with its message, not a 500."""
    client = AsyncClient()
    response = await client.post(VALIDATE_URL, {})

    assert response.status_code == 400, response.content
    assert response.json() == {
        "error": "config is required and must be a string"}


# ---------------------------------------------------------------------------
# The point of the change: the build no longer occupies the shared
# thread_sensitive sync-view thread.
# ---------------------------------------------------------------------------
# Every SYNC view of a caller sharing one ``ThreadSensitiveContext`` runs on
# one ``thread_sensitive`` worker thread -- which is what Django's async test
# client gives these tests, and what makes the property directly observable
# here. (Under daphne, Django opens a fresh context per HTTP request, so the
# same build instead costs an unbudgeted thread per anonymous request; see
# ``docs/164-async-read-views-slo.md`` and the recorded #659 harness run.)
# Either way the build must not run there, and the two proofs below are the
# observable consequences of it leaving: a cheap sync endpoint stays
# responsive during slow validation builds, and concurrent validations no
# longer serialize.
#
# The injected latency (SLOW_BUILD_SECONDS) and the bound
# (STALL_THRESHOLD_SECONDS) are the constants the sibling stall proofs use,
# with the measurements that justify them, in ``web_annotation.tests
# .loop_stall`` (#433, #454).

@pytest.fixture
def slow_validate_build(mocker: pytest_mock.MockerFixture) -> float:
    """Make the validation build take ~``SLOW_BUILD_SECONDS``.

    Wraps the module-level ``load_pipeline_from_yaml`` the view builds
    through -- the same point a slow real GRR build would block -- so the
    delay lands wherever that build runs, on the request thread or on a
    worker. That is what makes the proofs below discriminating: they were
    verified red against the pre-#659 inline build.
    """
    real_build = views.load_pipeline_from_yaml

    def slow_build(content: str, grr: Any, **kwargs: Any) -> Any:
        time.sleep(SLOW_BUILD_SECONDS)
        return real_build(content, grr, **kwargs)

    mocker.patch(
        "web_annotation.pipelines.views.load_pipeline_from_yaml", slow_build)
    return SLOW_BUILD_SECONDS


@pytest.fixture
def slow_config_parse(mocker: pytest_mock.MockerFixture) -> float:
    """Make the expansion-gate parse take ~``SLOW_BUILD_SECONDS``.

    Wraps ``AnnotationConfigParser.parse_str`` -- the call whose real cost
    is the repository scan behind each wildcard -- so the delay lands
    wherever the parse runs, on the loop or on a worker.
    """
    real_parse = views.AnnotationConfigParser.parse_str

    def slow_parse(content: str, *args: Any, **kwargs: Any) -> Any:
        time.sleep(SLOW_BUILD_SECONDS)
        return real_parse(content, *args, **kwargs)

    mocker.patch.object(
        views.AnnotationConfigParser, "parse_str", staticmethod(slow_parse))
    return SLOW_BUILD_SECONDS


@pytest.fixture
def slow_body_parse(mocker: pytest_mock.MockerFixture) -> float:
    """Make the DRF request-body parse take ~``SLOW_BUILD_SECONDS``.

    Wraps ``Request._parse`` -- the call whose real cost is ~0.9 ms per MB of
    an unbounded anonymous body -- so the delay lands wherever the parse
    runs, on the loop or on a worker.
    """
    real_parse = Request._parse

    def slow_parse(self: Request) -> Any:
        time.sleep(SLOW_BUILD_SECONDS)
        return real_parse(self)

    mocker.patch.object(Request, "_parse", slow_parse)
    return SLOW_BUILD_SECONDS


def _record_parse_threads(mocker: pytest_mock.MockerFixture) -> list[str]:
    """Record the thread each ``parse_str`` call runs on."""
    threads: list[str] = []
    real_parse = views.AnnotationConfigParser.parse_str

    def recording_parse(content: str, *args: Any, **kwargs: Any) -> Any:
        threads.append(threading.current_thread().name)
        return real_parse(content, *args, **kwargs)

    mocker.patch.object(
        views.AnnotationConfigParser, "parse_str",
        staticmethod(recording_parse))
    return threads


async def _fire_validate(config: str = VALID_CONFIG) -> int:
    """POST one validation request; return its status code."""
    client = AsyncClient()
    response = await client.post(VALIDATE_URL, {"config": config})
    return response.status_code


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_slow_validate_builds_leave_the_sync_view_thread_free(
    slow_validate_build: float,
) -> None:
    """A cheap SYNC endpoint stays responsive under slow validation builds.

    ``GET /api/version`` is a synchronous DRF view, so it runs on the very
    thread the validation build used to occupy. Sampling it while N slow
    validations are in flight is the in-process form of the #164 load
    harness: if the build still ran on that thread, the samples would queue
    behind it and show latencies of about one build each.
    """
    latencies: list[float] = []
    cheap_statuses: list[int] = []
    stop = asyncio.Event()

    async def sample_cheap_endpoint() -> None:
        client = AsyncClient()
        while not stop.is_set():
            start = time.monotonic()
            response = await client.get(VERSION_URL)
            latencies.append(time.monotonic() - start)
            cheap_statuses.append(response.status_code)
            await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)

    sampler = asyncio.ensure_future(sample_cheap_endpoint())
    # Let the sampler record a baseline before the burst.
    await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS * 2)

    statuses = await asyncio.gather(*[_fire_validate() for _ in range(4)])
    stop.set()
    await sampler

    assert all(s == 200 for s in statuses), statuses
    assert all(s == 200 for s in cheap_statuses), cheap_statuses
    # The latency check comes before the sample-count guard so that a
    # regression reports the queueing it actually caused; a blocked sampler
    # also records fewer samples, and that guard would otherwise fire first
    # and misreport the cause.
    assert len(latencies) >= 2, (
        f"cheap endpoint was sampled only {len(latencies)} times -- "
        f"no latency to judge"
    )
    worst = max(latencies)
    assert worst < STALL_THRESHOLD_SECONDS, (
        f"cheap sync endpoint took {worst:.3f}s "
        f"(>= threshold {STALL_THRESHOLD_SECONDS:.3f}s, injected build "
        f"latency {slow_validate_build:.3f}s) -- the validation build is "
        f"still occupying the shared thread_sensitive thread"
    )
    assert len(latencies) >= 5, len(latencies)


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_concurrent_validate_builds_do_not_serialize(
    slow_validate_build: float,
) -> None:
    """Concurrent validations overlap instead of queueing on one thread.

    Nothing dedupes these builds -- each request builds its own pipeline --
    so on the shared single-threaded path N of them cost N x one build. Off
    it, they overlap on the bounded validation pool and cost about one.
    """
    start = time.monotonic()
    statuses = await asyncio.gather(*[_fire_validate() for _ in range(3)])
    elapsed = time.monotonic() - start

    assert all(s == 200 for s in statuses), statuses
    assert elapsed < 2 * slow_validate_build, (
        f"3 concurrent validations took {elapsed:.3f}s, about "
        f"{elapsed / slow_validate_build:.1f} builds' worth -- they are "
        f"still serializing on one thread"
    )


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_sync_thread_proof_is_discriminating(
    slow_validate_build: float,
    mocker: pytest_mock.MockerFixture,
) -> None:
    """Sabotage check: building on the shared sync thread FAILS the proof.

    Reproduces the pre-#659 placement exactly -- the build handed to
    ``sync_to_async`` with asgiref's default ``thread_sensitive=True``, i.e.
    the one thread every synchronous view shares. The cheap sync endpoint
    must then queue behind it and show a latency reaching
    ``SABOTAGED_STALL_FLOOR_SECONDS``, which is above
    ``STALL_THRESHOLD_SECONDS`` -- so the proof above would fail under this
    sabotage, and is therefore not vacuously green.
    """
    async def build_on_shared_sync_thread(
        self: PipelineValidation, content: str,
    ) -> None:
        # WRONG (deliberately): thread_sensitive=True is the shared sync-view
        # thread, which is what the async pool submission exists to avoid.
        await sync_to_async(self._build_pipeline)(content, self.grr)

    mocker.patch.object(
        PipelineValidation, "_abuild_pipeline", build_on_shared_sync_thread)

    latencies: list[float] = []
    stop = asyncio.Event()

    async def sample_cheap_endpoint() -> None:
        client = AsyncClient()
        while not stop.is_set():
            start = time.monotonic()
            await client.get(VERSION_URL)
            latencies.append(time.monotonic() - start)
            await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)

    sampler = asyncio.ensure_future(sample_cheap_endpoint())
    await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS * 2)
    status = await _fire_validate()
    stop.set()
    await sampler

    assert status == 200
    worst = max(latencies, default=0.0)
    assert worst >= SABOTAGED_STALL_FLOOR_SECONDS, (
        f"expected the cheap endpoint to queue for >= "
        f"{SABOTAGED_STALL_FLOOR_SECONDS:.3f}s behind a build on the shared "
        f"sync thread (injected build latency {slow_validate_build:.3f}s), "
        f"got {worst:.3f}s -- the sabotage may have stopped biting, which "
        f"would make the proof above vacuous"
    )


# ---------------------------------------------------------------------------
# ...and neither does the expansion-gate parse, which scans the repository
# ---------------------------------------------------------------------------
# The gate parse resolves no resource, but every wildcard it expands is a scan
# of the whole repository (``AnnotationConfigParser.query_resources`` iterates
# ``grr.get_all_resources()`` once per wildcard). Measured against the
# production-scale ENCODE GRR (7922 position scores): 27 ms for one wildcard,
# 1.59 s for a config at ``MAX_ANNOTATORS``. On a *sync* view that was a busy
# worker thread; on this async view "inline" means the event loop, where the
# same second and a half is the whole server going quiet -- and unlike a busy
# thread it cannot be preempted. So the gate keeps its place in the order (it
# still refuses before the build is submitted) but is itself awaited off the
# loop, on the same bounded pool.

def test_validation_pool_threads_carry_the_expected_name() -> None:
    """Anchor for the two thread-identity proofs below.

    They read a thread name and compare it to ``VALIDATE_THREAD_PREFIX``;
    that only means anything while the pool really names its workers so. A
    renamed pool must fail here, not silently make both proofs vacuous.
    """
    future = PipelineValidation.VALIDATE_EXECUTOR.execute(
        threading.current_thread)
    name = future.result(timeout=10).name

    assert name.startswith(VALIDATE_THREAD_PREFIX), name


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_expansion_gate_parse_runs_on_a_validation_worker(
    mocker: pytest_mock.MockerFixture,
) -> None:
    """The gate parse must execute on the bounded pool, not the loop thread.

    Thread identity rather than a stopwatch: this is the property, and it
    holds on a loaded host where a timing bound would wobble. The loop-stall
    proof below covers the consequence.
    """
    threads = _record_parse_threads(mocker)

    assert await _fire_validate() == 200
    assert threads, "the gate parse never ran"
    assert all(
        name.startswith(VALIDATE_THREAD_PREFIX) for name in threads
    ), threads


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_slow_expansion_parse_does_not_park_the_event_loop(
    slow_config_parse: float,
) -> None:
    """A slow gate parse must leave the event loop ticking.

    The consequence of the test above, in the idiom the sibling stall proofs
    use (#433/#454): a heartbeat coroutine ticks on the loop while a
    validation request waits on a parse of ``SLOW_BUILD_SECONDS``. A parse
    left on the loop parks it for that whole time -- as the sabotage test
    below demonstrates it would.
    """
    heartbeats: list[float] = []
    stop = asyncio.Event()

    async def heartbeat() -> None:
        while not stop.is_set():
            heartbeats.append(time.monotonic())
            await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)

    hb_task = asyncio.ensure_future(heartbeat())
    status = await _fire_validate()
    stop.set()
    await hb_task

    assert status == 200
    assert len(heartbeats) >= 2, (
        f"heartbeat coroutine barely ran ({len(heartbeats)} ticks) -- "
        f"no gap to measure"
    )
    worst_gap = max_gap(heartbeats)
    assert worst_gap < STALL_THRESHOLD_SECONDS, (
        f"event loop stalled for {worst_gap:.3f}s (>= threshold "
        f"{STALL_THRESHOLD_SECONDS:.3f}s, injected parse latency "
        f"{slow_config_parse:.3f}s) -- the expansion-gate parse ran ON the "
        f"loop"
    )
    assert len(heartbeats) >= 5, len(heartbeats)


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_loop_stall_proof_is_discriminating(
    slow_config_parse: float,
    mocker: pytest_mock.MockerFixture,
) -> None:
    """Sabotage check: parsing on the loop FAILS the proof above.

    Reproduces the placement the proof rules out -- the gate parse called
    straight from the handler coroutine, i.e. on the event loop -- and
    requires the loop to park for ``SABOTAGED_STALL_FLOOR_SECONDS``, which
    is above ``STALL_THRESHOLD_SECONDS``. Without this the proof could pass
    for want of anything slow to detect.
    """
    async def parse_on_the_event_loop(  # noqa: RUF029
        self: PipelineValidation, content: str,
    ) -> Any:
        # Awaits nothing on purpose -- that IS the sabotage. It must stay a
        # coroutine to stand in for the method it replaces.
        # WRONG (deliberately): a repository-scanning parse called inline
        # from the handler runs on the loop and cannot be preempted.
        return views.AnnotationConfigParser.parse_str(content, grr=self.grr)

    mocker.patch.object(
        PipelineValidation, "_aparse_config", parse_on_the_event_loop)

    heartbeats: list[float] = []
    stop = asyncio.Event()

    async def heartbeat() -> None:
        while not stop.is_set():
            heartbeats.append(time.monotonic())
            await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)

    hb_task = asyncio.ensure_future(heartbeat())
    status = await _fire_validate()
    stop.set()
    await hb_task

    assert status == 200
    worst_gap = max_gap(heartbeats)
    assert worst_gap >= SABOTAGED_STALL_FLOOR_SECONDS, (
        f"expected the loop to park for >= "
        f"{SABOTAGED_STALL_FLOOR_SECONDS:.3f}s with the gate parse called on "
        f"it (injected parse latency {slow_config_parse:.3f}s), got "
        f"{worst_gap:.3f}s -- the sabotage may have stopped biting, which "
        f"would make the proof above vacuous"
    )


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_the_build_also_runs_on_a_validation_worker(
    mocker: pytest_mock.MockerFixture,
) -> None:
    """The build's own thread identity, for the same reason as the parse."""
    threads: list[str] = []
    real_build = views.load_pipeline_from_yaml

    def recording_build(content: str, grr: Any, **kwargs: Any) -> Any:
        threads.append(threading.current_thread().name)
        return real_build(content, grr, **kwargs)

    mocker.patch(
        "web_annotation.pipelines.views.load_pipeline_from_yaml",
        recording_build)

    assert await _fire_validate() == 200
    assert threads, "the build never ran"
    assert all(
        name.startswith(VALIDATE_THREAD_PREFIX) for name in threads
    ), threads


# ---------------------------------------------------------------------------
# ...nor does the declared-annotator bound, whose yaml parse is not cheap
# ---------------------------------------------------------------------------
# The second bound counts what the config *declares*, which needs a
# ``yaml.safe_load`` of the whole (bounded) body. On the sync view that was a
# per-request ``thread_sensitive`` worker thread, where pure-Python yaml
# releases the GIL every switch interval and the rest of the process kept
# running. On this async view "inline" is the event loop, and the parse is not
# cheap at the size the *first* bound permits: measured in this worktree with
# the repo's venv, worst of several legal 64 KiB shapes,
#
#   ``- [1,2,3,4,5]\n`` x 4681        (65 534 chars)   555 ms
#   ``annotators: [{a: 1, b: 2}, ...]`` (62 413 chars)  473 ms
#   an alias-heavy mapping             (65 536 chars)  208 ms
#
# against 6 ms for a config at ``MAX_ANNOTATORS`` written the way a human
# writes one. Half a second of un-preemptible loop time, for a request that is
# then *refused*, at 120/min per IP (the ``pipeline_validate`` scope) is one
# anonymous client saturating the loop -- so the count goes to the same
# bounded pool. It still refuses before the expansion parse and the build.

@pytest.fixture
def slow_annotator_count(mocker: pytest_mock.MockerFixture) -> float:
    """Make the declared-annotator count take ~``SLOW_BUILD_SECONDS``.

    Wraps the module-level ``_count_annotators`` -- the point whose real cost
    is a ``yaml.safe_load`` of up to ``MAX_CONFIG_LENGTH`` -- so the delay
    lands wherever the count runs, on the loop or on a worker.
    """
    real_count = views._count_annotators

    def slow_count(content: str) -> int | None:
        time.sleep(SLOW_BUILD_SECONDS)
        return real_count(content)

    mocker.patch(
        "web_annotation.pipelines.views._count_annotators", slow_count)
    return SLOW_BUILD_SECONDS


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_slow_annotator_count_does_not_park_the_event_loop(
    slow_annotator_count: float,
) -> None:
    """A slow declared-annotator count must leave the event loop ticking.

    Same idiom as the expansion-gate proof above (#433/#454): a heartbeat
    coroutine ticks while one validation request waits on a count of
    ``SLOW_BUILD_SECONDS``. A count left inline in the handler coroutine
    parks the loop for that whole time.
    """
    heartbeats: list[float] = []
    stop = asyncio.Event()

    async def heartbeat() -> None:
        while not stop.is_set():
            heartbeats.append(time.monotonic())
            await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)

    hb_task = asyncio.ensure_future(heartbeat())
    status = await _fire_validate()
    stop.set()
    await hb_task

    assert status == 200
    assert len(heartbeats) >= 2, (
        f"heartbeat coroutine barely ran ({len(heartbeats)} ticks) -- "
        f"no gap to measure"
    )
    worst_gap = max_gap(heartbeats)
    assert worst_gap < STALL_THRESHOLD_SECONDS, (
        f"event loop stalled for {worst_gap:.3f}s (>= threshold "
        f"{STALL_THRESHOLD_SECONDS:.3f}s, injected count latency "
        f"{slow_annotator_count:.3f}s) -- the declared-annotator count ran "
        f"ON the loop"
    )
    assert len(heartbeats) >= 5, len(heartbeats)


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_annotator_count_runs_on_a_validation_worker(
    mocker: pytest_mock.MockerFixture,
) -> None:
    """The declared-annotator count must execute on the bounded pool.

    Thread identity rather than a stopwatch, for the same reason as the
    expansion-gate proof: it is the property, and it holds on a loaded host
    where a timing bound would wobble.
    """
    threads: list[str] = []
    real_count = views._count_annotators

    def recording_count(content: str) -> int | None:
        threads.append(threading.current_thread().name)
        return real_count(content)

    mocker.patch(
        "web_annotation.pipelines.views._count_annotators", recording_count)

    assert await _fire_validate() == 200
    assert threads, "the declared-annotator count never ran"
    assert all(
        name.startswith(VALIDATE_THREAD_PREFIX) for name in threads
    ), threads


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_annotator_count_stall_proof_is_discriminating(
    slow_annotator_count: float,
    mocker: pytest_mock.MockerFixture,
) -> None:
    """Sabotage check: counting on the loop FAILS the proof above.

    Reproduces the placement the proof rules out -- the count called
    straight from the handler coroutine, i.e. on the event loop, which is
    where it sat between the async conversion and this fix -- and requires
    the loop to park for ``SABOTAGED_STALL_FLOOR_SECONDS``, above
    ``STALL_THRESHOLD_SECONDS``. Without this the proof could pass for want
    of anything slow to detect.
    """
    async def count_on_the_event_loop(  # noqa: RUF029
        self: PipelineValidation, content: str,
    ) -> int | None:
        # Awaits nothing on purpose -- that IS the sabotage. It must stay a
        # coroutine to stand in for the method it replaces.
        # WRONG (deliberately): a yaml.safe_load of an up-to-64 KiB body
        # called inline from the handler runs on the loop and cannot be
        # preempted.
        return views._count_annotators(content)

    mocker.patch.object(
        PipelineValidation, "_acount_annotators", count_on_the_event_loop)

    heartbeats: list[float] = []
    stop = asyncio.Event()

    async def heartbeat() -> None:
        while not stop.is_set():
            heartbeats.append(time.monotonic())
            await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)

    hb_task = asyncio.ensure_future(heartbeat())
    status = await _fire_validate()
    stop.set()
    await hb_task

    assert status == 200
    worst_gap = max_gap(heartbeats)
    assert worst_gap >= SABOTAGED_STALL_FLOOR_SECONDS, (
        f"expected the loop to park for >= "
        f"{SABOTAGED_STALL_FLOOR_SECONDS:.3f}s with the declared-annotator "
        f"count called on it (injected count latency "
        f"{slow_annotator_count:.3f}s), got {worst_gap:.3f}s -- the sabotage "
        f"may have stopped biting, which would make the proof above vacuous"
    )


# ---------------------------------------------------------------------------
# ...and neither does reading the request body, which is the widest untrusted
# input on the whole API
# ---------------------------------------------------------------------------
# Everything above bounds work that happens *after* the config string exists.
# Producing that string is itself a parse of an anonymous, unauthenticated
# body, and nothing on this path caps its size: Django's
# ``DATA_UPLOAD_MAX_MEMORY_SIZE`` is consulted only by ``HttpRequest.body`` /
# ``.POST``, neither of which DRF uses, and for multipart Django bounds only
# non-file field bytes and the file *count* -- never a file part's size.
# Django's ASGI handler has already buffered the whole body before the view
# runs, so the parse proceeds at disk speed rather than at the client's upload
# speed: it lands as one contiguous, un-preemptible burst. Measured through
# this endpoint: ~0.9 ms per MB, i.e. ~1 s per GB of body. On the shared
# sync-view thread that was a busy thread; on the event loop it is the whole
# process -- every other HTTP request, every websocket frame -- going quiet.
# So ``request.data`` is awaited off the loop like every other long pole here.

def _record_body_parse_threads(mocker: pytest_mock.MockerFixture) -> list[str]:
    """Record the thread each DRF request-body parse runs on.

    Patched at ``Request._parse`` rather than at a concrete parser class so
    the proof covers whichever parser content negotiation selects -- JSON and
    multipart are both reachable here, and both are unbounded.
    """
    threads: list[str] = []
    real_parse = Request._parse

    def recording_parse(self: Request) -> Any:
        threads.append(threading.current_thread().name)
        return real_parse(self)

    mocker.patch.object(Request, "_parse", recording_parse)
    return threads


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_request_body_parse_runs_on_a_validation_worker(
    mocker: pytest_mock.MockerFixture,
) -> None:
    """The body parse must execute on the bounded pool, not the loop thread.

    Thread identity rather than a stopwatch, for the same reason as the
    sibling proofs. The bounded pool rather than the shared
    ``thread_sensitive`` thread for the reason this issue exists: the parse
    is unauthenticated and unbounded, so the shared sync-view thread is the
    one place it must not go either.
    """
    threads = _record_body_parse_threads(mocker)

    assert await _fire_validate() == 200
    assert threads, "the request body was never parsed"
    assert all(
        name.startswith(VALIDATE_THREAD_PREFIX) for name in threads
    ), threads


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_slow_body_parse_does_not_park_the_event_loop(
    slow_body_parse: float,
) -> None:
    """A slow body parse must leave the event loop ticking.

    The consequence of the test above, in the stall idiom (#433/#454): a
    heartbeat coroutine ticks while one validation request waits on a parse
    of ``SLOW_BUILD_SECONDS``, standing in for the ~1 s per GB a real
    oversized body costs.
    """
    heartbeats: list[float] = []
    stop = asyncio.Event()

    async def heartbeat() -> None:
        while not stop.is_set():
            heartbeats.append(time.monotonic())
            await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)

    hb_task = asyncio.ensure_future(heartbeat())
    status = await _fire_validate()
    stop.set()
    await hb_task

    assert status == 200
    assert len(heartbeats) >= 2, (
        f"heartbeat coroutine barely ran ({len(heartbeats)} ticks) -- "
        f"no gap to measure"
    )
    worst_gap = max_gap(heartbeats)
    assert worst_gap < STALL_THRESHOLD_SECONDS, (
        f"event loop stalled for {worst_gap:.3f}s (>= threshold "
        f"{STALL_THRESHOLD_SECONDS:.3f}s, injected parse latency "
        f"{slow_body_parse:.3f}s) -- the request body was parsed ON the loop"
    )
    assert len(heartbeats) >= 5, len(heartbeats)


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_body_parse_stall_proof_is_discriminating(
    slow_body_parse: float,
    mocker: pytest_mock.MockerFixture,
) -> None:
    """Sabotage check: parsing the body on the loop FAILS the proof above.

    Reproduces the placement the proof rules out -- ``request.data`` touched
    straight from the handler coroutine, which is where it sat between the
    async conversion and this fix -- and requires the loop to park for
    ``SABOTAGED_STALL_FLOOR_SECONDS``, above ``STALL_THRESHOLD_SECONDS``.
    """
    async def read_body_on_the_event_loop(  # noqa: RUF029
        self: PipelineValidation,
        request: Request,
    ) -> Any:
        # Awaits nothing on purpose -- that IS the sabotage. It must stay a
        # coroutine to stand in for the method it replaces.
        # WRONG (deliberately): touching request.data inline from the handler
        # runs an unbounded parse of an anonymous body on the loop.
        return request.data

    mocker.patch.object(
        PipelineValidation, "_aread_body", read_body_on_the_event_loop)

    heartbeats: list[float] = []
    stop = asyncio.Event()

    async def heartbeat() -> None:
        while not stop.is_set():
            heartbeats.append(time.monotonic())
            await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)

    hb_task = asyncio.ensure_future(heartbeat())
    status = await _fire_validate()
    stop.set()
    await hb_task

    assert status == 200
    worst_gap = max_gap(heartbeats)
    assert worst_gap >= SABOTAGED_STALL_FLOOR_SECONDS, (
        f"expected the loop to park for >= "
        f"{SABOTAGED_STALL_FLOOR_SECONDS:.3f}s with the body parsed on it "
        f"(injected parse latency {slow_body_parse:.3f}s), got "
        f"{worst_gap:.3f}s -- the sabotage may have stopped biting, which "
        f"would make the proof above vacuous"
    )


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_a_malformed_json_body_is_still_a_400() -> None:
    """A parse error raised on a worker still renders as DRF's 400.

    ``ParseError`` used to be raised inside the handler coroutine, where
    DRF's exception handler caught it directly. Awaiting the parse re-raises
    it through the future, from the ``await`` -- still inside the handler, so
    the response is unchanged.
    """
    client = AsyncClient()
    response = await client.post(
        VALIDATE_URL, "{not json", content_type="application/json")

    assert response.status_code == 400, response.content
    assert "detail" in response.json(), response.json()


# ---------------------------------------------------------------------------
# The load-test build-delay hook, on THIS endpoint's direct-build path
# ---------------------------------------------------------------------------
# The #164 hook lives in the pipeline cache's loader, and this endpoint never
# goes through the cache -- so before #659 the harness had no way to induce a
# slow *validation* build. The build helper reads the same environment
# variable, so the harness can dial contention here the way it does for the
# cached paths.

# Half the injected delay, and ~50x the measured cost of a warm validation
# request against the fixture GRR (~0.02s) -- comfortably between the two, so
# neither a slow host nor a fast one can flip the verdict.
NO_DELAY_BOUND_SECONDS = SLOW_BUILD_SECONDS / 2


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_validate_build_honours_the_injected_build_delay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``GPFWA_BUILD_DELAY_SECONDS`` slows THIS endpoint's build."""
    monkeypatch.setenv("GPFWA_BUILD_DELAY_SECONDS", str(SLOW_BUILD_SECONDS))
    client = AsyncClient()

    start = time.monotonic()
    response = await client.post(VALIDATE_URL, {"config": VALID_CONFIG})
    elapsed = time.monotonic() - start

    assert response.status_code == 200, response.content
    assert response.json() == {"errors": ""}
    assert elapsed >= SLOW_BUILD_SECONDS, (
        f"validate answered in {elapsed:.3f}s with a "
        f"{SLOW_BUILD_SECONDS:.3f}s build delay injected -- the hook did not "
        f"reach this endpoint's build path"
    )


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_validate_build_delay_is_a_no_op_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With the variable unset the hook costs nothing -- safe in production."""
    monkeypatch.delenv("GPFWA_BUILD_DELAY_SECONDS", raising=False)
    client = AsyncClient()
    # Warm the first-request cost (imports, GRR/table open) out of the
    # measurement; it is not what this test is about.
    warmup = await client.post(VALIDATE_URL, {"config": VALID_CONFIG})
    assert warmup.status_code == 200, warmup.content

    start = time.monotonic()
    response = await client.post(VALIDATE_URL, {"config": VALID_CONFIG})
    elapsed = time.monotonic() - start

    assert response.status_code == 200, response.content
    assert response.json() == {"errors": ""}
    assert elapsed < NO_DELAY_BOUND_SECONDS, (
        f"validate took {elapsed:.3f}s with no delay configured -- the hook "
        f"is not a no-op when GPFWA_BUILD_DELAY_SECONDS is unset"
    )


# ---------------------------------------------------------------------------
# The untrusted body must never be written to the log
# ---------------------------------------------------------------------------
# Awaiting the body parse on the pool means an executor now *carries the parsed
# body as a task result*, and the pool's done-callback used to render every
# result into a DEBUG record. The shipped ``LOGGING`` puts the root logger at
# DEBUG behind a console handler and a file handler, so on this anonymous,
# size-unbounded endpoint that turned every refused request into a write of
# the whole attacker-supplied body -- to disk and to stdout -- *before* the
# ``MAX_CONFIG_LENGTH`` refusal ran. The pool must describe what it finished,
# never render what it produced.

#: Distinctive enough that finding it in a log record cannot be a coincidence.
BODY_CANARY = "cAnArY-payload-that-must-not-be-logged"


def _canary_records(caplog: pytest.LogCaptureFixture) -> list[str]:
    """Every captured log message that carries the canary."""
    return [
        record.getMessage()
        for record in caplog.records
        if BODY_CANARY in record.getMessage()
    ]


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_a_refused_oversized_body_is_never_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The body of a 400-refused request must not reach any log record.

    This is the amplification that matters: the request is *rejected*, so a
    client paying nothing but bandwidth would otherwise buy a log write the
    size of whatever it sent, at the endpoint's full throttle allowance.
    """
    oversized = BODY_CANARY + "#" * PipelineValidation.MAX_CONFIG_LENGTH
    client = AsyncClient()

    with caplog.at_level(logging.DEBUG):
        response = await client.post(VALIDATE_URL, {"config": oversized})

    assert response.status_code == 400, response.content
    assert _canary_records(caplog) == []


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_an_accepted_body_is_never_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Nor does a config that passes every bound get rendered into the log.

    The editor posts on a debounce, so even benign traffic would otherwise
    cost a multiple of the config's size in log volume per keystroke.
    """
    client = AsyncClient()

    with caplog.at_level(logging.DEBUG):
        response = await client.post(
            VALIDATE_URL,
            {"config": f"# {BODY_CANARY}\n{VALID_CONFIG}"},
        )

    assert response.status_code == 200, response.content
    assert response.json() == {"errors": ""}
    assert _canary_records(caplog) == []
