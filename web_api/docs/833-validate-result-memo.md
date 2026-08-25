# #833 — What one validate-build costs, and what the memo removes

**Issue:** iossifovlab/gain#833.
**Date:** 2026-08-25.

`POST /api/pipelines/validate` resolves every referenced resource and builds
every annotator, on every request. #666 decided that build stays: the editor's
green state promises "this config builds against the GRR", and nothing here
changes that promise. What this change removes is the *repeat* cost — the
editor validates on a debounce, so one session posts the same text many times,
and each of those repeats paid for a full rebuild.

The issue asked for the measurement first. This is it, then what the numbers
decided.

## The setup

A production-*shape* GRR, not a fixture. `gain-infra` renders
`templates/grr-definition.yaml.j2` into a `type: group` of `type: directory`
children over bind-mounted, read-only paths, so the measurement uses the same
shape:

```yaml
id: "bench"
type: group
children:
- {id: grr_encode, type: directory, directory: .../grr_encode, read_only: true}
- {id: grr,        type: directory, directory: .../grr,        read_only: true}
```

**8194 resources** (7922 of them `grr_encode` position scores). Host: this
build machine, warm page cache, `uv run --package gain-core`. Timings are the
best of three consecutive runs in one process, after the repository index is
warm — `grr.get_all_resources()` costs 0.79 s the first time and 0.000 s
thereafter, so index construction is a process-start cost and not part of a
request.

The two costs timed are exactly the two the view submits to its bounded pool
(`AnnotationConfigParser.parse_str`, the expansion gate; and
`load_pipeline_from_yaml`, the build). The declared-annotator count is a third
submission but is `yaml.safe_load` on a bounded body, already measured in
`659-validate-async-slo.md`.

## What one request costs

| config | expands to | gate parse | build | total pool work |
|---|---:|---:|---:|---:|
| `pipeline/hg38_clinical_annotation` — a real published pipeline, 13 annotators | 13 | 5.5 ms | 44.7 ms | **50.2 ms** |
| `- position_score_annotator: ATAC-seq/*` — one wildcard | 369 | 9.7 ms | 1046.4 ms | **1056.1 ms** |
| ten wildcards, `ATAC-seq/ENCSR0{0..9}*` | 29 | 77.1 ms | 143.1 ms | **220.2 ms** |

The plain case is what an editing session actually re-posts. The wildcard cases
are the legal worst end: `ATAC-seq/*` is a single, entirely ordinary
declaration that sits under `MAX_EXPANDED_ANNOTATORS` (500) and costs a second
of CPU per request.

## Where the build's time goes

Shares of one profiled build (`cProfile`, cumulative). cProfile inflates
absolute time, so these are read as proportions, not as milliseconds
comparable with the table above.

| phase | plain | `ATAC-seq/*` | ten wildcards |
|---|---:|---:|---:|
| config re-parse (incl. the wildcard scan, repeated) | 2.1% | 1.4% | **62.8%** |
| annotator construction | **87.1%** | **98.4%** | 36.8% |
|  …of which cerberus resource-config schema validation | 60.2% | 74.8% | 28.5% |
| resource resolution (`get_resource`) | 23.7% | 20.7% | 7.3% |
| `yaml.safe_load` of the config | 10.6% | 0.0% | 0.3% |

Two things stand out.

**Annotator construction dominates, and inside it the dominant single cost is
cerberus schema validation of each resource's own config** — 60% of a plain
build, 75% of the wildcard one. That is per-resource work repeated identically
on every build that touches that resource.

**A wildcard config pays its repository scan twice.** The view parses the
config to gate the expansion (`_aparse_config`), then `load_pipeline_from_yaml`
parses it again. With ten wildcards that second scan is 63% of the build. It is
not a bug introduced here — it is why the gate parse is kept separate (see the
`post` docstring) — but it is why the wildcard row's build is so much larger
than its gate.

## Invalidation: what a cached verdict can actually go stale against

The issue required this be answered explicitly, so it was measured rather than
assumed. A probe built a pipeline against a scratch directory GRR, rewrote the
resource's `genomic_resource.yaml` on disk so the config's score no longer
existed, and rebuilt **in the same process**:

```
build 1 (resource declares score_a): OK
build 2 (resource now declares score_b, same process): OK        <-- unchanged
build 3 (fresh repo object, score_b): FAILED (…'score_a' is not supported…)
```

**A live gain process does not see a GRR content change at all.** The
repository object memoises the resources it has resolved for the life of the
process, and nothing in `web_api` calls
`GenomicResourceRepoProtocol.invalidate`; the GRR itself is a module-level
singleton built at import (`annotation_base_view.GRR`). So the endpoint's
verdict is *already* pinned to the process's snapshot of the repository, and a
memo keyed to that same snapshot adds no staleness over the build it replaces.

So the honest statement is that **neither bound is load-bearing today** — the
memo is exactly as stale as the build it replaces, for the measured reason
above. Both exist for the day that stops being true, and they catch different
things:

- **Repository generation** catches the repository *object* being replaced.
  An entry is only ever returned to a caller holding the same GRR it was
  computed against; handed a different one, the memo drops everything. In
  `web_api` today nothing replaces that object (it is a module-level
  singleton built at import), so this fires only in tests.

- **TTL**, `VALIDATION_CACHE_TTL_SECONDS`, five minutes. This catches content
  moving *under* an unchanged object — which the generation key provably
  cannot see, identity being unchanged in that case. And that is the shape
  the real risk has: the GRRs are bind-mounted directories grr-sync rewrites
  while the server runs, and `invalidate()` exists on the protocol. So the
  TTL is the operative bound of the two, not a backstop to it.

Both are tested (`tests/test_validation_cache.py`).

The mechanism that would make this exact against in-place invalidation is a
monotone generation counter on `GenomicResourceRepoProtocol`, bumped by
`invalidate()`, so a cache could key on `(repository, generation)`. That is a
gain-core protocol change and out of scope here. Worth noting that
`LRUPipelineCache` — which holds whole *built* pipelines against the same GRR
for the life of the process, with no staleness key at all — has the same
exposure and no bound, which is the sign this belongs on the repository
rather than on one endpoint's memo.

## What was implemented, and what was not

The issue named two candidates and made the second conditional on what the
first left behind.

**Implemented — the bounded result memo.** Validation needs only the outcome,
so an entry is a sha256 digest of the config text mapped to the `errors`
string. Bounded at `VALIDATION_CACHE_SIZE` (256) entries, LRU. A repeat of any
config in the table above goes from its row's total to a dictionary lookup.

Both halves of an entry have to be bounded for the entry count to bound
memory, and only one of them is naturally. The digest is fixed-size; the
verdict is not, because the annotator-configuration message echoes the
resource id back — measured through the endpoint, a 60 KB config yields a
**60112-character** `errors` string, so 256 of those would be ~15 MB of
attacker-chosen text held for the TTL. Hence
`ValidationResultCache.MAX_VERDICT_LENGTH` (4 KiB, against real messages in
the tens to low hundreds of bytes): a verdict past it is returned to the
caller unchanged but not remembered, so such a config is simply rebuilt next
time.

The digest is also `surrogatepass`, and both halves of that matter. It has to
be **total**, because `json.loads` accepts a lone surrogate and DRF's
JSONParser passes it through — so a two-byte ASCII body can put a str in
`request.data` that plain `encode("utf-8")` refuses, and since the digest is
the first thing on the path that encodes the config, that raise would be an
anonymous 500 for a config the endpoint previously answered 200. And it has to
stay **injective**, which `replace` and `ignore` are not: a lossy encode maps
distinct texts onto one key, and a memo that does that answers one config with
another config's verdict.

The memo sits after every bound that refuses from the request alone — so a
malformed request still gets its own accurate 400 — and *before* the admission
check, so a hit is neither charged a pool slot nor shed by
`MAX_VALIDATIONS_IN_FLIGHT`. Only the 200 verdicts are remembered; the four
400 refusals are properties of the request and the 503 is a property of the
moment.

**Not implemented — shared resource-resolution reuse.** The issue said to
price it and implement it only if the memo left a dominant cost on the table.
It does leave one: the 60–75% of every build that is cerberus schema
validation of resource configs is untouched for a config the memo has not
seen, and reuse would cut it across *different* configs too, not only repeats.
But that work belongs in gain core's resource layer rather than in `web_api` —
the issue's own framing is that the memo wraps *around* `load_pipeline_from_yaml`,
"not inside it" — so it is filed as **iossifovlab/gain#886** rather than
folded in here.

**Not implemented — in-flight coalescing.** Two concurrent POSTs of the same
*unseen* text both miss, both build, and both take a pool slot; only the
second one to *finish* leaves a verdict behind. A debounced editor that
outruns one build produces exactly that. So "the repeat cost of a session
collapses" is a claim about sequential repeats, which is what a debounce
mostly produces. Coalescing them would mean sharing one in-flight build
between callers — which is what `LRUPipelineCache` already does for the
pipeline-id path, and a different piece of machinery from a result memo. Not
in #833's scope; worth knowing before reading the numbers as a bound on
concurrent work.

## A note for whoever re-runs the #659 numbers

The memo changes what a burst of identical validations measures. Anything that
fires N requests carrying one config text now measures one build and N-1
lookups. `web_annotation/loadtest/cheap_endpoint_slo.py` therefore gives each
request a distinct config (a trailing yaml comment, which the parser discards,
so every request still builds the identical pipeline).

Note that the distinguishing text carries **both** a per-request index and a
per-run nonce, and the nonce is the load-bearing half. The recipe recorded in
`659-validate-async-slo.md` drives *one long-lived server* at K = 8, 32, 64,
96 with a 65 s gap between runs — every gap well inside the 300 s TTL. An
index alone restarts at 0 each run, so the K=32 run would send 8 hits and 24
builds, K=64 would send 32 hits, K=96 would send 64. Every request still
answers 200 and nothing in the emitted record distinguishes a build from a
lookup, so the harness would report a clean, wrong result. (`run_matrix.sh`
starts a fresh server per K and would not have shown it.)

The in-process proofs in `tests/test_pipeline_validation_async.py` do the same
through `an_unseen_valid_config()`. If you add a new proof that times a build,
use that helper — `test_the_burst_helper_still_buys_a_build_per_request` is
the guard, but it can only guard the helper, not a fresh literal.
