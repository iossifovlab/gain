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
# Clean-loop push round-trips are single-digit ms; the slow build is 0.4 s.
# 0.15 s sits well above clean-loop noise and well below a parked-loop spike.
LATENCY_BOUND_S = 0.15


@pytest.fixture
def slow_build(mocker: MockerFixture) -> float:
    """Make pipeline builds take ~SLOW seconds on the loader thread.

    The sleep runs off-loop (inside the loader thread), so the event loop
    stays free while the build is in progress.
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
async def test_ws_push_latency_bounded_under_concurrent_builds(
    slow_build: float,
) -> None:
    """WS pushes stay fast while concurrent slow builds run -> loop not parked.

    The builds sleep on loader threads (off-loop), so the event loop keeps
    delivering notifications promptly. If an async view leaked blocking work
    onto the loop, the sampled push latency would breach LATENCY_BOUND_S.
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

    builds = [asyncio.ensure_future(fire_request()) for _ in range(4)]

    latencies: list[float] = []
    seq = 0
    while not all(b.done() for b in builds):
        t0 = time.monotonic()
        await channel_layer.group_send(
            "global",
            {"type": "annotation_notify", "message": f"loadtest_ping:{seq}"},
        )
        await _recv_ping(communicator, seq)
        latencies.append(time.monotonic() - t0)
        seq += 1
        await asyncio.sleep(0.02)

    statuses = await asyncio.gather(*builds)
    await communicator.disconnect(timeout=5)

    assert all(s == 200 for s in statuses), statuses
    assert len(latencies) >= 5, latencies
    worst = max(latencies)
    assert worst < LATENCY_BOUND_S, (
        f"WS push latency {worst:.3f}s >= bound {LATENCY_BOUND_S}s -- "
        f"an async view parked the event loop under build load"
    )


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_ws_push_latency_detects_on_loop_block() -> None:
    """Positive control: a loop park between emit and receipt IS measured.

    Proves the sampler is sensitive -- without this, a "no leak" pass could
    merely mean the harness is blind. We park the loop for ``block`` seconds
    synchronously between group_send and receipt; the measured latency must
    reflect (at least most of) the park.
    """
    user = await User.objects.acreate_user(
        "ws-block", "ws-block@example.com", "secret")
    communicator = await _connect_global_ws(user)
    channel_layer = get_channel_layer()
    assert channel_layer is not None

    block = 0.4
    t0 = time.monotonic()
    await channel_layer.group_send(
        "global",
        {"type": "annotation_notify", "message": "loadtest_ping:0"},
    )
    time.sleep(block)  # noqa: ASYNC251 -- intentional loop park for the test
    await _recv_ping(communicator, 0)
    latency = time.monotonic() - t0
    await communicator.disconnect(timeout=5)

    assert latency >= block * 0.8, (
        f"sampler measured {latency:.3f}s for a {block}s loop park -- "
        f"it is NOT sensitive to on-loop blocking, "
        f"so a null result is untrustworthy"
    )
    assert latency >= LATENCY_BOUND_S, (
        f"a real loop park ({latency:.3f}s) must breach the no-leak bound "
        f"{LATENCY_BOUND_S}s"
    )
