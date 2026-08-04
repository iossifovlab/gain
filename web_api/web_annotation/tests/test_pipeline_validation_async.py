# pylint: disable=W0621,C0114,C0116,W0212,W0613
import asyncio
import time
from typing import Any

import pytest
import pytest_mock
from asgiref.sync import sync_to_async
from django.test import AsyncClient

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
