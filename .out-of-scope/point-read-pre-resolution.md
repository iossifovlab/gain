# The point read resolves its score definitions on every call

`PositionScore.fetch_position_scores` resolves the ids it is asked for
through `_resolve_score_defs` on each call, and it stays that way. There
is no "definitions already resolved" entry on the score, and the
annotator's substitution branch keeps reading through the documented
point read rather than through `fetch_records` +
`get_score_values_from_record` with a list it resolved at construction.

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
