# pylint: disable=W0621,C0114,C0116,W0212,W0613
import asyncio
import time

import pytest
from channels.layers import get_channel_layer
from django.test import AsyncClient
from pytest_mock import MockerFixture

from web_annotation.consumers import AnnotationStateConsumer
from web_annotation.models import User
from web_annotation.pipeline_cache import LRUPipelineCache
from web_annotation.single_allele_annotation.views import SingleAnnotation
from web_annotation.testing import CustomWebsocketCommunicator

ANNOTATE_URL = "/api/single_allele/annotate"
PIPELINE_ID = "t4c8/t4c8_pipeline"

# The injected build parks the loader thread this long (off-loop). If a
# regression ran the build wait ON the event loop instead, the loop would
# freeze for ~this long. Kept well above host noise so a real park is
# unmistakable.
SLOW_BUILD_S = 2.0
# Event-loop lag at/above this is a parked loop, never host noise: clean lag
# is sub-millisecond and even a loaded CI runner keeps it in the low tens of
# ms, an order of magnitude under this bound, while a leaked build wait parks
# for ~SLOW_BUILD_S. A single lag sample this large is therefore a real leak.
PARKED_LOOP_BOUND_S = 1.0
LAG_SAMPLE_INTERVAL_S = 0.02


def loop_parked(lags: list[float], bound: float) -> bool:
    """Whether event-loop lag ever reached the parked-loop magnitude.

    ``lags`` comes from ``_sample_loop_lag``, which measures CONTINUOUSLY --
    each sleep both paces and measures, with no unmeasured gap -- so a genuine
    on-loop park is captured in some sample no matter when it starts. (Sampling
    only inside an emit->receipt window, as an earlier version did, missed a
    park that landed in the gap between samples: the false-green that let a real
    leak pass.)

    Host noise keeps lag in the millisecond range; only a parked loop (a leaked
    ~``SLOW_BUILD_S`` build wait) reaches ``bound``. So one sample at/above
    ``bound`` is a real leak -- zero tolerance. The magnitude gap (ms noise vs a
    ~2 s park) is what makes this both flake-proof and leak-sensitive.
    """
    return max(lags, default=0.0) >= bound


async def _sample_loop_lag(
    stop_event: asyncio.Event, interval: float = LAG_SAMPLE_INTERVAL_S,
) -> list[float]:
    """Continuously measure event-loop lag until ``stop_event`` is set.

    Each iteration times how much longer than ``interval`` its own
    ``asyncio.sleep`` actually took; that excess is how long the loop was
    unavailable. Because the sleep both paces and measures, every moment is
    inside some measured window -- a loop park is always charged to a sample,
    regardless of phase.
    """
    loop = asyncio.get_running_loop()
    lags: list[float] = []
    while not stop_event.is_set():
        started = loop.time()
        await asyncio.sleep(interval)
        lags.append(loop.time() - started - interval)
    return lags


@pytest.fixture
def slow_build(mocker: MockerFixture) -> float:
    """Make pipeline builds take ~SLOW_BUILD_S seconds on the loader thread.

    The sleep runs off-loop (inside the loader thread), so the event loop
    stays free while the build is in progress.
    """
    slow_seconds = SLOW_BUILD_S
    real_load = LRUPipelineCache._load_pipeline_raw

    def slow_load(raw, grr, pipeline_id="unknown"):  # type: ignore
        time.sleep(slow_seconds)
        return real_load(raw, grr, pipeline_id)

    mocker.patch.object(
        LRUPipelineCache, "_load_pipeline_raw", staticmethod(slow_load),
    )
    return slow_seconds


async def _connect_global_ws(user: User) -> CustomWebsocketCommunicator:
    communicator = CustomWebsocketCommunicator(
        AnnotationStateConsumer.as_asgi(), "/ws/notifications/", user=user)
    connected, _ = await communicator.connect(timeout=5)
    assert connected
    return communicator


async def _recv_ping(
    communicator: CustomWebsocketCommunicator, seq: int, timeout: int = 5,
) -> None:
    """Receive frames until the sentinel for ``seq`` arrives (ignore others)."""
    sentinel = f"loadtest_ping:{seq}"
    while True:
        frame = await communicator.receive_json_from(timeout=timeout)
        if frame.get("message") == sentinel:
            return


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_event_loop_not_parked_during_slow_build(
    slow_build: float,
) -> None:
    """A slow build must not park the event loop, so WS pushes stay responsive.

    The build sleeps on a loader thread (off-loop), so the loop must stay free.
    A continuous lag monitor runs alongside; if an async view leaked the build
    wait onto the loop it would freeze for ~SLOW_BUILD_S and some lag sample
    would reach PARKED_LOOP_BOUND_S. We also confirm a WS notification is still
    delivered end-to-end. (The four requests share one deduped build future --
    they exercise the concurrent-reader path but produce a single build.)
    """
    SingleAnnotation.lru_cache.unload_pipeline(PIPELINE_ID)
    user = await User.objects.acreate_user(
        "ws-load", "ws-load@example.com", "secret")
    communicator = await _connect_global_ws(user)
    channel_layer = get_channel_layer()
    assert channel_layer is not None

    async def fire_request() -> int:
        client = AsyncClient()
        response = await client.post(
            ANNOTATE_URL,
            {"annotatable": {"chrom": "chr1", "pos": "3"},
             "pipeline_id": PIPELINE_ID},
            content_type="application/json",
        )
        return response.status_code

    stop = asyncio.Event()
    monitor = asyncio.ensure_future(_sample_loop_lag(stop))
    builds = [asyncio.ensure_future(fire_request()) for _ in range(4)]

    statuses = await asyncio.gather(*builds)

    # The loop stayed free -> a fresh notification is delivered promptly.
    await channel_layer.group_send(
        "global",
        {"type": "annotation_notify", "message": "loadtest_ping:0"},
    )
    await _recv_ping(communicator, 0)

    stop.set()
    lags = await monitor
    await communicator.disconnect(timeout=5)

    assert all(s == 200 for s in statuses), statuses
    assert len(lags) >= 5, lags
    assert not loop_parked(lags, PARKED_LOOP_BOUND_S), (
        f"event-loop lag peaked at {max(lags):.3f}s "
        f"(>= {PARKED_LOOP_BOUND_S}s) -- an async view parked the loop during "
        f"the slow build"
    )


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_loop_lag_monitor_detects_on_loop_block() -> None:
    """Positive control: the SAME lag monitor the no-park test relies on must
    register a real on-loop block.

    Without this, a "not parked" pass could merely mean the monitor is blind.
    We park the loop synchronously; the monitor must measure ~the park and
    breach PARKED_LOOP_BOUND_S -- exercising the exact detector, not a proxy.
    """
    stop = asyncio.Event()
    monitor = asyncio.ensure_future(_sample_loop_lag(stop))
    await asyncio.sleep(0.05)  # let the monitor take clean samples first

    block = 1.5
    time.sleep(block)  # noqa: ASYNC251 -- intentional loop park for the test
    await asyncio.sleep(0.05)  # let the monitor observe the park

    stop.set()
    lags = await monitor
    worst = max(lags)

    assert worst >= block * 0.8, (
        f"lag monitor measured {worst:.3f}s for a {block}s loop park -- it is "
        f"NOT sensitive to on-loop blocking, so a null result is untrustworthy"
    )
    assert worst >= PARKED_LOOP_BOUND_S, (
        f"a real loop park ({worst:.3f}s) must breach the parked-loop bound "
        f"{PARKED_LOOP_BOUND_S}s"
    )


@pytest.mark.parametrize(
    ("lags", "expected_parked", "reason"),
    [
        # Clean loop: sub-millisecond lag throughout.
        ([0.0002] * 100, False, "clean loop"),
        # The observed flake magnitude (a lone ~0.16 s host spike) is far below
        # the parked-loop bound -> not a park. This is what made the old
        # max()<0.15 check flake; here it is comfortably clean.
        ([0.0002] * 99 + [0.16], False, "lone host spike"),
        # Even a larger isolated host pause stays below the bound.
        ([0.0002] * 99 + [0.6], False, "larger isolated host pause"),
        # Just under the bound is still not a park.
        ([0.0002] * 99 + [PARKED_LOOP_BOUND_S - 0.01], False, "just under"),
        # At the bound: a parked loop, zero tolerance even as a lone sample --
        # this is exactly the single deduped-build leak signature.
        ([0.0002] * 99 + [PARKED_LOOP_BOUND_S], True, "at bound = parked"),
        # A full leaked build wait.
        ([0.0002] * 99 + [SLOW_BUILD_S], True, "full build-wait park"),
    ],
)
def test_loop_parked_flags_park_not_host_noise(
    lags: list[float], *, expected_parked: bool, reason: str,
) -> None:
    """Lock in the detector: host-noise-magnitude lag passes; a lone
    parked-loop-magnitude sample fails.

    Deterministic guard for the flake fix (first seen on gain/master build
    #549) and for single-build leak detection -- exercises ``loop_parked``
    directly, with no timing or host load, so neither the wide-margin
    flake-tolerance nor the zero-tolerance park detection can silently regress.
    """
    assert loop_parked(lags, PARKED_LOOP_BOUND_S) is expected_parked, reason
