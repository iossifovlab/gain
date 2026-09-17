# A score read resolves its request on every call

`PositionScore.get_scores_at_position` resolves the ids it is asked for
through `_resolve_score_defs` on each call, and it stays that way. There
is no "definitions already resolved" entry on the score, and the
annotator's substitution branch keeps reading through the documented
point read rather than through `fetch_records` +
`get_score_values_from_record` with a list it resolved at construction.

The same holds for the three aggregating reads --
`PositionScore.get_scores_in_region_agg`,
`AlleleScore.get_allele_scores_in_region_agg` and
`FragmentScore.get_fragment_scores_overlapping_region_agg`. Each takes
the UNRESOLVED query list and resolves it inside the call; no resolved
request crosses the seam, and an annotator that resolves its constant
list at pipeline load does so for the refusal alone and throws the
answer away. See "The aggregating reads" below (gain#1300).

Nor does the fragment kind's query memo grow to remember MORE than the
resolved names -- not the aggregator classes and parameters a name
resolves to, and not the fold's column mapping. The memo remembers what
resolution answers, and the read builds the rest fresh per call. See
"Remembering what a name resolves to" below (gain#1172).

(The point read was `fetch_position_scores` when this was written;
gain#1268 removed it and made `get_scores_at_position` the only one. The
refusal is unaffected — the plane's `_region_read_defs` calls the same
`_resolve_score_defs` per read, so the resolution this entry declines to
hoist out of the call is still inside it. It is now performed *twice* per
point read, once in `get_scores_at_position` and once again inside
`fetch_region_segments_scores`; that duplication is a plain waste and is not what
this entry refuses, which is caching the result ACROSS calls.)

## Why this is out of scope

The proposal (gain#1193, filed from the efficiency review of gain#1194)
was to stop paying the per-call resolution in `PositionScoreAnnotator`,
whose source list (`simple_score_queries`) is fixed for the life of the
annotator: resolve it once in `__init__` and either bypass the point read
from the annotator or grow a pre-resolved variant of it on the score. The
review measured the resolver at ~0.40 µs of a ~2.7 µs substitution call,
about 15%.

The measurement is accurate and the remedy is refused, for three reasons.

**The 15% is a fixture number.** The ~2.7 µs call is the point read on the
test suite's in-memory table -- a few rows, no I/O, a linear scan. On the
backend annotation actually runs against the denominator is two orders of
magnitude larger and the resolver does not move:

| Backend, 5 scores                       | Point read | Resolver alone | Share  |
| --------------------------------------- | ---------- | -------------- | ------ |
| In-memory, 20 rows, sequential          | 2.6 µs     | 0.42 µs        | ~20%   |
| Tabix, 10k rows, sequential positions   | 31 µs      | 0.44 µs        | ~1.4%  |
| Tabix, 10k rows, random positions       | ~500 µs    | 0.46 µs        | ~0.1%  |

(Measured 2026-09-08 at gain `d9d6374e9`, `timeit` best-of-5 over 20k
calls, `a_position_score().with_tabix()`; the pre-resolved arm read
through `fetch_records` + `get_score_values_from_record`.) A sorted VCF
is the sequential row. A change that buys 1.4% of a 31 µs read is below
what the annotation pipeline's other costs per variant let anyone observe.

**There is no twin to match.** The issue offered the region read as the
precedent: "the region twin already resolves once at construction
(`resolve_aggregation_queries`)". It does not. The annotator's
construction-time call is a *validation*, so that a query naming no
aggregator for a `bool` score is refused as the pipeline loads
(gain#1131). The region read then calls the same resolver again on every
`get_scores_in_region_agg`, from its own `_resolve_aggregation_queries`,
and the score's docstring says why: building the read's state per call is
"what keeps a read thread-safe and an annotator stateless". The point
read already follows that rule. Pre-resolving would make it the odd one
out, not bring it in line.

**Both remedies cost more than they buy.** Reading through `fetch_records`
from the annotator duplicates the point read's body -- the contig check,
the drain-rather-than-abandon decision and the first-record rule are all
reasoning that lives on the read and would need a second home. Growing a
"defs already resolved" public entry on `PositionScore` adds a method to
the declared API surface (`test_score_resource_api.py` pins it) whose only
caller would be one annotator branch, for a saving no benchmark against a
real table can see.

## The aggregating reads

gain#1300 (split out of gain#1158) asked the same question of the three
folding reads: should a RESOLVED request be able to cross the seam, so
that `PositionScoreAnnotator`, `AlleleScoreAnnotator` and
`FragmentScoreAnnotator` -- each with one query list fixed for the life
of the annotator -- resolve it once at pipeline load and hand the result
to the read? If yes, `FragmentScore`'s per-instance query memo
(`_resolve_fragment_aggregation_queries`, its `_RESOLVED_QUERIES_BOUND`
and the three tests pinning the memo's mechanics) would go with it.

No, for the reasons above, and the measurement the issue asked for
confirms it is the same saving at the same size.

What a resolved request could save is the bare NAME resolution -- the
`score_def_for` / `resolve_aggregator_name` walk. It cannot save building
the accumulators: those are mutable, so the read builds them fresh per
call whatever the seam carries, and on the position kind that build is
most of what `_resolve_aggregation_queries` costs.

| Read, `max` on every score       | N   | Bare resolution | Whole read | Share |
| -------------------------------- | --- | --------------- | ---------- | ----- |
| Fragment, in-memory, 20 rows     | 5   | 2.7 µs          | 58 µs      | ~4.7% |
| Fragment, in-memory, 20 rows     | 20  | 8.7 µs          | 202 µs     | ~4.3% |
| Fragment, memo hit (as shipped)  | 20  | 2.7 µs          | 202 µs     | ~1.3% |
| Position, in-memory, 20 rows     | 20  | 5.5 µs          | 186 µs     | ~3.0% |
| Allele, in-memory, 20 rows       | 20  | 5.6 µs          | 166 µs     | ~3.4% |
| Fragment, tabix, 200 rows        | 20  | 7.6 µs          | 258 µs     | ~3.0% |
| Position, tabix, 200 rows        | 20  | 5.9 µs          | 278 µs     | ~2.1% |
| Allele, tabix, 200 rows          | 20  | 5.6 µs          | 246 µs     | ~2.3% |

(Measured 2026-09-16 at gain `ef302691a`, `timeit` min-of-7 over 2000
calls, a 200 bp region, `float` scores built with the testing builders
and `.with_tabix()` for the tabix rows.) The tabix rows are a 200-row
local file; a GRR table is block-gunzipped per seek and usually remote,
so the read there is milliseconds and the share drops under 1%. The
fragment memo already takes its kind from ~4.5% to ~1.3% on the fixture,
which is what the issue itself suspected: the remaining win is a tuple
construction and an `lru_cache` lookup, 1-3 µs per annotator call.

Against that, a resolved request crossing the seam is a second public
input type on three reads whose contract is "the request is checked when
this is called", and it moves the thread-safety rule -- fresh state per
call -- from the read, where it is stated once, into every caller that
opts in. The memo stays as the fragment kind's own, with its bound.

## Remembering what a name resolves to

gain#1172 (from the efficiency review of gain#1161, the PR that added
the fragment memo) asked the question one layer down. The memo remembers
`(requests, score_ids)` -- aggregator NAMES -- and the read then builds
each accumulator through `build_region_aggregators` →
`Aggregator.build` → `_resolve`, which is an `isinstance` and an
`lru_cache` lookup per aggregator before `cls(*params)`. Should the memo
instead remember what each name resolves TO, the `(class, parameters)`
pair, so the read builds `cls(*params)` directly? And, once it does,
should the remembered entry also carry `fold_region_segments`'s
`column_of` / `targets` mapping, which is pure over the same key and is
rebuilt per call?

No. This is the same remedy at the same size: a resolution cached across
calls, whose ceiling is the per-aggregator lookup it skips. Measured at
gain `23523d600`, `timeit` min-of-7, 20 `float` scores with `max` on
every one (four times the query count a real annotator carries), a
200-row table built with `a_fragment_score()`:

| Fragment read, 20 queries        | Whole read | Item 1 ceiling | Item 2 ceiling |
| -------------------------------- | ---------- | -------------- | -------------- |
| tabix, region touching 5 rows    | 77 µs      | 3.7 µs (4.7%)  | 2.2 µs (2.8%)  |
| tabix, region touching 40 rows   | 457 µs     | 3.6 µs (0.8%)  | 2.1 µs (0.5%)  |
| tabix, region touching 200 rows  | 2260 µs    | 3.0 µs (0.1%)  | 2.7 µs (0.1%)  |
| in-memory, 200 rows              | 1651 µs    | 3.7 µs (0.2%)  | 2.1 µs (0.1%)  |

"Item 1 ceiling" is `build_region_aggregators(requests)` minus
`[cls(*params) for cls, params in remembered]` -- the whole of what
remembering the pair could save, since the accumulator is built fresh
either way. "Item 2 ceiling" is the `column_of` / `targets`
construction. Both are ~0.1-0.2 µs per aggregator, and the 4.7% row is a
region that reads five rows off a local file; on a GRR table the read is
milliseconds and both shares are well under 1%.

Against that, the change has grown since it was filed.
`build_region_aggregators` is now shared by all three folding reads, so
a fragment memo that bypasses it spells aggregator construction a second
way for the one kind that already differs; `build_region_aggregator`'s
error wrapping (the `ValueError` naming the score and the resource) would
have to move into the memo with the resolution, or the refusal loses the
context that makes it useful; `Aggregator` would grow a public
`(class, parameters)` accessor beside the `resolve_class` it already
has; and the column mapping needs a new keyword on `fold_region_segments`
that only the memoised caller would pass. The memo remembers names; the
read builds from them.

## What is still in scope

**A real per-variant cost.** The refusal is of *this* saving at *this*
size. If a measurement on a tabix or bigWig table, at sequential
positions, shows the point read paying something that is not the backend
read itself, that is a different issue and it should quote the tabix
numbers rather than the fixture's.

**Measuring on the right denominator.** Efficiency-review percentages for
the score reads should be re-measured on `a_position_score().with_tabix()`
before they become follow-up issues. The in-memory table exists to make
tests fast, not to stand in for a GRR.

## Prior requests

- iossifovlab/gain#1193 -- "Position annotator point read: resolve the
  score definitions once, not per call"
- iossifovlab/gain#1300 -- "Should a resolved aggregation request cross
  the read seam, superseding the fragment query memo?"
- iossifovlab/gain#1172 -- "After #1161: the fold memo should remember
  aggregator classes, not names, and the fold should not rebuild its
  columns per call"
