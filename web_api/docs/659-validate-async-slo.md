# #659 — Pipeline validation off the request thread: recorded run

**Issue:** iossifovlab/gain#659.
**Date:** 2026-08-04.

`POST /api/pipelines/validate` is anonymous and used to resolve resources and
build every annotator inline, in a synchronous DRF view. This change makes the
view async (adrf) and submits its two repository-touching costs — the
expansion-gate parse and the build — to a dedicated bounded pool
(`AnnotationMixin.VALIDATE_EXECUTOR`, 8 workers), each awaited via
`await_build`. The two GRR-free bounds from #635 (body size, declared-annotator
count) still run inline, before anything reaches a pool, so a refusal stays
cheap, and the expansion gate still refuses before the build is submitted.

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

For contrast, the bound that *does* stay inline — `_count_annotators`'
`yaml.safe_load` on a worst-case 64 KiB body — measures **126 ms**, touches no
GRR, and is what makes the refusal cheap. That is the trade recorded here, not
an oversight.

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
*synchronous* structure (sync `post`, parse and build called inline) but
**keeping** the `GPFWA_BUILD_DELAY_SECONDS` hook — without the hook the baseline
does no slow work at all and the comparison is meaningless. In-repo test GRR,
injected build delay **2.0 s**, per-request timeout **60 s**, sample interval
**0.1 s**, logged in as the unlimited load-test user. Runs interleaved
sync/async at each K so both see the same host conditions.

| K | sync p50 (ms) | sync **p95** (ms) | sync max (ms) | async p50 (ms) | async **p95** (ms) | async max (ms) |
|---:|---:|---:|---:|---:|---:|---:|
| 8   | 3.45 | 4.19   | 7.09   | 3.34 | 3.90 | 11.24  |
| 32  | 3.29 | 13.21  | 45.64  | 3.38 | 4.31 | 132.07 |
| 64  | 3.21 | 66.87  | 96.89  | 3.53 | 5.53 | 105.59 |
| 128 | 4.26 | 128.29 | 344.38 | 3.76 | 5.21 | 335.34 |

Zero timeouts and zero errors on the cheap endpoint in every run. The cheap
endpoint's p95 degrades ~30x with K on the synchronous path (4.2 → 128 ms) and
stays flat on the async one (3.9 → 5.2 ms): **25x better at K=128**, parity at
K=8 where there is no contention to speak of.

The sync runs also collect far fewer cheap samples (28-29 versus 86-296): the
sampler is itself a synchronous view, so on the baseline it is competing for the
same threads the builds occupy.

At K=128 both servers answered 120/128 — the missing 8 are `429`s from the
`pipeline_validate` throttle (120/min, #635), identical on both, i.e. the
throttle doing its job rather than anything this change caused.

## The cost, stated plainly

Bounding the pool bounds the slow endpoint's own throughput. Wall time for the
burst of K validations:

| K | sync | async (8 workers) |
|---:|---:|---:|
| 8   | 2.04 s | 2.05 s  |
| 32  | 2.15 s | 8.21 s  |
| 64  | 2.29 s | 16.18 s |
| 128 | 2.86 s | 30.54 s |

That is the trade being made on purpose. The synchronous path was fast at K=128
because Django's ASGI handler gives every HTTP request its own
`ThreadSensitiveContext` thread — so 128 anonymous requests bought 128 threads,
and the rest of the API paid for them (the p95 column above). The pool converts
that into queueing at a fixed cost.

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
