# WS Notification Consumer Responsiveness SLO (iossifovlab/gain#170) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Measure WebSocket notification-delivery latency under concurrent slow-build load, prove (with a CI-durable, sensitivity-checked regression test) that no async-view code path leaks blocking work onto the event loop, and record the numbers + verdict in `docs/`.

**Architecture:** The notifications consumer (`AnnotationStateConsumer`) is a **sync** `WebsocketConsumer` — its handlers run on `thread_sensitive` threads, not the event loop — so it is *not* itself a loop-blocker. The genuinely loop-shared surface is the **async views** (#163 `SingleAnnotation.post` + #165/#166/#167 read views) and the channel-layer `group_send` machinery. We therefore measure **WS push latency** (`emit → client receipt` of a cheap sentinel notification) while a sustained stream of async-view requests churns the loop: if any async view leaks blocking work, the sampled WS latency spikes; if the loop stays clean, it stays flat. A test-only sync ping endpoint (gated under `settings_e2e`) re-uses the consumer's existing `annotation_notify` handler to inject sentinel frames — **no production consumer change**.

**Tech Stack:** Python 3.12, Django 5.2 ASGI + Daphne, Django Channels (`InMemoryChannelLayer`), adrf async views, `aiohttp` (client, already in venv — **no new dependency**), `asyncio`, pytest + `pytest-asyncio` + `pytest-mock`.

## Global Constraints

- **No new runtime dependency.** `aiohttp` is already in the gain venv; do not add `websockets`, `httpx`, or `locust`. (Matches the #164 harness constraint.)
- **No production behavior change.** The ping endpoint must be gated so it can never appear in a production URLconf; the consumer must not be modified. The positive-control loop-block must live in the **test**, not in committed production code (no `GPFWA_LOOP_BLOCK_SECONDS` env knob).
- **Reuse #164 infrastructure verbatim where possible:** `web_annotation/loadtest/run_daphne_server.sh` (already uses `settings_e2e`), `GPFWA_BUILD_DELAY_SECONDS` build-delay injection, the unlimited-user login flow, the `_percentile`/`CheapSamples` reporting idiom.
- **Channel layer is `InMemoryChannelLayer` (per-process).** The ping producer MUST run **inside the daphne process** (it does — it is an HTTP endpoint served by daphne). A separate `manage.py` process would land in a different layer instance and reach no consumers.
- **Latency clocking = client-side round-trip stamping.** The aiohttp harness stamps both `t_send` (when it POSTs the ping) and `t_recv` (when the WS frame arrives) **in the same client process** — no cross-process clock comparison. Correlate ping↔frame by an integer `seq`.
- **Lint before every commit** (per `gain/CLAUDE.md`): `ruff check --fix .` **and** `mypy gain ...` **and** `pylint --rcfile=pylintrc gain` for touched Python; `shellcheck` parity is not enforced but keep shell `set -euo pipefail`. Run from `gain/` (repo root of the submodule). The harness module lives under `web_api/web_annotation/` so it is linted as part of the `web_api` package.
- **Tests run with `PYTHONHASHSEED=0`** and from `web_api/` via `pytest`. The settings module is fixed by `pytest.ini` (`DJANGO_SETTINGS_MODULE = web_annotation.test_settings`) — do **not** pass `DJANGO_SETTINGS_MODULE` on the command line. Because `test_settings` does **not** set `LOADTEST_PING_ENABLED`, the ping *route* is absent during pytest (it is import-time gated in `urls.py`); unit tests therefore call the ping *view callable directly* and assert the flag on the `settings_e2e` module object. The live route is exercised only when the daphne harness runs under `settings_e2e` (Task 4).

---

## File Structure

| Path | Responsibility | Task |
|---|---|---|
| `web_api/web_annotation/loadtest/ping_view.py` | **Create.** Test-only sync Django view: relays a sentinel `loadtest_ping:<seq>` frame to the `"global"` group via `group_send`, reusing the consumer's `annotation_notify` handler. | 1 |
| `web_api/web_annotation/settings_e2e.py` | **Modify.** Add `LOADTEST_PING_ENABLED = True` flag. | 1 |
| `web_api/web_annotation/urls.py` | **Modify.** Conditionally append the ping route when `settings.LOADTEST_PING_ENABLED`. | 1 |
| `web_api/web_annotation/tests/test_ping_view.py` | **Create.** Unit test: the view calls `group_send` with the sentinel payload and returns 200; `settings_e2e` enables the flag. | 1 |
| `web_api/web_annotation/tests/test_ws_notification_responsiveness.py` | **Create.** CI-durable regression test: WS push latency stays bounded under concurrent slow builds (no-leak) + a deterministic on-loop-block positive control proving the sampler detects loop parking. | 2 |
| `web_api/web_annotation/loadtest/ws_notification_slo.py` | **Create.** Daphne+aiohttp harness: sustained K-in-flight annotate load + continuous WS-ping sampler; reports WS p50/p95/p99 split into baseline/under-load, as JSON. | 3 |
| `web_api/web_annotation/loadtest/run_ws_matrix.sh` | **Create.** K-sweep driver (fresh cold daphne per K) for the WS harness, mirroring `run_matrix.sh`. | 3 |
| `web_api/docs/170-ws-notification-responsiveness-slo.md` | **Create.** Methodology + measured numbers + on-loop-call static audit of the #165–#167 async views + go/no-go verdict feeding back into #165/#166/#167. | 4 |

**Interface contract reused from existing code (do not re-implement):**

- `AnnotationStateConsumer.annotation_notify(self, event)` (`web_annotation/consumers.py:141-144`) already does `self.send(json.dumps({"message": event["message"]}))`. A `group_send` with `{"type": "annotation_notify", "message": <str>}` delivers `{"message": <str>}` to every socket in the group. **This is the ping vehicle — no consumer change.**
- The consumer joins the `"global"` group on `connect` (`consumers.py:31-32`), so a `group_send("global", ...)` reaches every connected client.
- `slow_build` fixture pattern (`tests/test_single_annotation_async.py:203-216`): patches `LRUPipelineCache._load_pipeline_raw` to `time.sleep` on the loader thread.
- `CustomWebsocketCommunicator(application, path, *, user=...)` (`web_annotation/testing.py:8-32`): `.connect()`, `.receive_json_from(timeout)`, `.disconnect()`.
- Build-delay env knob `GPFWA_BUILD_DELAY_SECONDS` read in `web_annotation/pipeline_cache.py` (default `0.0` no-op).

---

### Task 1: Test-only sync ping endpoint (gated under `settings_e2e`)

**Files:**
- Create: `web_api/web_annotation/loadtest/ping_view.py`
- Modify: `web_api/web_annotation/settings_e2e.py` (add flag)
- Modify: `web_api/web_annotation/urls.py:71-72` (conditional route append, after the `admin_panel` block)
- Test: `web_api/web_annotation/tests/test_ping_view.py`

**Interfaces:**
- Consumes: `channels.layers.get_channel_layer()`, the consumer's existing `annotation_notify` handler.
- Produces: HTTP route `POST|GET /api/_loadtest/ping?seq=<int>` → `HttpResponse("ok", 200)` and a side-effect `group_send("global", {"type": "annotation_notify", "message": "loadtest_ping:<seq>"})`. The harness (Task 3) and reader correlate on the `loadtest_ping:<seq>` sentinel.

- [ ] **Step 1: Write the failing test**

Create `web_api/web_annotation/tests/test_ping_view.py`:

```python
# pylint: disable=C0114,C0116
from unittest.mock import MagicMock

from web_annotation import settings_e2e
from web_annotation.loadtest import ping_view


def test_settings_e2e_enables_loadtest_ping() -> None:
    # The flag gates the route into the URLconf only under settings_e2e (the
    # module run_daphne_server.sh uses). pytest runs under test_settings, which
    # does NOT set it, so assert on the settings_e2e module object directly.
    assert settings_e2e.LOADTEST_PING_ENABLED is True


def test_ping_view_group_sends_sentinel(mocker) -> None:
    # The route is import-time gated and absent under test_settings, so call the
    # view callable directly rather than going through a URL/Client.
    captured: dict = {}

    def fake_async_to_sync(func):  # mimic asgiref.sync.async_to_sync(group_send)
        def _call(group, message):
            captured["group"] = group
            captured["message"] = message
        return _call

    mocker.patch.object(ping_view, "get_channel_layer", return_value=MagicMock())
    mocker.patch.object(ping_view, "async_to_sync", fake_async_to_sync)

    request = MagicMock()
    request.GET = {"seq": "7"}
    response = ping_view.loadtest_ping(request)

    assert response.status_code == 200
    assert captured["group"] == "global"
    assert captured["message"] == {
        "type": "annotation_notify",
        "message": "loadtest_ping:7",
    }
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd web_api && PYTHONHASHSEED=0 pytest web_annotation/tests/test_ping_view.py -v`
Expected: FAIL — `ImportError`/`ModuleNotFoundError: web_annotation.loadtest.ping_view` (the module does not exist yet) and `AttributeError` on `settings_e2e.LOADTEST_PING_ENABLED`.

- [ ] **Step 3: Create the ping view**

Create `web_api/web_annotation/loadtest/ping_view.py`:

```python
"""Test-only WebSocket ping producer for the #170 WS-responsiveness harness.

Relays a sentinel notification to the ``"global"`` channel group so a connected
``AnnotationStateConsumer`` re-emits it to its socket. The harness stamps the
HTTP-send time and the WS-receipt time CLIENT-SIDE and correlates by ``seq``,
measuring WS push latency under load (iossifovlab/gain#170).

Gated behind ``settings.LOADTEST_PING_ENABLED`` (true only under
``settings_e2e``) so it can never appear in a production URLconf. It re-uses the
consumer's existing ``annotation_notify`` handler, so NO production consumer
change is needed. The view is SYNC: it runs on a ``thread_sensitive`` thread
(reachable even while the loop is busy), and its ``async_to_sync(group_send)``
hop onto the loop carries any loop-contention into the measured window.
"""
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.http import HttpRequest, HttpResponse
from django.views.decorators.csrf import csrf_exempt


@csrf_exempt
def loadtest_ping(request: HttpRequest) -> HttpResponse:
    """Group-send a sentinel ping to the ``global`` group; return 200."""
    seq = request.GET.get("seq", "0")
    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(
        "global",
        {"type": "annotation_notify", "message": f"loadtest_ping:{seq}"},
    )
    return HttpResponse("ok")
```

- [ ] **Step 4: Add the settings flag**

In `web_api/web_annotation/settings_e2e.py`, append after the existing settings:

```python
# Enable the test-only WS ping route (web_annotation.loadtest.ping_view) used by
# the #170 WS-notification-responsiveness harness. Never set in production.
LOADTEST_PING_ENABLED = True
```

- [ ] **Step 5: Wire the gated route**

In `web_api/web_annotation/urls.py`, immediately after the `admin_panel` block (currently ending at line 72) and before `websocket_urlpatterns`, add:

```python
if getattr(settings, "LOADTEST_PING_ENABLED", False):
    from web_annotation.loadtest.ping_view import loadtest_ping
    urlpatterns += [path("api/_loadtest/ping", loadtest_ping)]
```

(`settings` and `path` are already imported in `urls.py`.)

- [ ] **Step 6: Run the test to verify it passes**

Run: `cd web_api && PYTHONHASHSEED=0 pytest web_annotation/tests/test_ping_view.py -v`
Expected: PASS (2 passed).

- [ ] **Step 7: Lint**

Run: `cd .. && ruff check --fix web_api/web_annotation/loadtest/ping_view.py web_api/web_annotation/tests/test_ping_view.py && mypy gain --exclude core/docs/ --exclude core/gain/docs/ && pylint --rcfile=pylintrc gain`
Expected: no findings on the touched files. (If `mypy`/`pylint` over the whole `gain` package is slow, scope to the package per `web_api/CLAUDE.md` conventions, but run all three before committing.)

- [ ] **Step 8: Commit**

```bash
git add web_api/web_annotation/loadtest/ping_view.py \
        web_api/web_annotation/settings_e2e.py \
        web_api/web_annotation/urls.py \
        web_api/web_annotation/tests/test_ping_view.py
git commit -m "Add test-only gated WS ping endpoint for #170 responsiveness harness"
```

---

### Task 2: CI-durable WS-push-latency regression test + positive control

**Files:**
- Test: `web_api/web_annotation/tests/test_ws_notification_responsiveness.py` (Create)

**Interfaces:**
- Consumes: `slow_build` fixture idiom (re-declared locally), `CustomWebsocketCommunicator`, `AnnotationStateConsumer.as_asgi()`, `get_channel_layer()`, `django.test.AsyncClient`, `SingleAnnotation.lru_cache.unload_pipeline`.
- Produces: two committed async tests that future regressions (a stray sync ORM call landing on the loop in a #165–#167 async view) will trip:
  - `test_ws_push_latency_bounded_under_concurrent_builds` — green when the loop stays clean.
  - `test_ws_push_latency_detects_on_loop_block` — the positive control proving the sampler is *sensitive* (a "no-leak" result is meaningless if the harness can't detect a leak).

**Design notes (read before writing):**
- Both `AsyncClient` (drives the real async-view build path) and the `CustomWebsocketCommunicator` run in the **same test event loop**. Builds sleep on a **loader thread** (off-loop), so under clean code the WS ping round-trip stays in the **single-digit-ms** range; the bound is set well below the slow-build window.
- The ping vehicle is `group_send("global", {"type": "annotation_notify", "message": "loadtest_ping:<seq>"})`; the consumer relays `{"message": "loadtest_ping:<seq>"}`. `_recv_ping` ignores any non-matching frames (e.g. resync/`pipeline_status`).
- Use a **fresh user with no saved pipelines** so `connect`'s resync emits no frames to drain.
- The positive control deliberately uses a **deterministic in-loop `time.sleep` between emit and receipt** rather than a racy view monkeypatch, so CI is stable. This still proves the property under test: *if the loop is parked between a notification's emit and its delivery, the sampled latency reflects the full park.* (This is the deterministic cousin of "patch an async view to block the loop"; the realism trade-off is documented in the SLO doc.)

- [ ] **Step 1: Write the failing test file**

Create `web_api/web_annotation/tests/test_ws_notification_responsiveness.py`:

```python
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
    """Make pipeline builds take ~SLOW seconds on the loader thread (off-loop)."""
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

    block = 0.4
    t0 = time.monotonic()
    await channel_layer.group_send(
        "global",
        {"type": "annotation_notify", "message": "loadtest_ping:0"},
    )
    time.sleep(block)  # park the event loop thread between emit and receipt
    await _recv_ping(communicator, 0)
    latency = time.monotonic() - t0
    await communicator.disconnect(timeout=5)

    assert latency >= block * 0.8, (
        f"sampler measured {latency:.3f}s for a {block}s loop park -- "
        f"it is NOT sensitive to on-loop blocking, so a null result is untrustworthy"
    )
    assert latency >= LATENCY_BOUND_S, (
        f"a real loop park ({latency:.3f}s) must breach the no-leak bound "
        f"{LATENCY_BOUND_S}s"
    )
```

- [ ] **Step 2: Run to verify the suite behaves (both tests pass on clean code)**

Run: `cd web_api && PYTHONHASHSEED=0 pytest web_annotation/tests/test_ws_notification_responsiveness.py -v`
Expected: 2 passed. `test_ws_push_latency_bounded...` passes because the clean loop delivers pings in ms; `test_ws_push_latency_detects_on_loop_block` passes because the deliberate park is measured (~0.4 s ≥ 0.32 s and ≥ 0.15 s).

> If `test_ws_push_latency_bounded...` *fails* on first run, that is a genuine signal — a real on-loop blocker exists in the async-view path. Stop and capture it for the SLO doc (Task 4, audit + "blocking-on-loop path identified") rather than loosening the bound.

- [ ] **Step 3: Prove the no-leak test is not vacuously green (sanity-flip)**

Temporarily insert `time.sleep(0.4)` at the top of `fire_request` *inside the loop body*’s `await channel_layer.group_send` is the wrong place — instead, temporarily change `LATENCY_BOUND_S = 0.0001` and re-run only `test_ws_push_latency_bounded_under_concurrent_builds`.
Expected: FAIL (worst latency ≥ a few ms > 0.0001 s), confirming the assertion is live. Then **revert** `LATENCY_BOUND_S` to `0.15`.

Run: `cd web_api && PYTHONHASHSEED=0 pytest web_annotation/tests/test_ws_notification_responsiveness.py::test_ws_push_latency_bounded_under_concurrent_builds -v`

- [ ] **Step 4: Re-run the full file after reverting the bound**

Run: `cd web_api && PYTHONHASHSEED=0 pytest web_annotation/tests/test_ws_notification_responsiveness.py -v`
Expected: 2 passed.

- [ ] **Step 5: Lint**

Run: `cd .. && ruff check --fix web_api/web_annotation/tests/test_ws_notification_responsiveness.py && mypy gain --exclude core/docs/ --exclude core/gain/docs/ && pylint --rcfile=pylintrc gain`
Expected: no findings on the touched file.

- [ ] **Step 6: Commit**

```bash
git add web_api/web_annotation/tests/test_ws_notification_responsiveness.py
git commit -m "Add WS push-latency regression test + on-loop-block positive control (#170)"
```

---

### Task 3: Daphne + aiohttp WS-notification load harness

**Files:**
- Create: `web_api/web_annotation/loadtest/ws_notification_slo.py`
- Create: `web_api/web_annotation/loadtest/run_ws_matrix.sh`
- Test: `web_api/web_annotation/tests/test_ws_notification_slo_harness.py` (a thin unit test for the pure helpers — the end-to-end run is exercised manually in Task 4)

**Interfaces:**
- Consumes: the running daphne server (started by the existing `run_daphne_server.sh`, which already uses `settings_e2e` ⇒ the Task 1 ping route is live), `POST /api/login`, `POST /api/single_allele/annotate`, `GET /api/_loadtest/ping?seq=N`, `WS /ws/notifications/`.
- Produces: a JSON record `{label, params, annotate{...}, ws{baseline{...}, under_load{...}}}` where each `{...}` is `_percentile` summary (p50/p95/p99/max/min/count). Consumed by `run_ws_matrix.sh` → combined JSON array → the SLO doc (Task 4).

- [ ] **Step 1: Write the failing helper test**

Create `web_api/web_annotation/tests/test_ws_notification_slo_harness.py`:

```python
# pylint: disable=C0114,C0116
from web_annotation.loadtest import ws_notification_slo as ws


def test_percentile_basic() -> None:
    values = [1.0, 2.0, 3.0, 4.0]
    assert ws._percentile(sorted(values), 50) == 2.5
    assert ws._percentile(sorted(values), 100) == 4.0


def test_summary_splits_empty() -> None:
    summary = ws._summary([])
    assert summary["count"] == 0
    assert summary["p95_ms"] is None


def test_summary_reports_percentiles() -> None:
    summary = ws._summary([10.0, 20.0, 30.0, 40.0])
    assert summary["count"] == 4
    assert summary["p50_ms"] == 25.0
    assert summary["max_ms"] == 40.0
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd web_api && PYTHONHASHSEED=0 pytest web_annotation/tests/test_ws_notification_slo_harness.py -v`
Expected: FAIL — `ModuleNotFoundError: web_annotation.loadtest.ws_notification_slo`.

- [ ] **Step 3: Create the harness module**

Create `web_api/web_annotation/loadtest/ws_notification_slo.py`:

```python
#!/usr/bin/env python
"""WS notification-responsiveness load harness (iossifovlab/gain#170).

Measures WebSocket push latency (a cheap sentinel notification's emit->receipt)
while a SUSTAINED stream of K in-flight slow-build annotate requests churns the
event loop. The notifications consumer is a SYNC WebsocketConsumer, so it does
not itself block the loop; the surface under test is the async-view + group_send
path. If an async view leaks blocking work onto the loop, the sampled WS latency
spikes; if the loop stays clean, it stays flat.

Clocking: CLIENT-SIDE round-trip stamping. The harness records ``t_send`` when it
POSTs ``GET /api/_loadtest/ping?seq=N`` and ``t_recv`` when the matching
``loadtest_ping:N`` frame arrives on the WS -- both in THIS process, so no
cross-process clock comparison. The ping route is test-only and gated under
``settings_e2e`` (see web_annotation/loadtest/ping_view.py).

Phases: a BASELINE window (no load) establishes idle push latency, then the
sustained build load runs for ``--duration`` while sampling continues
(UNDER-LOAD), then a short recovery. The report splits ws latency into
baseline/under_load so the doc shows the idle->load delta.

Uses ``aiohttp`` (already in the gain venv). No new dependency.

Usage:
    python -m web_annotation.loadtest.ws_notification_slo \\
        --base-url http://127.0.0.1:21011 --ws-url ws://127.0.0.1:21011/ws/notifications/ \\
        --concurrency 16 --duration 10 --ping-interval 0.1 \\
        --delay 0.4 --label async-K16 --email loadtest@example.com
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import dataclass, field
from typing import Any

import aiohttp

VERSION_PATH = "/api/version"
ANNOTATE_PATH = "/api/single_allele/annotate"
LOGIN_PATH = "/api/login"
PING_PATH = "/api/_loadtest/ping"


def _percentile(sorted_values: list[float], pct: float) -> float:
    """Nearest-rank percentile of an already-sorted, non-empty list."""
    if not sorted_values:
        return float("nan")
    if len(sorted_values) == 1:
        return sorted_values[0]
    rank = pct / 100.0 * (len(sorted_values) - 1)
    low = int(rank)
    high = min(low + 1, len(sorted_values) - 1)
    frac = rank - low
    return sorted_values[low] * (1.0 - frac) + sorted_values[high] * frac


def _summary(latencies_ms: list[float]) -> dict[str, Any]:
    """p50/p95/p99/max/min summary of a latency list (ms)."""
    ok = sorted(latencies_ms)
    return {
        "count": len(ok),
        "p50_ms": round(_percentile(ok, 50), 2) if ok else None,
        "p95_ms": round(_percentile(ok, 95), 2) if ok else None,
        "p99_ms": round(_percentile(ok, 99), 2) if ok else None,
        "max_ms": round(max(ok), 2) if ok else None,
        "min_ms": round(min(ok), 2) if ok else None,
    }


@dataclass
class WsSamples:
    """WS ping round-trip latencies, tagged baseline vs under-load."""

    baseline_ms: list[float] = field(default_factory=list)
    under_load_ms: list[float] = field(default_factory=list)
    missed: int = 0


async def _login(
    session: aiohttp.ClientSession, base_url: str, email: str,
    password: str, timeout: float,
) -> None:
    """Obtain a session cookie (unlimited user bypasses throttle+quota)."""
    async with session.post(
        f"{base_url}{LOGIN_PATH}", json={"email": email, "password": password},
        timeout=aiohttp.ClientTimeout(total=timeout),
    ) as resp:
        if resp.status != 200:
            body = await resp.text()
            raise RuntimeError(f"login returned {resp.status}: {body[:200]}")


async def _sanity_check(
    session: aiohttp.ClientSession, base_url: str, timeout: float,
) -> None:
    async with session.get(
        f"{base_url}{VERSION_PATH}",
        timeout=aiohttp.ClientTimeout(total=timeout),
    ) as resp:
        if resp.status != 200:
            body = await resp.text()
            raise RuntimeError(f"sanity returned {resp.status}: {body[:200]}")


def _csrf_header(session: aiohttp.ClientSession) -> dict[str, str]:
    for cookie in session.cookie_jar:
        if cookie.key == "csrftoken":
            return {"X-CSRFToken": cookie.value}
    return {}


async def _fire_annotate(
    session: aiohttp.ClientSession, base_url: str, pipeline_id: str,
    timeout: float,
) -> str:
    """Fire one POST annotate; return its status (or error tag)."""
    payload = {
        "annotatable": {"chrom": "chr1", "pos": "3"},
        "pipeline_id": pipeline_id,
    }
    try:
        async with session.post(
            f"{base_url}{ANNOTATE_PATH}", json=payload,
            headers=_csrf_header(session),
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            await resp.read()
            return str(resp.status)
    except asyncio.TimeoutError:
        return "timeout"
    except aiohttp.ClientError as exc:
        return f"error:{type(exc).__name__}"


async def _sustain_load(
    session: aiohttp.ClientSession, base_url: str, pipeline_id: str,
    *, concurrency: int, stop: asyncio.Event, timeout: float,
    statuses: list[str],
) -> None:
    """Keep ``concurrency`` annotate requests in flight until ``stop`` is set."""
    inflight: set[asyncio.Future] = set()
    while not stop.is_set():
        while len(inflight) < concurrency:
            inflight.add(asyncio.ensure_future(
                _fire_annotate(session, base_url, pipeline_id, timeout)))
        done, inflight = await asyncio.wait(
            inflight, timeout=0.05, return_when=asyncio.FIRST_COMPLETED)
        for fut in done:
            statuses.append(fut.result())
    for fut in await asyncio.gather(*inflight, return_exceptions=True):
        if isinstance(fut, str):
            statuses.append(fut)


async def _ws_receiver(
    ws: aiohttp.ClientWebSocketResponse, pending: dict[int, float],
    samples: WsSamples, load_active: asyncio.Event, stop: asyncio.Event,
) -> None:
    """Match ``loadtest_ping:N`` frames to their send time; record latency."""
    async for msg in ws:
        if msg.type != aiohttp.WSMsgType.TEXT:
            continue
        try:
            data = json.loads(msg.data)
        except ValueError:
            continue
        message = data.get("message", "")
        if not isinstance(message, str) or not message.startswith(
                "loadtest_ping:"):
            continue
        seq = int(message.split(":", 1)[1])
        sent = pending.pop(seq, None)
        if sent is None:
            continue
        latency_ms = (time.monotonic() - sent) * 1000.0
        if load_active.is_set():
            samples.under_load_ms.append(latency_ms)
        else:
            samples.baseline_ms.append(latency_ms)
        if stop.is_set() and not pending:
            return


async def _ws_pinger(
    session: aiohttp.ClientSession, base_url: str, pending: dict[int, float],
    *, interval: float, stop: asyncio.Event, timeout: float,
    samples: WsSamples,
) -> None:
    """POST the ping endpoint every ``interval`` s, stamping send time."""
    seq = 0
    while not stop.is_set():
        pending[seq] = time.monotonic()
        try:
            async with session.get(
                f"{base_url}{PING_PATH}", params={"seq": str(seq)},
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                await resp.read()
        except (asyncio.TimeoutError, aiohttp.ClientError):
            pending.pop(seq, None)
            samples.missed += 1
        seq += 1
        await asyncio.sleep(interval)


async def run(args: argparse.Namespace) -> dict[str, Any]:
    """Run baseline -> sustained-load -> recovery; return the result record."""
    base_url = args.base_url.rstrip("/")
    samples = WsSamples()
    pending: dict[int, float] = {}
    statuses: list[str] = []
    stop_all = asyncio.Event()
    stop_load = asyncio.Event()
    load_active = asyncio.Event()

    jar = aiohttp.CookieJar(unsafe=True)
    connector = aiohttp.TCPConnector(limit=0)
    async with aiohttp.ClientSession(
            connector=connector, cookie_jar=jar) as session:
        await _sanity_check(session, base_url, args.timeout)
        if args.email:
            await _login(
                session, base_url, args.email, args.password, args.timeout)

        async with session.ws_connect(args.ws_url) as ws:
            receiver = asyncio.ensure_future(_ws_receiver(
                ws, pending, samples, load_active, stop_all))
            pinger = asyncio.ensure_future(_ws_pinger(
                session, base_url, pending, interval=args.ping_interval,
                stop=stop_all, timeout=args.timeout, samples=samples))

            # BASELINE: sample the idle loop before any load.
            await asyncio.sleep(args.baseline)

            # UNDER LOAD: sustain K in-flight builds for --duration.
            load_active.set()
            loader = asyncio.ensure_future(_sustain_load(
                session, base_url, args.pipeline_id,
                concurrency=args.concurrency, stop=stop_load,
                timeout=args.timeout, statuses=statuses))
            await asyncio.sleep(args.duration)
            stop_load.set()
            await loader
            load_active.clear()

            # RECOVERY: brief tail, then drain.
            await asyncio.sleep(args.recovery)
            stop_all.set()
            await pinger
            try:
                await asyncio.wait_for(receiver, timeout=args.timeout)
            except asyncio.TimeoutError:
                receiver.cancel()
            await ws.close()

    return {
        "label": args.label,
        "params": {
            "base_url": base_url,
            "ws_url": args.ws_url,
            "pipeline_id": args.pipeline_id,
            "concurrency_K": args.concurrency,
            "duration_seconds": args.duration,
            "ping_interval_seconds": args.ping_interval,
            "injected_build_delay_seconds": args.delay,
        },
        "annotate": {
            "fired": len(statuses),
            "ok_200": sum(1 for s in statuses if s == "200"),
            "non_200": sorted({s for s in statuses if s != "200"}),
        },
        "ws": {
            "missed_pings": samples.missed,
            "baseline": _summary(samples.baseline_ms),
            "under_load": _summary(samples.under_load_ms),
        },
    }


def build_parser() -> argparse.ArgumentParser:
    """Build the harness CLI argument parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:21011")
    parser.add_argument(
        "--ws-url", default="ws://127.0.0.1:21011/ws/notifications/")
    parser.add_argument("--pipeline-id", default="t4c8/t4c8_pipeline")
    parser.add_argument("--concurrency", "-K", type=int, default=16)
    parser.add_argument("--duration", type=float, default=10.0)
    parser.add_argument("--baseline", type=float, default=1.0)
    parser.add_argument("--recovery", type=float, default=1.0)
    parser.add_argument("--ping-interval", type=float, default=0.1)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--delay", type=float, default=None)
    parser.add_argument("--label", default=None)
    parser.add_argument("--email", default=None)
    parser.add_argument("--password", default="secret")
    parser.add_argument("--compact", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Parse args, run the harness, print the JSON result record."""
    args = build_parser().parse_args(argv)
    result = asyncio.run(run(args))
    if args.compact:
        json.dump(result, sys.stdout, separators=(",", ":"))
    else:
        json.dump(result, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the helper test to verify it passes**

Run: `cd web_api && PYTHONHASHSEED=0 pytest web_annotation/tests/test_ws_notification_slo_harness.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Create the K-sweep script**

Create `web_api/web_annotation/loadtest/run_ws_matrix.sh`:

```bash
#!/usr/bin/env bash
# Drive the #170 WS-notification SLO harness across a K-sweep against a FRESH
# (cold-pipeline) daphne server per K. Emits one compact JSON record per K to
# stdout and a combined JSON array to $OUT (default: ws-matrix-${LABEL}.json).
#
# Reuses run_daphne_server.sh (which uses settings_e2e -> the test-only ping
# route is live). Run the SAME script on the post-#163 master to record the
# headline numbers; a pre/post version comparison is intentionally NOT the
# deliverable (see docs/170-ws-notification-responsiveness-slo.md -- both
# versions keep the loop clean, so the comparison is low-information; the
# committed positive-control test is the sensitivity proof instead).
#
# Usage:
#   DELAY=0.4 KS="8 16 32" LABEL=async DURATION=10 \
#     bash web_annotation/loadtest/run_ws_matrix.sh
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WEB_API_DIR="$(cd "${HERE}/../.." && pwd)"
REPO_ROOT="$(cd "${WEB_API_DIR}/.." && pwd)"
cd "${WEB_API_DIR}"

if [[ -z "${VIRTUAL_ENV:-}" && -f "${REPO_ROOT}/.venv/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source "${REPO_ROOT}/.venv/bin/activate"
fi

DELAY="${DELAY:-0.4}"
KS="${KS:-8 16 32}"
LABEL="${LABEL:-run}"
DURATION="${DURATION:-10}"
TIMEOUT="${TIMEOUT:-30}"
PORT="${PORT:-21011}"
EMAIL="${EMAIL:-loadtest@example.com}"
OUT="${OUT:-${HERE}/ws-matrix-${LABEL}.json}"

records=()
for K in ${KS}; do
  echo "[run_ws_matrix] === ${LABEL} K=${K} delay=${DELAY}s (fresh cold server) ===" >&2
  pkill -f "daphne -b 127.0.0.1 -p ${PORT}" 2>/dev/null || true
  sleep 1
  GPFWA_BUILD_DELAY_SECONDS="${DELAY}" PORT="${PORT}" \
    bash "${HERE}/run_daphne_server.sh" > "/tmp/daphne-ws-${LABEL}-K${K}.log" 2>&1 &
  for _ in $(seq 1 60); do
    if curl -s -m2 "http://127.0.0.1:${PORT}/api/version" >/dev/null 2>&1; then
      break
    fi
    sleep 0.5
  done
  rec="$(python -m web_annotation.loadtest.ws_notification_slo \
      --base-url "http://127.0.0.1:${PORT}" \
      --ws-url "ws://127.0.0.1:${PORT}/ws/notifications/" \
      --concurrency "${K}" --duration "${DURATION}" --timeout "${TIMEOUT}" \
      --delay "${DELAY}" --label "${LABEL}-K${K}" \
      --email "${EMAIL}" --compact)"
  echo "${rec}"
  records+=("${rec}")
  pkill -f "daphne -b 127.0.0.1 -p ${PORT}" 2>/dev/null || true
  sleep 1
done

printf '%s\n' "${records[@]}" | python -c "import sys,json; print(json.dumps([json.loads(l) for l in sys.stdin if l.strip()], indent=2))" > "${OUT}"
echo "[run_ws_matrix] wrote ${OUT}" >&2
```

- [ ] **Step 6: Make the script executable + smoke-check the module imports/CLI**

Run:
```bash
cd web_api
chmod +x web_annotation/loadtest/run_ws_matrix.sh
PYTHONHASHSEED=0 python -m web_annotation.loadtest.ws_notification_slo --help >/dev/null && echo "CLI OK"
```
Expected: `CLI OK` (argparse help builds without error).

- [ ] **Step 7: Lint**

Run: `cd .. && ruff check --fix web_api/web_annotation/loadtest/ws_notification_slo.py web_api/web_annotation/tests/test_ws_notification_slo_harness.py && mypy gain --exclude core/docs/ --exclude core/gain/docs/ && pylint --rcfile=pylintrc gain`
Expected: no findings on touched files. (Watch for pylint `C0103` on any reassigned module-level constant — there are none here, but keep an eye per `gain/CLAUDE.md`.)

- [ ] **Step 8: Commit**

```bash
git add web_api/web_annotation/loadtest/ws_notification_slo.py \
        web_api/web_annotation/loadtest/run_ws_matrix.sh \
        web_api/web_annotation/tests/test_ws_notification_slo_harness.py
git commit -m "Add daphne+aiohttp WS-notification load harness + K-sweep script (#170)"
```

---

### Task 4: Run the measurement, audit the async views, write the SLO doc

**Files:**
- Create: `web_api/docs/170-ws-notification-responsiveness-slo.md`

**Interfaces:**
- Consumes: Task 3 harness output (`ws-matrix-async.json`), the committed regression test (Task 2) as the sensitivity proof, a manual static read of the #163/#165/#166/#167 async views.
- Produces: the issue's four acceptance-criteria artifacts (harness ✓ from Tasks 1+3, numbers ✓, blocking-path-found-or-not ✓, recommendation ✓).

- [ ] **Step 1: Run the WS matrix on post-#163 master (single-daphne fallback)**

Run (from `gain/web_api`, gain venv active):
```bash
DELAY=0.4 KS="8 16 32" LABEL=async DURATION=10 PORT=21011 \
  bash web_annotation/loadtest/run_ws_matrix.sh
```
Capture `web_annotation/loadtest/ws-matrix-async.json`. Note the environment (Linux host, in-repo `t4c8/t4c8_pipeline` GRR, sqlite, `InMemoryChannelLayer`) — same limitations as #164 (full compose still needs `/mnt/cephfs` + the prebuilt image; not run here).

Expected shape: `ws.under_load.p95_ms` in the **single-to-low-tens of ms**, `missed_pings` ≈ 0, `baseline` ≈ `under_load` (loop stays clean). If `under_load.p95_ms` is large or `missed_pings > 0`, that is a real on-loop blocker — capture it.

- [ ] **Step 2: Drive the #165–#167 read views as a second load target**

The default harness load is `SingleAnnotation.post` (slow-build await — the widest contention window). Run a second pass that hammers the converted async **read** views (their only loop-leak risk is a stray sync ORM call) and confirm WS latency stays flat. Do this with a focused ad-hoc loop using the same server, e.g. drive `POST /api/editor/annotator_attributes`, `/api/editor/annotator_yaml`, `/api/editor/aggregators` (the #166 surface) and `PipelineDoc` `GET` (the #167 surface) at high concurrency while the WS pinger samples. Record p95 per endpoint group.

> Implementation note for the executor: extend `ws_notification_slo.py` with an optional `--load-endpoint {annotate,editor_reads}` switch only if the ad-hoc loop proves insufficient; do not over-build. The acceptance bar is "WS p95 flat while these views are hammered."

- [ ] **Step 3: Static on-loop-call audit of the async views**

Read each `async def` handler reachable from an `AsyncAnnotationBaseView` subclass and grep for blocking calls **not** wrapped in `sync_to_async` / not awaited off-thread:

```bash
cd web_api
grep -rn "async def \(post\|get\)" web_annotation/editor/views.py \
    web_annotation/single_allele_annotation/views.py \
    web_annotation/pipelines/views.py
# In each, look for: bare ORM (.objects.<...> without a/await or sync_to_async),
# .read_text(/open(/requests., concurrent.futures .result(), time.sleep.
grep -rn "\.objects\.\|\.result()\|time\.sleep\|\.read_text(\|open(" \
    web_annotation/editor/views.py | grep -v "await\|sync_to_async\|aget\|acreate\|afilter"
```
Record, for each async view, either the line of a confirmed on-loop blocking call (→ file a follow-up + quantify via the harness) or "clean — all blocking I/O is awaited off-thread / `sync_to_async`-wrapped". Cross-check against the measured null result from Step 1: a clean audit + flat WS latency = trustworthy "no on-loop blocker found".

- [ ] **Step 4: Write the SLO doc**

Create `web_api/docs/170-ws-notification-responsiveness-slo.md` mirroring `164-async-read-views-slo.md`'s structure. Required sections:
1. **Header** — issue, branch, date (2026-06-24), one-line purpose.
2. **What is measured & why the consumer is sync** — restate the premise correction: `AnnotationStateConsumer` is a sync `WebsocketConsumer`, so the loop-shared surface is the async views + `group_send`, not the consumer body. The harness is a *falsification test* for an async-view loop leak.
3. **Durable artifacts** — table: `ping_view.py` (gated), `ws_notification_slo.py` + `run_ws_matrix.sh`, the committed regression test (the **sensitivity proof**, since "no leak" is meaningless without it), and the no production consumer/runtime change.
4. **How it was run** — single-daphne fallback, params (delay 0.4 s, K ∈ {8,16,32}, duration 10 s, ping 0.1 s), environment limits.
5. **Measured numbers** — a table of `baseline` vs `under_load` WS p50/p95/p99/max + `missed_pings` per K, for the `SingleAnnotation` load and the #165–#167 read-view pass.
6. **On-loop blocker audit** — the Step 3 findings; "no on-loop blocker found" *or* the identified path + its quantified WS-latency impact.
7. **Verdict** — does the async migration measurably protect WS responsiveness, and is there an on-loop blocker worth fixing — feeding back into the value case for #165/#166/#167. Flag the framing judgement for the human go/no-go on the issue (as #164 did).

- [ ] **Step 5: Sanity-check the doc against the issue's acceptance criteria**

Re-read iossifovlab/gain#170's four checkboxes and confirm each maps to a section:
- harness driving concurrent builds + WS sampling → §3/§4 (Tasks 1+3);
- WS push-latency numbers in `docs/` → §5;
- blocking-on-loop path identified & quantified (or clear "none found") → §6;
- recommendation feeding #165/#166/#167 → §7.

- [ ] **Step 6: Commit**

```bash
git add web_api/docs/170-ws-notification-responsiveness-slo.md \
        web_api/web_annotation/loadtest/ws-matrix-async.json
git commit -m "Record #170 WS-notification responsiveness SLO: numbers, audit, verdict"
```

- [ ] **Step 7: Post the verdict back to the issue (close-out)**

Per `genomics-toolbox/CLAUDE.md` issue conventions, comment the headline result + verdict on the issue and leave the human go/no-go open:
```bash
gh issue comment 170 --repo iossifovlab/gain --body-file <(printf '%s\n' \
  "WS-notification responsiveness SLO recorded in web_api/docs/170-ws-notification-responsiveness-slo.md." \
  "<one-line headline: e.g. WS push p95 stays ~Xms under K=32 build load, 0 missed pings, no on-loop blocker found; sensitivity proven by committed positive-control test>." \
  "Flagging the framing for human go/no-go.")
```

---

## Self-Review

**Spec coverage** (issue #170 acceptance criteria):
- *Harness driving concurrent slow builds AND a WS client sampling p95/p99* → Tasks 1 (ping endpoint) + 3 (`ws_notification_slo.py` sustained load + WS sampler). ✓
- *WS push-latency numbers recorded in `docs/`* → Task 4 §5 + committed `ws-matrix-async.json`. ✓
- *Any blocking-on-loop path identified & quantified (or clear "none found")* → Task 4 §6 static audit cross-checked with measured null; sensitivity guaranteed by Task 2's positive control. ✓
- *Recommendation feeding #165/#166/#167* → Task 4 §7. ✓
- *Compare with/without load; optionally pre/post #163* → with/without via baseline-vs-under_load split (Task 3 report); pre/post #163 deliberately dropped as low-information (both versions keep the loop clean) and replaced by the positive control — rationale recorded in `run_ws_matrix.sh` header + Task 4 §2. ✓

**Placeholder scan:** No "TBD"/"handle edge cases"/"similar to Task N" — all code blocks are complete. Task 4 Step 2's optional `--load-endpoint` is gated with an explicit "only if the ad-hoc loop proves insufficient; do not over-build" instruction, not a placeholder.

**Type consistency:** `_percentile(sorted_values, pct)` and `_summary(latencies_ms)` signatures match between `ws_notification_slo.py` (Task 3 Step 3) and its test (Task 3 Step 1). The sentinel string `loadtest_ping:<seq>` is identical across `ping_view.py` (Task 1), the regression test's `_recv_ping` (Task 2), and the harness `_ws_receiver`/`_ws_pinger` (Task 3). The ping route `/api/_loadtest/ping` and group `"global"` are consistent across Tasks 1/3. `LATENCY_BOUND_S = 0.15` and `slow_build = 0.4` are used consistently within Task 2.

**Known execution risk to watch:** if `test_ws_push_latency_bounded_under_concurrent_builds` flakes on a loaded CI box (GC pause pushing a single ping over 0.15 s), prefer raising the bound to e.g. 0.25 s with a comment rather than asserting on a percentile — but only after confirming via the harness that the loop is genuinely clean. Do **not** weaken the positive control.
