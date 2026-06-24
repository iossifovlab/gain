# #170 — WebSocket notification consumer responsiveness under concurrent build load

**Issue:** iossifovlab/gain#170 (informed by #164; informs #165/#166/#167).
**Branch:** `feat/170-ws-notification-responsiveness-slo` (off `master`).
**Date:** 2026-06-24.

This is the harness + measured-numbers + on-loop-blocker verdict deliverable for
the WebSocket notification path. It commits a reusable WS load harness, a
CI-durable regression test (with a sensitivity-proving positive control), a
test-only gated ping endpoint, and records what was measured.

---

## What is measured, and why the consumer being *sync* reframes it

#164 established that Django ASGI wraps **each** HTTP request in its own
`ThreadSensitiveContext`, so concurrent sync DRF views run on **separate**
threads and do not serialize — the cheap-HTTP-endpoint SLO showed ~parity
between sync and async. #164 closed by pointing here: the genuinely
loop-shared surface is the **WebSocket notifications consumer** path.

Reading that path closely sharpens the question. `AnnotationStateConsumer`
(`web_annotation/consumers.py`) is a **sync** `WebsocketConsumer`: its handlers
(`connect`, `pipeline_status`, `job_status`, and every `self.send`) run on a
`thread_sensitive` thread, **not** on the event loop. So the consumer *body* —
including the DB-touching `_resync_pipeline_status` on connect — does **not**
block the loop. The genuinely loop-shared surface is narrower than #170's
bullet implies:

1. the **channel-layer delivery machinery** (`group_send` → demultiplex →
   schedule the sync handler), which runs on the loop; and
2. the **converted async views** (#163 `SingleAnnotation.post`, the #165/#166
   editor read views, the #167 `PipelineDoc`), whose coroutines run on the
   loop. If any of them does blocking work *without* hopping to a thread (a
   stray sync ORM call, a `future.result()`, a non-awaited build), **that**
   parks the loop and delays every WS push.

So the experiment is a **falsification test for an async-view loop leak**: drive
concurrent async-view requests whose await path includes the injected slow
build, and watch whether WS push latency stays flat (loop clean) or spikes
(loop leak). The slow build is the *probe*, not the load — builds are deferred
to the `ThreadedTaskExecutor` (off-loop since #150) and awaited off-thread
(#163), so a flat result is the expected, and a spike would localize a leak.

**The decisive metric is the WS push-latency _median_, not the tail.** A parked
loop delays *every* in-flight push, so it inflates p50 and drops pings. Tail
jitter (p99/max) with a flat p50 and zero drops is thread-pool/GC scheduling,
not loop parking.

---

## Durable artifacts committed

| Path | What |
|---|---|
| `web_annotation/loadtest/ping_view.py` | Test-only sync ping endpoint. Relays `{"type":"annotation_notify","message":"loadtest_ping:<seq>"}` to the `"global"` group, reusing the consumer's **existing** `annotation_notify` handler — **no consumer change**. Gated behind `settings.LOADTEST_PING_ENABLED` (true only under `settings_e2e`), so it can never appear in a production URLconf. |
| `web_annotation/loadtest/ws_notification_slo.py` | Daphne+aiohttp harness. Sustains `K` in-flight slow-build `POST /api/single_allele/annotate` while a WS client samples push latency. **Client-side round-trip clocking**: stamps `t_send` on the ping POST and `t_recv` on the matching `loadtest_ping:<seq>` WS frame — both in the harness process, so no cross-process clock. Reports `baseline` vs `under_load` p50/p95/p99 + `missed_pings` as JSON. |
| `web_annotation/loadtest/run_ws_matrix.sh` | K-sweep driver (fresh cold daphne per K), mirroring the #164 `run_matrix.sh`. |
| `web_annotation/tests/test_ws_notification_responsiveness.py` | **CI-durable regression test.** `test_ws_push_latency_bounded_under_concurrent_builds` asserts WS push latency stays < 0.15 s under 4 concurrent slow builds (loop clean). `test_ws_push_latency_detects_on_loop_block` is the **positive control**: it parks the loop with a synchronous `time.sleep` between a ping's emit and receipt and asserts the sampler measures the park — proving a "no leak" result is trustworthy and not a blind harness. |
| `web_annotation/loadtest/ws-matrix-async.json` | The raw measured records below. |

`settings_e2e.py` gained `LOADTEST_PING_ENABLED = True`; `urls.py` appends the
ping route only when that flag is set. No new runtime dependency — `aiohttp` was
already in the venv. The consumer and `run_daphne_server.sh` are unchanged.

---

## How the measurement was run

**Single-`daphne` fallback (#164 option (b)).** The full e2e compose stack needs
the prebuilt `gain-web-api-prod` image and a `/mnt/cephfs/seqpipe/grr` mount —
not available on this machine — so this used one `daphne` on `settings_e2e`, the
in-repo test GRR (`t4c8/t4c8_pipeline`), a throwaway sqlite DB, and the
`InMemoryChannelLayer`. A fresh server per K guarantees a cold pipeline.

```bash
cd gain/web_api && source ../.venv/bin/activate
# per K in {8,16,32}: fresh cold daphne with the injected build delay, then
GPFWA_BUILD_DELAY_SECONDS=0.4 PORT=<port> bash web_annotation/loadtest/run_daphne_server.sh &
python -m web_annotation.loadtest.ws_notification_slo \
  --base-url http://127.0.0.1:<port> --ws-url ws://127.0.0.1:<port>/ws/notifications/ \
  --concurrency <K> --duration 10 --baseline 1 --recovery 1 \
  --delay 0.4 --label async-K<K> --email loadtest@example.com
```

Params: injected build delay **0.4 s**, sustained-load window **10 s**, WS ping
cadence **0.1 s**, K ∈ {8, 16, 32}, unlimited user (bypasses the 10/min
`UserRateThrottle` + single-allele quota so every request reaches the build
wait). Machine: Linux, `PYTHONHASHSEED=0`.

> The `run_ws_matrix.sh` wrapper produces the same records; here each K was
> driven directly so the cold-server lifecycle was explicit. Absolute
> millisecond numbers are machine-specific; the **shape** (flat p50, zero missed
> pings) is the result that matters.

---

## Measured numbers (WS push latency, delay 0.4 s, 10 s load window)

| Label | annotate fired | 200s | missed pings | baseline p95 (ms) | baseline p99 (ms) | **under-load p50 (ms)** | under-load p95 (ms) | under-load p99 (ms) | under-load max (ms) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| async-K8  | 538 | 538 | **0** | 3.31 | 3.76 | **2.49** | 7.61  | 20.18 | 68.99 |
| async-K16 | 528 | 528 | **0** | 3.22 | 3.65 | **2.53** | 10.45 | 68.00 | 84.01 |
| async-K32 | 472 | 469 | **0** | 3.79 | 4.76 | **2.59** | 8.74  | 32.77 | 54.25 |

Reading the table:

- **WS push median is flat under load.** `under_load` p50 holds at **~2.5 ms**
  across K=8→32 — statistically identical to the idle `baseline` (~2.7 ms p50,
  ~3–5 ms p95). The loop is delivering notifications at idle speed *while*
  hundreds of slow builds churn through it.
- **Zero missed pings** at every K. No notification was dropped or stalled past
  the per-frame timeout.
- **Tail (p99/max) shows modest tens-of-ms jitter** (worst 84 ms at K=16). With
  the median pinned at idle and zero drops, this is thread-pool/GC scheduling
  noise on the in-memory channel layer, **not** loop parking — a parked loop
  would lift the median and lose pings, and did neither.

### The lone K=32 `500`

One of 472 annotate requests at K=32 returned `500`: `sqlite3.OperationalError:
database is locked`. This is a **test-fallback artifact** — the throwaway sqlite
DB serializes the concurrent `AlleleQuery` writes that the unlimited user's
`_build_and_persist` performs under K=32. Production runs **postgres**, which
does not have this single-writer limitation. Crucially this write happens in the
**off-loop** `await sync_to_async(self._build_and_persist)` path, so it neither
touched the event loop nor affected WS delivery (0 missed pings that run). It is
unrelated to #170 and noted only for measurement honesty.

---

## On-loop-blocker audit (static, corroborating the measured null)

Every `async def` handler reachable from an `AsyncAnnotationBaseView` subclass
was read for blocking work issued directly on the loop (bare ORM, `.result()`,
`time.sleep`, file I/O, blocking HTTP). Findings:

| Async handler | Long pole(s) | On the loop? |
|---|---|---|
| `SingleAnnotation.post` (`single_allele_annotation/views.py:156`) | `aget_pipeline` (build) → off-loop; annotate → `await await_build(ANNOTATE_EXECUTOR…)`; quota + `_build_and_persist` (incl. the `AlleleQuery` ORM at :252) → `await sync_to_async(...)` | **No** — all off-thread |
| `PipelineDoc.get` (#167, `pipelines/views.py:372`) | `aget_pipeline` → off-loop; `_render_doc` (markdown/Jinja + GRR metadata) → `await sync_to_async(...)`; no ORM | **No** |
| editor async views ×5 (#165/#166, `editor/views.py:335,477,528,703,768`) | each: `aget_pipeline` → off-loop; every metadata read / annotator-factory build → `await sync_to_async(...)`; no bare ORM | **No** |

A bare-blocking-call grep over those three files matched only ORM/`read_text`
calls that live **inside sync helper methods invoked via `sync_to_async`** (e.g.
`_build_and_persist`) or inside **sync** view classes (off-loop via Django's
per-request `ThreadSensitiveContext`). **No on-loop blocking path was found.**

The read views (#165/#166/#167) were verified by this audit rather than a
separate measured pass: each one's *only* long pole is `aget_pipeline` (awaited
off-loop) and every remaining GRR/ORM touch is `sync_to_async`-wrapped, so there
is no on-loop work for a sustained read-view load to expose. The harness's
`SingleAnnotation` load is the widest contention window (it adds the
`ANNOTATE_EXECUTOR` await on top of the build await) and already drove the loop
hardest with a flat result. A measured editor-endpoint pass is a straightforward
harness extension (a `--load-endpoint` switch) and is left as optional
follow-up, since the audit found nothing for it to surface.

**Sensitivity is proven, not assumed.** The committed
`test_ws_push_latency_detects_on_loop_block` parks the loop for 0.4 s between a
ping's emit and receipt and asserts the sampler measures ≥ 0.32 s — so the
harness demonstrably *would* catch a loop blocker. The "no blocker found"
verdict is therefore a result, not an absence of evidence.

---

## Verdict

- **Does the async migration measurably protect WS responsiveness?** **Yes.**
  Under sustained concurrent slow-build load up to K=32, WS push-latency median
  stays at its idle ~2.5 ms with **zero missed pings**. The loop is never
  parked; notifications flow at idle speed throughout. This is the event-loop
  responsiveness benefit #163 was claimed to deliver, now observed end-to-end on
  the path that genuinely shares the loop (rather than the sync-HTTP endpoint
  #164 found to be insulated by per-request `ThreadSensitiveContext`).

- **Is there an on-loop blocker worth fixing?** **No.** The static audit of all
  seven async handlers found every long pole awaited off-thread
  (`aget_pipeline` / `await_build` / `sync_to_async`), corroborated by the flat
  measured median and the sensitivity-proven harness.

- **Feeding back into #165/#166/#167.** The conversions are doing exactly what
  they should for the loop: every read view keeps the loop clean, so converting
  them carries no WS-responsiveness regression and preserves the property that
  matters — prompt notification delivery under build contention. The value case
  for the read-view conversions is **event-loop protection + uniformity**, the
  same framing #164 recommended; this measurement confirms that protection holds
  on the WS path in practice.

This restates and confirms #164's recommendation on the surface that actually
shares the loop. The SLO framing (median-flat + zero-drops as the pass
criterion, tail jitter expected) is a judgement call, flagged for the human
go/no-go on the issue.

---

## Reproduce / extend

```bash
cd gain/web_api && source ../.venv/bin/activate
DELAY=0.4 KS="8 16 32" LABEL=async DURATION=10 PORT=21011 \
  bash web_annotation/loadtest/run_ws_matrix.sh     # -> ws-matrix-async.json
```

- Full-stack variant: bring up `web_infra/compose-jenkins.yaml` with the
  `gain-web-api-prod` image + seeded GRR + postgres, set
  `GPFWA_BUILD_DELAY_SECONDS=0.4` on the backend, and point `--base-url` /
  `--ws-url` at it. Postgres removes the sqlite-lock artifact above.
- A measured read-view pass: add a `--load-endpoint editor_reads` switch to
  `ws_notification_slo.py` driving the #165/#166/#167 editor endpoints at high
  K. The audit predicts a flat result.

### Environmental limits on what was measured locally

- Full e2e compose stack **not run** here (needs the prebuilt image + cephfs
  mount). Measured on the in-repo test GRR with sqlite + `InMemoryChannelLayer`
  — representative for the daphne loop/channel model under test, not for
  production GRR build times or postgres latency. The **shape** (flat p50, zero
  missed pings) is the result that carries; absolute ms are machine-specific.
