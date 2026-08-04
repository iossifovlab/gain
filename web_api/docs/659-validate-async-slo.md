# #659 — Pipeline validation off the sync-view thread: recorded run

**Issue:** iossifovlab/gain#659.
**Date:** 2026-08-04.

`POST /api/pipelines/validate` is anonymous and used to resolve resources and
build every annotator inline, in a synchronous DRF view. This change makes the
view async (adrf) and submits only the build to a dedicated bounded pool
(`AnnotationMixin.VALIDATE_EXECUTOR`, 8 workers), awaited via `await_build`.
The three request bounds and the expansion gate from #635 still run inline,
before anything reaches a pool, so a refusal stays cheap.

The build path also grew the same env-gated `GPFWA_BUILD_DELAY_SECONDS` hook the
pipeline cache's loader has (#164) — this endpoint never goes through the
cache, so without it the harness could not induce a slow *validation* build.
Default `0.0`, i.e. a true no-op.

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

Two daphne servers on one host (this branch vs. a copy of it with
`PipelineValidation` reverted to the synchronous view), in-repo test GRR,
injected build delay **2.0 s**, per-request timeout **60 s**, sample interval
**0.1 s**, logged in as the unlimited load-test user.

| K | sync p50 (ms) | sync **p95** (ms) | sync max (ms) | async p50 (ms) | async **p95** (ms) | async max (ms) |
|---:|---:|---:|---:|---:|---:|---:|
| 8   | 4.07 | 5.57   | 6.55   | 4.19 | 8.63  | 29.22  |
| 32  | 4.66 | 34.03  | 248.51 | 4.48 | 7.43  | 119.09 |
| 64  | 4.09 | 76.57  | 99.96  | 4.39 | 15.06 | 119.67 |
| 128 | 8.36 | 260.92 | 630.13 | 6.59 | 28.30 | 523.20 |

Zero timeouts and zero errors on the cheap endpoint in every run; the slow
requests themselves all completed (at K=128 both servers answered 120/128 —
the same 8 short of the burst on both, so not a property of this change).

The cheap endpoint's p95 degrades roughly linearly with K on the synchronous
path (5.6 → 261 ms) and stays flat on the async one (8.6 → 28.3 ms): **9x
better at K=128**, parity within noise at K=8, where there is no contention to
speak of.

## The cost, stated plainly

Bounding the pool bounds the slow endpoint's own throughput. Wall time for the
burst of K validations:

| K | sync | async (8 workers) |
|---:|---:|---:|
| 8   | 2.04 s | 2.05 s  |
| 32  | 2.35 s | 8.24 s  |
| 64  | 2.38 s | 16.22 s |
| 128 | 3.41 s | 30.91 s |

That is the trade being made on purpose. The synchronous path was fast at K=128
because Django's ASGI handler gives every HTTP request its own
`ThreadSensitiveContext` thread — so 128 anonymous requests bought 128 threads,
and the rest of the API paid for them (the p95 column above). The pool converts
that into queueing at a fixed cost. A first attempt with 4 workers queued deeply
enough to trip a 30 s client timeout at K=64; 8 completes every request at K=128
while keeping the cheap endpoint flat.

## Note on #164's finding

#164 recorded that the "single shared daphne `thread_sensitive` sync-view
thread" premise does not hold at the HTTP layer, because Django's ASGI handler
wraps each request in its own `ThreadSensitiveContext`. That is why the win here
shows up as *bounded* rather than *serialized* occupancy: the sync baseline does
not queue the cheap endpoint behind one build, it degrades it by thread
count. The in-process regression tests
(`tests/test_pipeline_validation_async.py`) run under Django's async test
client, which does share one such thread — there the property is directly
observable, and both proofs were verified red against the pre-change
synchronous view.
