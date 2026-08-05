# #659 — Pipeline validation off the request thread: recorded run

**Issue:** iossifovlab/gain#659.
**Date:** 2026-08-04.

**Re-measured 2026-08-05** against the final shape of the change (body parse
inline behind #676's declared-length bound; only the annotator count, the
expansion-gate parse and the build on the pool). The earlier figures in this
file's history described a shape that submitted the body parse too, and were
partly measuring the `pipeline_validate` throttle at K=128 — every K here stays
under the 120/min limit, so all requests are answered and nothing is throttled.

`POST /api/pipelines/validate` is anonymous and used to resolve resources and
build every annotator inline, in a synchronous DRF view. This change makes the
view async (adrf) and submits every cost that is not O(1) — the
declared-annotator count, the expansion-gate parse and the build — to a
dedicated bounded pool (`AnnotationMixin.VALIDATE_EXECUTOR`, 8 workers), each
awaited via `await_build`. The declared-length refusal (#676), the body parse
it bounds, and the config-size bound run inline. The order of the bounds is
unchanged: each still refuses before the work it bounds, and the expansion
gate still refuses before the build is submitted.

The build path also grew the same env-gated `GPFWA_BUILD_DELAY_SECONDS` hook the
pipeline cache's loader has (#164) — this endpoint never goes through the
cache, so without it the harness could not induce a slow *validation* build.
Default `0.0`, i.e. a true no-op.

## Why the expansion-gate parse had to move too

On a synchronous view, "inline" is a busy worker thread. On an async view it is
the **event loop**, which is worse: the loop serves every other request the
process is handling, and CPU running on it cannot be preempted the way a
GIL-holding thread is.

The gate parse resolves no resource, but each wildcard it expands scans the
whole repository once (`AnnotationConfigParser.query_resources` iterates
`grr.get_all_resources()` per wildcard). Measured on this host against a
production-scale GRR — `/data/grr/grr_encode`, **7922** `position_score`
resources — with `AnnotationConfigParser.parse_str`, best of three:

| config | cost |
|---|---|
| 1 wildcard matching 1 resource | **27 ms** |
| 100 wildcards (= `MAX_ANNOTATORS`), each matching 1 | **1.59 s** |

So a single anonymous request at the declared-annotator bound would have parked
the loop for ~1.6 s. It is now awaited on the same bounded pool as the build.

## ...and so did the declared-annotator count

The first recorded run kept `_count_annotators` inline on the strength of a
**126 ms** worst-case measurement. That number was measured on a body shaped
the way a *pipeline* is; it is not the worst case, because yaml's cost per byte
depends on the shape and `MAX_CONFIG_LENGTH` is the only thing bounding the
shape. Re-measured with the repo's venv, best of three, all bodies legal (under
64 KiB, so the size bound does not refuse them):

| 64 KiB body | `yaml.safe_load` |
|---|---|
| `- [1,2,3,4,5]\n` × 4681 (65 534 chars) | **555 ms** |
| `annotators: [{a: 1, b: 2}, …]` × 4800 (62 413 chars) | **473 ms** |
| alias-heavy mapping, 8 000 aliases (65 536 chars) | **208 ms** |
| 100 annotators (= `MAX_ANNOTATORS`), written by hand (2 828 chars) | 6 ms |

Half a second of un-preemptible loop time, for a request that is then *refused*
400, at the 120/min per-IP `pipeline_validate` rate — one anonymous client
inside its own budget. So the count goes to the same bounded pool; a refused
request now costs a worker slot for the length of one parse, which is the price
of not paying it out of the loop.

### What that buys, measured with real yaml (no injected sleeps)

K concurrent `POST /api/pipelines/validate` of the 65 534-char body above,
through the real endpoint under Django's `AsyncClient`, with a 20 ms heartbeat
coroutine ticking on the loop. "Loop unavailable" is the share of the burst
spent inside heartbeat gaps longer than 100 ms. Median of three runs:

| K | inline ticks | inline unavailable | pool ticks | pool unavailable |
|---:|---:|---:|---:|---:|
| 1  | 3  | 94.7% | 23 | 16.2% |
| 4  | 5  | 96.6% | 25 | 66.8% |
| 8  | 9  | 96.5% | 35 | 81.0% |
| 16 | 17 | 96.6% | 47 | 83.9% |

Inline, the loop gets one turn per request and is dead ~96% of the burst at
every K. On the pool it gets 4–8× as many turns, and at K=1 — one anonymous
client, which is what the throttle actually permits per IP — it is available
84% of the time instead of 5%.

**The residual, stated plainly:** this is pure-Python yaml, so at pool
saturation the loop thread still competes for the GIL against 8 CPU-bound
workers, and the *worst single gap* can then exceed the inline one (K=16
worst-gap ranged 1.09–1.87 s on the pool against 0.64–1.18 s inline, over the
same three runs). Threads make the loop *runnable*, not *scheduled*. Removing
that residual means bounding the parse's input rather than relocating it —
i.e. a smaller `MAX_CONFIG_LENGTH`, which #635 set deliberately and this issue
does not reopen — or a parser that releases the GIL. Recorded here so the next
person does not have to rediscover it.

### The same thing through daphne, three ways

Three daphne servers on this host from this one checkout — the pre-#659
**sync** view (port 21097), this branch with the count **inline on the loop**
(21098), and this branch with the count **on the pool** (21099) — no injected
build delay, driven by the #164 harness with `--target validate
--validate-config <the 65 534-char body>`, logged in as the unlimited
load-test user. Every request is refused `400` in all three, so the attacker
pays nothing in any of them. Cheap endpoint is `GET /api/version`:

| K | variant | cheap p50 (ms) | cheap **p95** (ms) | cheap max (ms) | samples | burst wall |
|---:|---|---:|---:|---:|---:|---:|
| 8  | sync (pre-#659) | 3.33 | 3028 | 4855  | 9  | 5.26 s |
| 8  | async, inline   | 3.27 | 3239 | 4981  | 8  | 5.00 s |
| 8  | async, pool     | 3.26 | 2930 | 4658  | 9  | 5.11 s |
| 16 | sync (pre-#659) | 3.25 | 6170 | 10280 | 9  | 10.37 s |
| 16 | async, inline   | 3.26 | 6089 | 9367  | 8  | 9.39 s |
| 16 | async, pool     | 3.15 | 5129 | 6036  | 10 | 10.24 s |

Two things to read off this, both worth saying out loud:

1. **The pool is the best of the three**, and the only variant that improves
   the tail (K=16 max 6.0 s against 9.4–10.3 s) — but by ~15%, not by an order
   of magnitude, and at K=8 the three are within noise of each other. With
   8–10 cheap samples per burst the p95 is nearly the max; treat the column as
   an indication, not a percentile.
2. **The pre-#659 synchronous view was no better.** The premise that a
   `thread_sensitive` worker thread kept the process responsive under this
   load does not survive measurement: a 0.55 s pure-Python parse holds the GIL
   whether it runs on the loop, on a `thread_sensitive` thread, or on a pool
   worker, and at K≥8 the process is GIL-bound in every variant. What the loop
   placement adds on top of that is a *hard* block rather than contention —
   which is what the in-process table above isolates, and why the fix is still
   the right one — but the dominant cost here is the parse itself.

The lever that would actually fix the adversarial case is the size bound, not
the placement: `MAX_CONFIG_LENGTH` is what decides how much yaml an anonymous
request can buy. #635 set it at 64 KiB with reasoning recorded in the code, and
this issue explicitly does not reopen it; #666 is where "does this endpoint
need to parse this at all" belongs.

## ...but reading the request body did NOT have to move

`MAX_CONFIG_LENGTH` bounds the config *string*, not the body the string is
parsed out of, and on `master` nothing bounded that body either:

- `DATA_UPLOAD_MAX_MEMORY_SIZE` (2.5 MB) is consulted by `HttpRequest.body`
  and `HttpRequest.POST`. DRF uses neither -- `Request.data` hands the raw
  stream to the negotiated parser, and `JSONParser` calls `json.load(stream)`
  with no size check.
- For multipart, Django bounds non-file field bytes and the file *count*
  (`DATA_UPLOAD_MAX_NUMBER_FILES`, 100). A file *part's* size is bounded by
  nothing.
- Under ASGI the client's upload speed does not bound it either: Django's
  `ASGIHandler.read_body` buffers the whole body into a `SpooledTemporaryFile`
  *before* dispatch, so the parse proceeds at disk speed and lands as one
  contiguous, un-preemptible burst.

Measured on this host through the real endpoint (timing `Request._parse` from
inside a patched handler), all answering the usual `200`:

| body | parse |
|---|---|
| multipart file part, 16 MB | **0.019 s** (1.17 ms/MB) |
| multipart file part, 128 MB | **0.147 s** (1.15 ms/MB) |
| multipart file part, 512 MB | **0.586 s** (1.15 ms/MB) |
| JSON, 16 MB | **0.045 s** (2.82 ms/MB) |
| JSON, 128 MB | **0.393 s** (3.07 ms/MB) |

An early version of this change put that parse on the pool with everything
else. #676 made that unnecessary by refusing on the declared `Content-Length`
*before* the parser is reached, which caps the parse's input at
`MAX_BODY_LENGTH` (512 KiB) -- about 1.5 ms of JSON at the rate above. A
bounded parse is not a long pole, so it runs inline on the coroutine and the
request makes one fewer trip through the queue.

This is why the ordering question resolved the way it did. `MAX_CONFIG_LENGTH`
cannot be the first thing that runs -- the string it bounds does not exist
until the body is parsed -- but a *raw-bytes* bound can, and that is what #676
added.

## Harness

`web_annotation/loadtest/cheap_endpoint_slo.py` gained `--target
{annotate,validate}` (`annotate` remains the default, so #164's records stay
comparable) plus `--validate-config`; `run_matrix.sh` forwards `TARGET`.

```bash
cd gain/web_api
# one fresh server per checkout, 2 s injected build delay
GPFWA_BUILD_DELAY_SECONDS=2.0 PORT=21099 \
  bash web_annotation/loadtest/run_daphne_server.sh &
python -m web_annotation.loadtest.cheap_endpoint_slo \
  --base-url http://127.0.0.1:21099 --target validate \
  --concurrency 64 --timeout 60 --delay 2.0 --label async-K64 \
  --email loadtest@example.com
```

## Measured: cheap `GET /api/version` under K concurrent slow validations

Two daphne servers on one host, started from the same checkout: port 21099 with
this branch's view, port 21098 with the same file reverted to the pre-change
*synchronous* structure (sync `post`, count/parse/build called inline) but
**keeping** the `GPFWA_BUILD_DELAY_SECONDS` hook -- without the hook the
baseline does no slow work at all and the comparison is meaningless. (`master`
cannot serve as the baseline for the same reason: it has the synchronous view
but not the hook.) In-repo test GRR, injected build delay **2.0 s**,
per-request timeout **60 s**, sample interval **0.1 s**. Runs are interleaved
sync/async at each K, with a 65 s gap between runs so the `pipeline_validate`
throttle window clears -- the bucket is cumulative per IP, and without the gap
the later K values measure the rate limiter rather than the server.

Every K stays under the 120/min limit: **all requests answered 200, no 429s,
no timeouts, no errors** on either side in any run.

| K | sync p50 | sync **p95** | sync max | async p50 | async **p95** | async max |
|---:|---:|---:|---:|---:|---:|---:|
| 8  | 8.03 | 61.49  | 248.19 | 7.96 | 35.30 | 51.75  |
| 32 | 6.92 | 57.15  | 141.70 | 6.84 | 13.35 | 107.38 |
| 64 | 5.98 | 96.66  | 226.13 | 6.59 | 16.20 | 383.13 |
| 96 | 9.21 | 153.85 | 507.83 | 6.86 | 13.63 | 336.26 |

(ms.)

The cheap endpoint's p95 **climbs with load on the synchronous path** (61 ->
154 ms) and **stays flat on the pool** (35 -> 13.6 ms): about **11x better at
K=96**. The async p95 *improves* as K rises, which is the giveaway -- the pool
caps how much work is ever in flight, so additional load queues instead of
landing on the server.

## The cost, stated plainly

Bounding the pool bounds the slow endpoint's own throughput. Wall time for the
burst of K validations:

| K | sync | async (8 workers) |
|---:|---:|---:|
| 8  | 2.46 s | 2.42 s  |
| 32 | 2.32 s | 8.23 s  |
| 64 | 2.55 s | 16.66 s |
| 96 | 3.11 s | 24.65 s |

The async column is `K / workers * delay` almost to the decimal (96 / 8 * 2 s =
24 s, measured 24.65 s). The sync column is flat at every K because the
synchronous view simply ran every build at once, on its own thread each.

**That is the trade, and it should be read as a trade rather than a win.** The
synchronous path drained a burst faster *by being unbounded*, and everything
else in the process paid for it -- that is the p95 column above. The pool
converts that into queueing at a fixed, chosen cost.

Note the burst column scales directly with the injected 2 s delay, which is a
stand-in, not a measured production build. Read it as `K / workers x build
cost`, not as an absolute latency.

## Pool width: why 8

Same K=96 burst, same 2 s injected build, one fresh server per width (the
executor is built at import), 120 s timeout:

| workers | cheap p50 | cheap **p95** | cheap max | burst wall |
|---:|---:|---:|---:|---:|
| 4  | 3.85 | 6.06  | 107.48  | 48.62 s |
| 8  | 3.50 | **5.55**  | 122.21  | 24.36 s |
| 16 | 3.98 | 14.55 | 1119.00 | 12.92 s |
| 32 | 3.68 | 78.07 | 1098.71 | 7.16 s  |

All 96 requests answered 200 at every width; no timeouts.

**Eight is a knee, and the knee is sharp in one direction.** Going 4 -> 8 halves
burst time *and* improves p95. Going 8 -> 16 -> 32 keeps halving burst time but
costs p95 2.6x then 14x, and the max jumps by an order of magnitude.

The limit is not core count -- the host has 32. A validation build is
Python-bound, so past a handful of workers the extra parallelism buys burst
throughput by taking GIL time from the loop thread that serves every other
request. Widening this pool spends exactly what the async conversion was for.

`AnnotationMixin.VALIDATE_POOL_WORKERS` holds the value and
`test_the_validation_pool_width_is_pinned` fails if it changes, because every
other assertion about the pool passes at any width.

**Caveat on precision:** this host was not idle (load average 7-19 from other
work during the runs), and each cell is a single run rather than a best-of-N.
The ordering is stable and the effect sizes are large, but treat individual
milliseconds as indicative.

## Note on #164's finding

#164 recorded that the "single shared daphne `thread_sensitive` sync-view
thread" premise does not hold at the HTTP layer, because Django's ASGI handler
wraps each request in its own `ThreadSensitiveContext`. That is why the win here
shows up as *bounded* rather than *serialized* occupancy: the sync baseline does
not queue the cheap endpoint behind one build, it degrades it by thread count.
The in-process regression tests
(`tests/test_pipeline_validation_async.py`) run under Django's async test
client, which does share one such thread — there the property is directly
observable, and each proof was verified red against the placement it rules out.
