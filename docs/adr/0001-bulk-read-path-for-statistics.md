# 1. A specialized bulk read path for the statistics scan

- **Status:** accepted
- **Date:** 2026-07-24
- **Issues:** [gain#385](https://github.com/iossifovlab/gain/issues/385) (the scan), [gain#387](https://github.com/iossifovlab/gain/pull/387) (shipped), [gain#398](https://github.com/iossifovlab/gain/issues/398) (the table capability), [gain#405](https://github.com/iossifovlab/gain/issues/405) / [gain#409](https://github.com/iossifovlab/gain/issues/409) (the parse contract), [gain#420](https://github.com/iossifovlab/gain/issues/420) (this record)

## Context

`grr_manage` computes a resource's statistics — histograms, min/max — by scanning
every record of a genomic score. Until gain#385 there was exactly one way to read
a score region: the per-record path, which builds a `Record` per row and reads
values off it through a score line.

Profiling that scan (cProfile, a 10Mbp chr21 slice) showed it was **not
I/O-bound**. Reads were already sequential; the cost was Python object churn:

- **~62%** in the read/line stack — `Record` and score-line allocation, position
  properties, value extraction;
- **~32%** in the histogram's per-value accumulation.

A scan whose time goes to per-record allocation cannot be made materially faster
by touching I/O. It gets faster by not building the objects — which means reading
a region as **columns** rather than as rows, and accumulating with numpy rather
than one Python call per value.

## Decision

A second, specialized read path exists alongside the per-record one.

It is used **only by the statistics scan**, and only when every one of four
conditions holds:

1. the resource is a **`position_score`**;
2. every requested score has value type **`float`**;
3. the backend is **tabix or bigWig** — i.e. it declares `supports_value_arrays`;
4. the scan region is **bounded** — a concrete contig with concrete start and end.

Anything else keeps the per-record path, unchanged. There is no fallback *within*
the bulk path: eligibility is decided up front, and an ineligible scan never
enters it.

The path is built from three pieces:

- `GenomicPositionTable.get_region_value_arrays(chrom, pos_begin, pos_end,
  value_columns, batch_size)` — the backend-level read, yielding batches of
  `(pos_begin, pos_end, {column index: raw cells})` with no `Record` built. It is
  **optional**: the base class refuses with `TypeError`, and a backend that serves
  it both overrides the method and sets `supports_value_arrays = True`.
- `GenomicScore.fetch_region_value_arrays(...)` — the score-level facade, which
  adds the parse and hands back one `float64` array per score id.
- `GenomicScoreImplementation._bulk_region_scan` — the shared driver behind both
  the histogram and the min/max passes.

Two predicates guard it, and the split between them is deliberate:

- `GenomicScore.supports_region_value_arrays(scores)` answers what the **score
  facade** can do — the backend serves the array read *and* every named score is a
  float this facade can parse. It is answerable on an unopened score.
- `GenomicScoreImplementation._bulk_scan_eligible(...)` adds what is the
  **consumer's** condition and no one else's: that the resource is a
  `position_score`. The bulk accumulators assume position-score semantics; that
  requirement belongs to the statistics scan, not to the read facade, and is
  asked separately.

### Measured result

Bit-identical output at every step. On a chr21 slice:

| Increment | Effect |
| --- | --- |
| vectorized accumulation + batched region read | ~1.6x (tabix), ~2.1x (bigWig) |
| tabix raw-row fast path — no `Record` per row | ~3.0x |
| bigWig column-array fast path | ~9.5x |
| min/max pass, bulk-vectorized | ~1.9x |

The intermediate figures are the important part of the story: the first increment
reached only 1.6x because it still drew from `get_records_in_region`, and
building a `Record` per row was ~70% of the remaining bulk cost. The speedup came
from deleting the per-record object, not from the vectorized arithmetic. For
bigWig the same held even more sharply — the `pyBigWig` `intervals()` fetch was
only ~1.2s of ~15s; everything else was the record generator and per-interval
parse.

## Why it is restricted rather than general

Each exclusion has its own reason, and they are not the same reason.

**`allele_score` / `np_score` — accumulator semantics.** The bulk accumulators
assume one value per position with a span weight. These types carry several
weight-1 records per position, which is a different accumulation, not a slower
spelling of the same one. (`np_score` is a deprecated alias of `allele_score`.)

**`cnv_collection` — accumulator semantics, differently.** Weight 1 rather than a
span weight.

**VCF — payload shape, not semantics.** The VCF backend subclasses the tabix one
and therefore *inherits a working implementation it cannot honour*: its record
payload is a `(variant, allele index)` pair rather than a raw row, and a VCF score
is an INFO field addressed **by name**, where this contract passes an integer
column index. So `VCFGenomicPositionTable` sets `supports_value_arrays` back to
`False` explicitly.

**Non-float scores — parse semantics.** The facade parses to `float64`. An `int`
score needs `int()` semantics, and `int("3.5")` raises where `float("3.5")` does
not; a `str` or `bool` score is not a number to accumulate at all.

**Unbounded scans — the overlap guard.** The bulk path needs a concrete contig for
its overlapping-position guard, and concrete bounds because that is what the
backend read takes. A whole-table scan keeps the per-record path.

## How the two paths are kept from drifting

This is the part most worth writing down. Two implementations of one computation
will diverge unless something forces them not to, and this decision rests entirely
on three mechanisms that do.

### 1. Bit-exactness is the governing contract

Not "close enough", not "statistically equivalent" — the bulk path must produce
byte-identical statistics, because a resource's statistics hash must not depend on
which path computed it. The same resource scanned with `--region-size 0`
(per-record) and with the default (bulk) must agree exactly.

This is gated by dedicated bulk-vs-per-record tests
(`test_histogram_scan_bulk.py`, `test_min_max_scan_bulk.py`,
`test_score_line_bulk_values.py`, `test_tabix_region_arrays.py`) covering
multi-score, `zero_based`, configured NA, sub-region clip, bigWig, and
batch-boundary overlap — plus the golden statistics tests
(`test_statistics_golden.py`).

### 2. Value parsing is one contract, with equivalence enforced

A scalar parser and a vectorized parser **cannot literally be the same function**.
So "one implementation" here means *enforced equivalence*: both forms hang off the
definition that owns the two inputs a parse needs (`value_parser` and `na_values`),
as `GenomicScoreDef.parse_value` and `GenomicScoreDef.parse_array`, so neither can
be changed against a config the other did not see. Their agreement is pinned by a
differential fuzz test, `test_parse_array_agrees_with_parse_value_fuzz`.

This mechanism exists because its absence caused a real, shipped-to-nobody bug:
`pd.to_numeric` silently diverged from `float()` in rounding, producing wrong
min/max, wrong histogram bars and a non-reproducible statistics hash that the
entire suite missed.

### 3. Capability is declared, not inferred

`GenomicPositionTable.supports_value_arrays` is a `ClassVar` a backend sets
explicitly. Callers **ask the flag; they do not test the class** — the capability
is not derivable from the class hierarchy, precisely because VCF inherits tabix's
implementation and must refuse it.

Probing by calling-and-catching does **not** work either: an unguarded call on a
VCF table reaches the inherited tabix implementation and trips its
`assert isinstance(self.pysam_file, pysam.TabixFile)`, yielding a message-less
`AssertionError` — and nothing at all under `python -O`.

The claim and the behaviour are held together by
`test_backend_record_contract.py`, which fails a backend whose declaration and
conduct disagree **in either direction**.

## Consequences

- There are two read paths to keep in step, forever. The three mechanisms above
  are the whole of what makes that safe; weakening any of them re-opens a class of
  silent, hash-visible wrongness.
- A new backend must decide `supports_value_arrays` deliberately. The contract test
  will fail it if it claims wrongly, but the *decision* is the backend author's.
- Extending the path to a new score type is not a matter of relaxing the gate. It
  requires accumulators that match that type's semantics — which is exactly the
  work gain#421 does for `allele_score`.
- Statistics output is unchanged. This decision bought throughput and nothing else;
  any observable difference in results is a bug, by construction.

## What it cost, honestly

Five adversarial review rounds, each of which found a real defect. **Two of the
last three were regressions introduced by fixes to earlier findings.** An account
of this change that omits that teaches the wrong lesson about how safe a
vectorization is.

The two worth naming:

- **`pd.to_numeric` is not `float()`.** It is not correctly rounded, so the bulk
  path produced different values than the per-record path — wrong min/max, wrong
  bars, and a statistics hash that did not reproduce. Found by an adversarial
  review going looking, not by the test suite.
- **`np.isin` is not the membership test it looks like.** The fix for the above
  replaced `pd.Series.isin` (hash-based, type-preserving) with `np.isin`, which
  coerces its second argument to a single dtype. `na_values` deliberately holds
  *both* representations of each sentinel, so `np.asarray({"-1", -1.0})` collapses
  to `array(['-1', '-1.0'], dtype='<U32')` — and it then broke in opposite
  directions: for text cells a stringified float sentinel became an NA token that
  `parse_value` never treats as one (real values silently dropped from the
  histogram); for float cells (bigWig) every sentinel became a string, so the
  comparison was **always False** and the `na_values` config did not apply at all —
  a permanent no-op no test pinned, which let a declared-absent value become the
  histogram's minimum.

Both reproduced end-to-end and both had exactly the property the original fix set
out to remove: the same resource giving different statistics under the per-record
path than under the bulk one.

The lesson is not "vectorizing is dangerous". It is that a second implementation of
an existing computation needs its equivalence **mechanized before** it is
optimized, because the failure mode is silent and the test suite you already have
will not see it.
