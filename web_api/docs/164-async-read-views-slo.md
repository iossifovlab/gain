# #164 — Async read-view migration: load test & SLO sign-off

**Issue:** iossifovlab/gain#164 (blocked-by #163, now merged).
**Branch:** `chore/164-async-read-views-load-test` (off `origin/master`).
**Date:** 2026-06-24.

This is the load-harness + measured-numbers + go/no-go deliverable for the async
read-view migration. It is **not** a feature PR; it commits a reusable harness +
an env-gated build-delay instrumentation, and records what was measured.

---

## What #163 did and the SLO under test

#163 converted `SingleAnnotation.post` to async so a slow GRR pipeline build is
`await`ed off-thread (`aget_pipeline` → `await_build`) instead of blocking on
`future.result()` inside a synchronous DRF view.

The SLO this harness was built to validate:

> Under `K` concurrent slow-build single-annotation requests, a cheap sync
> endpoint (`GET /api/version`) must stay responsive (p95 ~flat, **zero 30s
> timeouts**) on the async code, whereas on the pre-#163 sync code its latency
> should balloon because the shared request thread is parked on the build.

The win being claimed is **responsiveness-under-contention**, not single-request
speed.

---

## Durable artifacts committed

| Path | What |
|---|---|
| `web_api/web_annotation/pipeline_cache.py` | `GPFWA_BUILD_DELAY_SECONDS` env-gated, deterministic build delay in `_load_pipeline_raw` (default `0.0` → **true no-op**; malformed/negative → `0.0`). Safe to leave in. |
| `web_api/web_annotation/loadtest/cheap_endpoint_slo.py` | asyncio + `aiohttp` harness: logs in, fires `K` concurrent `POST /api/single_allele/annotate` against a COLD pipeline, concurrently samples `GET /api/version`, reports cheap-endpoint p50/p95/p99 + timeout-rate as JSON. Parameterized K / delay / timeout / sample-interval; rerunnable. |
| `web_api/web_annotation/loadtest/run_daphne_server.sh` | Stand up one fresh `daphne` (cold pipeline) on `settings_e2e` + the in-repo test GRR + throwaway sqlite, creating an *unlimited* user. No postgres / no image build / no `/mnt/cephfs`. |
| `web_api/web_annotation/loadtest/run_matrix.sh` | Sweep `K` against a fresh cold server per `K`; emit a combined JSON array. Run on both checkouts. |

No new runtime dependency was added — `aiohttp` was already in the gain venv;
`httpx` is not installed.

> **Note on the endpoint path.** The annotate endpoint is
> `POST /api/single_allele/annotate` (the issue's shorthand "`/api/single_annotation`").
> It is served by `SingleAnnotation` in
> `web_annotation/single_allele_annotation/views.py`.

---

## How the measurement was run

**Fallback used: (b) a single `daphne` process.** The full e2e compose stack
(`web_infra/compose-jenkins.yaml`) needs the prebuilt `gain-web-api-prod` image
and a `/mnt/cephfs/seqpipe/grr` mount — neither is available on this machine —
so option (a) was not run here. Option (b) is lighter and *more* controllable,
and exercises the real async dispatch (adrf), the real `ThreadedTaskExecutor`
build pool, and the real Django ASGI handler.

```bash
cd gain/web_api && source ../.venv/bin/activate

# One sweep on the ASYNC checkout (origin/master, this branch):
DELAY=0.4 KS="8 16 32" LABEL=async TIMEOUT=30 PORT=21011 \
  bash web_annotation/loadtest/run_matrix.sh        # -> matrix-async.json

# Same sweep on the SYNC baseline (a04a82926, post-#162 / pre-#163), with the
# identical GPFWA_BUILD_DELAY_SECONDS delay cherry-picked onto _load_pipeline_raw,
# run from a worktree on its own PYTHONPATH:
git worktree add --detach /tmp/baseline a04a82926
# (apply the same env-gated delay to /tmp/baseline/.../pipeline_cache.py,
#  copy web_annotation/loadtest/ over)
DELAY=0.4 KS="8 16 32" LABEL=sync TIMEOUT=30 PORT=21012 \
  bash web_annotation/loadtest/run_matrix.sh        # -> matrix-sync.json
```

Baseline `a04a82926` confirmed as the **direct parent** of the #163 conversion
commit (`db5219b0a`/`6914466b0`): adrf present, `SingleAnnotation(AnnotationBaseView)`
still synchronous.

Params: injected build delay **0.4 s**, per-request timeout / SLO threshold
**30 s**, cheap-endpoint sample interval **0.1 s**, K ∈ {8, 16, 32}, fresh
cold-pipeline server per K, unlimited user (bypasses the 10/min `UserRateThrottle`
and the single-allele quota so every request reaches the build wait).

---

## Measured numbers (cheap `GET /api/version`, delay 0.4 s, timeout 30 s)

| Checkout | K | annotate 200s | p50 (ms) | p95 (ms) | p99 (ms) | max (ms) | **timeouts** |
|---|---:|---:|---:|---:|---:|---:|---:|
| **sync baseline** (`a04a82926`) | 8  | 8/8   | 11.3 | 26.9 | 31.9 | 33.2  | **0** |
| **sync baseline** | 16 | 16/16 | 9.2  | 50.5 | 51.5 | 51.7  | **0** |
| **sync baseline** | 32 | 32/32 | 11.1 | 58.0 | 99.5 | 109.9 | **0** |
| **async** (`origin/master`) | 8  | 8/8   | 4.8  | 16.9 | 20.2 | 21.1  | **0** |
| **async** | 16 | 16/16 | 7.2  | 33.7 | 46.4 | 49.6  | **0** |
| **async** | 32 | 32/32 | 4.9  | 61.8 | 104.4 | 115.0 | **0** |

Both columns stay in the **tens of milliseconds** and have **zero 30 s
breaches**. The cheap endpoint did **not** balloon on the sync baseline.

---

## Why the sync baseline did NOT balloon — the load-bearing finding

The SLO's premise is that a slow sync build parks "the single shared daphne
`thread_sensitive` sync-view thread", so a concurrent cheap sync view queues
behind it. **That premise does not hold under Django's real ASGI handler.**

Django's `ASGIHandler.__call__` wraps *each HTTP request* in its **own**
`async with ThreadSensitiveContext()` before running the sync view via
`sync_to_async(..., thread_sensitive=True)`. A fresh `ThreadSensitiveContext`
gets its **own** dedicated thread, so two concurrent HTTP requests run their
sync views on **different** threads — they do not serialize.

Verified two ways on this machine (asgiref 3.11.1, daphne 4.2.1, Django 5.2.13):

1. **Isolated probe.** Two `sync_to_async(thread_sensitive=True)` calls *sharing*
   one `ThreadSensitiveContext` serialize (cheap waits 0.9 s behind a 1 s
   sleeper, both on `ThreadPoolExecutor-0_0`). Wrapping each in its **own**
   `ThreadSensitiveContext` (the Django ASGI pattern) does **not** serialize —
   they land on `ThreadPoolExecutor-1_0` vs `-2_0` and cheap returns in ~1 ms.

2. **Live sync server.** With a cold 2–3 s build in flight against the sync
   baseline daphne, `GET /api/version` stayed at **~0.02 s** across repeated
   probes — it never queued behind the build.

So at the **HTTP layer**, the cheap *sync* endpoint is already insulated from a
slow build on the sync baseline, because per-request `ThreadSensitiveContext`
isolates request threads. #163 does not change that particular outcome.

### Where #163's benefit is real

#163's proven, separate benefit is keeping the **event loop** unblocked. The
pre-#150 bug ran the build inline on the loop; #150 deferred it to a thread
pool, and #163 makes the async view `await` the build off-thread rather than
blocking a request thread on `future.result()`. The event-loop responsiveness
guarantee is already covered by the in-repo regression test
`test_concurrent_slow_builds_do_not_park_event_loop`
(`tests/test_single_annotation_async.py`), which drives the ASGI app and asserts
a heartbeat coroutine keeps ticking (max loop gap < the slow-build window) while
N slow builds run. That test measures the **event loop**; this harness measures
a competing **sync HTTP endpoint**.

---

## SLO verdict

- **Stated SLO ("async cheap-endpoint p95 stays ~flat as K rises with zero 30 s
  timeouts, while the sync baseline balloons"):** the *zero-timeout, flat-p95*
  half **PASSES on async** (and on sync). The *"sync baseline balloons"* half
  **does NOT reproduce** at the HTTP layer — the sync baseline also stays flat,
  because Django's per-request `ThreadSensitiveContext` isolates request threads.
  So the head-to-head delta the SLO predicted is **not observable via a
  competing sync HTTP endpoint** on a real daphne.
- **What is verified:** async is at parity-or-better with the sync baseline on
  cheap-endpoint p50/p95/p99 and has zero timeouts at K up to 32; the
  event-loop-responsiveness benefit of #163 holds (covered by the existing async
  test).

---

## GO / NO-GO recommendation for the rollout (#165–#167)

**Recommendation: GO — with the SLO restated.**

Rationale:

1. **No regression.** Async cheap-endpoint latency is at parity-or-better with
   the sync baseline across K ∈ {8, 16, 32}, with **zero 30 s breaches** on both.
   Converting more read views to async carries no measured responsiveness cost
   at the HTTP layer.
2. **The real win is event-loop responsiveness**, which is what matters for the
   async surface that *does* share the loop — WebSocket notification consumers
   (`AnnotationStateConsumer`) and any future async views — and which is already
   proven by `test_concurrent_slow_builds_do_not_park_event_loop`.
3. **Restate the pass criterion for #165–#167.** Drop "the sync baseline cheap
   *HTTP* endpoint balloons" — that is not how Django ASGI behaves (per-request
   `ThreadSensitiveContext`). Use instead:
   - async cheap-endpoint **p95 stays ~flat as K rises with zero 30 s timeouts**
     (this harness), **and**
   - the **event loop** stays unblocked under concurrent slow builds (the
     heartbeat regression test) — this is the property the migration actually
     protects.

This is a judgement call about the SLO's framing, so it is flagged for the human
go/no-go on the issue rather than decided unilaterally.

---

## Reproduce / extend

- Run on a proper host or the `gain-web-e2e` CI job for the full-stack variant
  (option (a)): bring up `web_infra/compose-jenkins.yaml` with the
  `gain-web-api-prod` image and a seeded GRR, set
  `GPFWA_BUILD_DELAY_SECONDS=0.4` on the `backend` service, then point the
  harness at the backend port.
- Single endpoint run:

  ```bash
  cd gain/web_api && source ../.venv/bin/activate
  GPFWA_BUILD_DELAY_SECONDS=0.4 PORT=21011 \
    bash web_annotation/loadtest/run_daphne_server.sh &    # fresh cold server
  python -m web_annotation.loadtest.cheap_endpoint_slo \
    --base-url http://127.0.0.1:21011 --concurrency 16 \
    --timeout 30 --delay 0.4 --label async-K16 \
    --email loadtest@example.com
  ```

- A sharper event-loop-vs-thread story (not required for go/no-go) would sample
  a WebSocket round-trip on the notifications consumer instead of a sync HTTP
  view, since that endpoint truly shares the event loop.

### Environmental limits on what was measured locally

- Full e2e compose stack (option (a)) **not run** here: needs the prebuilt
  `gain-web-api-prod` image and the `/mnt/cephfs/seqpipe/grr` mount.
- Measurements used the in-repo test GRR (`t4c8/t4c8_pipeline`) on macOS with
  sqlite + InMemoryChannelLayer — representative for the daphne request/thread
  model and the build-contention behavior under test, but not for production
  GRR build times or postgres latency. The absolute millisecond numbers are
  machine-specific; the **shape** (flat p95, zero timeouts, sync≈async) is the
  result that matters.
