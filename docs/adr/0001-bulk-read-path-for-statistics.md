# 1. A specialized bulk read path for the statistics scan

- **Status:** accepted
- **Date:** 2026-07-24
- **Amended:** 2026-07-29 by [gain#421](https://github.com/iossifovlab/gain/issues/421), which widened the resource-type condition. The decision stands; what changed is *which* kinds it admits and *why* the others are out. See [Amendment — gain#421](#amendment--gain421-the-gate-reads-per-kind-facts-instead-of-assuming-them). The original scoping is left in place below, because why the path was first restricted is the part of this record worth keeping.
- **Amended:** 2026-07-30 by [gain#406](https://github.com/iossifovlab/gain/issues/406), which widened the value-type condition from `float` to `float`, `int` and `str`. See [Amendment — gain#406](#amendment--gain406-the-value-type-condition-becomes-a-pairing).
- **Issues:** [gain#385](https://github.com/iossifovlab/gain/issues/385) (the scan), [gain#387](https://github.com/iossifovlab/gain/pull/387) (shipped), [gain#398](https://github.com/iossifovlab/gain/issues/398) (the table capability), [gain#405](https://github.com/iossifovlab/gain/issues/405) / [gain#409](https://github.com/iossifovlab/gain/issues/409) (the parse contract), [gain#420](https://github.com/iossifovlab/gain/issues/420) (this record), [gain#421](https://github.com/iossifovlab/gain/issues/421) / [gain#406](https://github.com/iossifovlab/gain/issues/406) (the amendments)

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

1. the resource is a kind the bulk path is exercised against — a
   **`position_score`**, an **`allele_score`**, or a **fragment score** in either
   of its two permanent spellings (`fragment_score`, `cnv_collection`). *As
   originally decided this read "a `position_score`"; gain#421 widened it — see
   the Amendment.*
2. every requested score has value type **`float`**. *As originally decided;
   gain#406 widened it to `float`, `int` and `str`, paired with the histogram
   each can feed — see the [gain#406 Amendment](#amendment--gain406-the-value-type-condition-becomes-a-pairing).*
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
  adds the parse and hands back one array per score id — `float64` as
  originally decided, and since gain#406 the shape the score's value type
  parses to.
- `GenomicScoreImplementation._bulk_region_scan` — the shared driver behind both
  the histogram and the min/max passes.

Two predicates guard it, and the split between them is deliberate:

- `GenomicScore.supports_region_value_arrays(scores)` answers what the **score
  facade** can do — the backend serves the array read *and* every named score is a
  float this facade can parse. It is answerable on an unopened score.
- `GenomicScoreImplementation._bulk_scan_eligible(...)` adds what is the
  **consumer's** condition and no one else's: that the resource is a kind this
  scan is exercised against. That requirement belongs to the statistics scan,
  not to the read facade, and is asked separately. *Originally it asked for a
  `position_score`, because the bulk accumulators assumed position-score
  semantics. Since gain#421 they assume nothing — each kind states its own
  record semantics and both scan paths read them — so what this predicate still
  excludes is a deliberate list, not a structural limit. See the Amendment.*

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

*Two of the five below have since been lifted — the record of why they were
made is kept, with the change noted inline and explained in the
[Amendment](#amendment--gain421-the-gate-reads-per-kind-facts-instead-of-assuming-them).*

**`allele_score` / `np_score` — accumulator semantics.** The bulk accumulators
assume one value per position with a span weight. These types carry several
weight-1 records per position, which is a different accumulation, not a slower
spelling of the same one. (`np_score` is a deprecated alias of `allele_score`.)
— *Superseded by gain#421 for `allele_score`: the accumulators no longer
assume. `np_score` remains excluded, for a different reason; see the
Amendment.*

**`cnv_collection` — accumulator semantics, differently.** Weight 1 rather than a
span weight. — *Superseded by gain#421. `cnv_collection` is the legacy — and
permanently accepted — spelling of `fragment_score`
([0003-fragment-score-vocabulary.md](0003-fragment-score-vocabulary.md)); both
spellings are now admitted, and its weight-1 rule is stated by the kind rather
than assumed away.*

**VCF — payload shape, not semantics.** The VCF backend subclasses the tabix one
and therefore *inherits a working implementation it cannot honour*: its record
payload is a `(variant, allele index)` pair rather than a raw row, and a VCF score
is an INFO field addressed **by name**, where this contract passes an integer
column index. So `VCFGenomicPositionTable` sets `supports_value_arrays` back to
`False` explicitly.

**Non-float scores — parse semantics.** The facade parses to `float64`. An `int`
score needs `int()` semantics, and `int("3.5")` raises where `float("3.5")` does
not; a `str` or `bool` score is not a number to accumulate at all. — *Superseded
by gain#406 for `int` and `str`: the parse dispatches on the value type and the
gate pairs each score type with the histogram that can accumulate it. `bool`
remains excluded — no consumer asks for a column of them.*

**Unbounded scans — the overlap guard.** The bulk path needs a concrete contig for
its overlapping-position guard, and concrete bounds because that is what the
backend read takes. A whole-table scan keeps the per-record path.

## Amendment — gain#421: the gate reads per-kind facts instead of assuming them

*Added 2026-07-29. Everything above is the original record and is left as
written; this section says what is true now and what changed.*

### The problem was not the gate, it was where the rules were written

The restriction to `position_score` was honest when it was made: the bulk clip,
weight and overlap helper really did assume position-score semantics. But those
same rules were already stated a second time, in the per-record path — an
implementation override that pinned a fragment's weight to 1 and added a record
count, and an overlap check inside `PositionScore.fetch_region_values`. Two
statements of one rule is exactly the drift this ADR exists to guard against,
one layer up from the drift it was written about.

So gain#421 did not widen the gate by writing a second set of accumulators. It
moved each kind's record semantics onto the score class as two facts, and made
**both** scan paths read them there:

| fact | `PositionScore` | `AlleleScore` | `FragmentScore` |
| --- | --- | --- | --- |
| `RECORD_ORDERING` | `DISJOINT` — two records touching is a data error | `SHARED` | `SHARED` |
| `RECORD_WEIGHT_IS_SPAN` | `True` — one count per covered base pair | `False` | `False` |

A third fact was drafted and then dropped. `FragmentScoreImplementation` also
overrode the per-record min/max add to accrue a **record count**, which the bulk
path returned as 0 — so the first draft declared `RECORDS_ARE_COUNTED` to make
the two agree. Tracing the count to its origin
(`2adeb08fb`, Feb 2025, "Count is used with cnv collection implementations")
found it has **no consumer anywhere in the stack**: nothing reads
`MinMaxValue.count`, `MinMaxValueStatisticMixin.get_min_max_file` has no
callers, `MinMaxValue.serialize` is never invoked outside tests, and all four
deployed GRRs (`grr`, `grr_encode`, `grr_seqpipe`, `grr_sfari`, 272 resources)
contain **zero** `min_max_*.yaml` files. It was a producer feeding a serializer
that is never called, writing a file that is never created. Rather than teach a
second path to reproduce it, gain#421 removed the count outright —
from `MinMaxValue` as well as from both scan paths. `MinMaxValue.deserialize`
ignores a stray `count:` key rather than rejecting it.

The bulk path reads the flags per batch (it has no record to hand a per-record
hook); the per-record path reads the same flag per record; and
`GenomicScore._record_weight`, the weight the annotators' `aggregate_region`
applies, derives from `RECORD_WEIGHT_IS_SPAN` rather than restating it. The
defaults on `GenomicScore` are the "one record, one count" rule, so a kind that
declares nothing gets the conservative answer.

### The condition is now a list of exercised kinds

`_bulk_scan_eligible` admits `position_score`, `allele_score`, and a fragment
score in **both** its permanent spellings — `fragment_score` and
`cnv_collection` ([0003-fragment-score-vocabulary.md](0003-fragment-score-vocabulary.md)).
The set is built through
`equivalent_resource_types` rather than written out, because a literal naming
only one fragment spelling would send the other silently back to the per-record
path: no error, no failing test, just the slow path forever.

### What is still excluded, and why — the reasons have changed

- **`np_score` — scope, not semantics.** It is a deprecated alias of
  `allele_score` and builds an `AlleleScore`, so it would read with exactly the
  semantics now admitted. It is left out because **no production GRR has one**,
  so the bulk path is not exercised against it and is not opened to it
  untested. This replaces the original "accumulator semantics" reason, which was
  true when written and is not true now.
- **VCF-backed scores — payload shape.** Unchanged, and this is now what keeps a
  VCF-backed *allele* score on the per-record path, since its kind is otherwise
  eligible. The reason is the table's, not the kind's: a VCF record's payload is
  a `(variant, allele index)` pair, so `VCFGenomicPositionTable` declares no
  column-array support.
- **Non-float scores — parse semantics.** Unchanged; opening it is gain#406.
  — *Done; see that amendment below.*
- **Unbounded scans — the overlap guard and the read's bounds.** Unchanged.

### What it cost, and what pins it

The review of this change found **no correctness defect** — bulk and per-record
agreed across eighteen fixture/region comparisons, and seven deliberate
mutations all failed loudly. What it found was documentation drift (this ADR,
which asserted the opposite of the shipped code) and missing tests, which is the
same failure mode one layer up.

The gap worth naming: `_SCAN_BATCH_SIZE` is 100_000 and every fixture holds a
handful of records, so every new test ran in exactly **one batch** — while every
histogram bar is accrued once per batch and the overlap guard carries
`prev_right` across batches. A boundary bug was invisible by construction. `test_scan_bulk_allele_fragment.py` now forces batch sizes of
1/2/3/100 over five-record fixtures, clipped and unclipped, counts the batches
actually consumed (`batch_size` is a hint a backend may ignore), and pins the
degenerate shapes an average-looking fixture never reaches: an all-NA column,
an empty region, a single record, values outside `view_range`, and a multi-base
allele record on both the histogram and the min/max path.

## Amendment — gain#406: the value-type condition becomes a pairing

*Added 2026-07-30. Everything above is the original record and the gain#421
amendment, left as written; this section says what is true now.*

### The condition was never really about the value type

The original gate asked for `float` because the facade's column parse *was* a
float parse — one `astype(np.float64)` with no dispatch. What the scan actually
needs is narrower and more precise: **an array in the shape the histogram at
the other end can accumulate.** Stated as a value type, that rule was both too
strict (an int score's number histogram is float64 arithmetic either way) and,
had it simply been relaxed, too loose (a str score's column cannot go into a
`NumberHistogram`, and a *number* column cannot go into a categorical one).

So gain#406 replaces the single type test with a **pairing**, checked in
`_can_bulk_histogram`:

| histogram | value types | array the read yields |
| --- | --- | --- |
| `NumberHistogram` | `float`, `int` | `float64`, `nan` for no value |
| `CategoricalHistogram` | `str` | `object` of `str`, `None` for no value |
| `NullHistogram` | any | nothing is read |

`GenomicScoreDef.parse_array` dispatches on `value_type` to produce those two
shapes, and `supports_region_value_arrays` admits the three types it defines a
parse for. `bool` is still out: nothing asks for a column of them.

### Why the mismatched pairings are excluded, and it is not symmetry for its own sake

Both mismatches are configurations the per-record path already handles — by
**failing one value at a time**. `NumberHistogram.add_value("aaa")` raises
`TypeError`, `_do_histogram` catches it, that score is nullified and the rest
of the resource keeps its statistics. A batch is different in kind: a column of
`str` handed to `NumberHistogram.add_batch` is not a value it can refuse, it is
a coercion failure inside the accumulation. Routing either mismatch to the bulk
path would turn a nullified score into a raised scan — so the pairing keeps
them where they already work.

The categorical-over-`int` case is the one that looks like it should be
allowed, and is the more instructive: `CategoricalHistogram.add_value` counts
ints happily, so the per-record path builds that histogram. But the bulk read
yields an int column as `float64` — its non-value has to be a nan — and
`3.0` is not the key `3`. Admitting it would nullify a histogram that
currently works. It stays on the per-record path.

### What `add_batch` had to reproduce

`CategoricalHistogram` gained one, and its equivalence is the same contract
`NumberHistogram.add_batch` is held to — including the failures:

- the `HistogramError` message reports `UNIQUE_VALUES_LIMIT + 1`, because
  `add_value` tests after every single add and therefore always raises holding
  exactly one value too many. That string is not cosmetic: it is what the
  `NullHistogram` carries into the saved statistics, so a batch reporting its
  own overshoot would change a resource's recorded output.
- insertion order is preserved, because it decides which values a display
  truncation keeps.
- a refused batch raises before it accumulates anything.

### The int column's one honest divergence

`parse_array` yields an int score as `float64`, so values are exact to 2**53
and correctly rounded above it, where the per-record path keeps an
arbitrary-precision Python `int`. A number histogram widens to float for its
bin arithmetic either way, so the only place this is observable is a `min_max`
extremum past 2**53. `_accumulate_min_max` converts an int score's extremes
back to `int` so the serialized statistic keeps the spelling the per-record
path writes (`min: 3`, not `min: 3.0`).

### Measured result

Bit-identical output, pinned by the same bulk-vs-per-record comparisons the
float path has. On a synthetic 200k/400k-row tabix position score
(`_do_histogram` vs `_do_histogram_bulk`, whole region):

| score + histogram | speedup |
| --- | --- |
| `float` + number (control) | 2.9x / 3.0x |
| `int` + number | 2.9x / 2.9x |
| `str` + categorical | 1.7x / 1.8x |

The control reproduces the ~3.0x this ADR recorded for tabix, which is what
makes the other two rows comparable. `int` lands where `float` does — it is
the same accumulation once parsed. `str` gains less, and the reason is worth
recording: **there is no vectorized `str()`.** A text column is already the
objects the scalar parse would return, so `_parse_text_array` is a one-pass
Python copy rather than a numpy coercion, and `Counter` over a batch is only
modestly cheaper than `add_value` per record. What `str` still buys is the
`Record` per row, which is where this ADR's original profile put ~62% of the
cost — so 1.7x is the record-object saving alone, with none of the parse
saving the numeric types get.

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
`test_score_line_bulk_values.py`, `test_tabix_region_arrays.py`, and — since
gain#421 — `test_scan_bulk_allele_fragment.py`) covering multi-score,
`zero_based`, configured NA, sub-region clip, bigWig, batch-boundary overlap,
shared-position allele sites, fragment record counts and forced batch
boundaries — plus the golden statistics tests (`test_statistics_golden.py`).

One known gap in that last line: `test_statistics_golden.py` builds only
position-score fixtures, so nothing golden-pins the serialized statistics of an
allele or fragment score. It predates gain#421 and is not made worse by it, but
it is the one mechanism above that does not yet cover the kinds the gate now
admits.

### 2. Value parsing is one contract, with equivalence enforced

A scalar parser and a vectorized parser **cannot literally be the same function**.
So "one implementation" here means *enforced equivalence*: both forms hang off the
definition that owns the two inputs a parse needs (`value_parser` and `na_values`),
as `GenomicScoreDef.parse_value` and `GenomicScoreDef.parse_array`, so neither can
be changed against a config the other did not see. Their agreement is pinned by a
differential fuzz test, `test_parse_array_agrees_with_parse_value_fuzz` — and,
since gain#406, by `..._per_type`, which runs the same equivalence over an int
and a str token corpus built from the tokens the two types disagree on
(`"3.5"`, `"1e3"`, `"0x10"`, the int64 boundary, and the empty cell a str
score counts where a numeric one drops it).

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
  work gain#421 does for `allele_score`. *It did it by making the accumulators
  read the kind's semantics rather than by writing a second set of them; see the
  Amendment. The sentence still holds for whatever comes next: a kind that
  cannot state its record rules in those terms is not admitted by editing the
  gate.*
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
