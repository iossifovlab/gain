# 37. A fragment can also be binned by its overlap or by its coverage, and an entry can name its tracks

- **Status:** accepted
- **Date:** 2026-10-07
- **Issues:** [gain#1791](https://github.com/iossifovlab/gain/issues/1791) (the epic),
  slices [gain#1792](https://github.com/iossifovlab/gain/issues/1792) (the `name` key),
  [gain#1793](https://github.com/iossifovlab/gain/issues/1793) (the `value` and
  `aggregate` keys, the fragment-count default),
  [gain#1794](https://github.com/iossifovlab/gain/issues/1794) (the streaming fold
  engine, `/tracks.mode`, `/tracks.uncovered_value`),
  [gain#1795](https://github.com/iossifovlab/gain/issues/1795) (`fragment_length`),
  [gain#1796](https://github.com/iossifovlab/gain/issues/1796) (`coverage_profile`),
  [gain#1798](https://github.com/iossifovlab/gain/issues/1798) (the docs and this record)
- **Design:** `seqpipe/genomics-toolbox`
  `docs/2026-10-06-gain-fragment-binner-modes-design.md` (decisions F22–F32).
  The numbers below cite it. The statements are this record's own.
- **Amends:** [ADR 0035](0035-a-fragment-belongs-to-the-bin-of-its-start.md).
  ADR 0035 deferred overlap assignment, refused a replacement value for
  uncovered bases, and deferred a `name` key. This record keeps start
  assignment as the default, and adds the other decisions for two new modes.
  [ADR 0025](0025-binning-tool-writes-one-hdf5-matrix-from-a-fixed-grid.md)
  gets the layout amendment for `/tracks.mode` and `/tracks.uncovered_value`,
  and the D10 amendment for `name`.

## Context

ADR 0035 put each fragment in one bin: the bin that holds its start. That
gives the number of fragments per bin. It does not give these results:

- The quantity of signal in each base pair of a bin. A long fragment that
  crosses three bins adds to one bin only.
- The mean depth of coverage in a bin. That needs the coverage profile: at
  each position, the sum of the values of the fragments over it.
- A bin value that includes the bases without fragments, for example the
  mean depth over the full bin, where an uncovered base has depth 0.

A prototype (`iossifovlab/binning_tool_prototype`, module `b.py`) did these
operations as streaming folds. Only its author could run it, its output did
not align with the output of `binning_tool`, and it had no tests. The
prototype also split "what one fragment adds" from "how a bin reduces the
values". `binning_tool` kept both in one `aggregate` key.

## Decision

### Three modes (F22)

The key `aggregate.mode` has three values:

- `fragment_start` (the default): a fragment adds its value, with weight 1,
  to the bin that holds its start. This is ADR 0035, unchanged.
- `fragment_length`: a fragment adds its value to each bin that it
  overlaps. The weight is the number of base pairs of the overlap. Gain's
  aggregators already apply a weight in closed form
  (`Aggregator.add(value, count)`), so a weight costs no loop per base.
- `coverage_profile`: for each track, the tool first makes the coverage
  profile. The profile is a sequence of runs of equal value. The tool then
  bins the runs as `fragment_length` bins fragments. A position with
  profile value 0 is uncovered. With pooled resources, the profile of a
  group adds the fragments of every resource.

Mode, `value` and grouping are independent. Each mode operates with each
`value` form and each `group` form, pooled or unpooled.

### Overlap assignment clips each fragment to its region (F26)

ADR 0035 rejected overlap assignment for two reasons. Column sums stop
meaning "number of fragments", and a region boundary needs
de-duplication across tasks. The first reason is the purpose of the new
modes: a `fragment_length` sum is a number of base pairs, by design. The
second reason does not hold when the weight is the overlap:

- `fragment_start` continues to read
  `get_fragment_scores_starting_in_region`, so each fragment is seen in one
  region only.
- `fragment_length` and `coverage_profile` read
  `get_fragment_scores_overlapping_region` and clip each fragment to the
  region.

A base pair belongs to one region only, and its contribution depends only
on the fragments that cover it. Thus the regions stay independent, and
the task bundles of ADR 0025 (D13) also. No base pair is counted two
times. A test makes two adjacent regions give the same block as their
union. Other tests make the output file of each length-weighted mode byte
for byte the same for the default `--task-budget` and for a budget of 0.

### `uncovered_value` (F25)

`aggregate.uncovered_value` is the value of each base of a region that no
fragment of the track covers. The tool adds it with a weight of 1 bp for
each uncovered base.

| Mode | Default | Other values |
| --- | --- | --- |
| `fragment_start` | not applicable | refused at parse time |
| `fragment_length` | `null` (uncovered bases add nothing) | any finite number |
| `coverage_profile` | `0` (an uncovered base has depth 0) | `null` or any finite number |

When the value is a number:

- The tool fills only the positions of the region. This agrees with the
  edge-bin rule of ADR 0025 (D5).
- A contig that a resource does not have is uncovered throughout, and its
  bins get the aggregate of the uncovered value. This changes the
  absent-contig rule of ADR 0035 (F9) for these two modes only.
- No bin is empty, so `EMPTY_BIN_VALUES` does not apply.

When the value is `null`, the empty-bin rule of ADR 0035 applies unchanged.
NaN is refused, because `/tracks.uncovered_value` records `null` as NaN.

ADR 0035 refused `none_value_replacement` on the fragment kind. Its reason
was that fragments have no positions to be uncovered, only bins without
fragments. That reason is true for start assignment, and the refusal stays
for `fragment_start`. It is false for the two length-weighted modes: they
reduce base pairs, so an uncovered base is a real input of the bin. The
mean depth over the full bin needs depth 0 at each uncovered base, and
`fillna` on the matrix cannot give that mean afterwards. Thus the
`coverage_profile` default is `0`. The `fragment_length` default is `null`,
because there a background level is a choice of the analyst. The key has a
new name, and not `none_value_replacement`, because a position-score
replacement fills positions without a record. This key fills bases without
a fragment, and its default changes with the mode.

### The aggregators that the length-weighted modes refuse (F24)

`fragment_start` accepts the seven aggregators of `EMPTY_BIN_VALUES`, as
before. `fragment_length` and `coverage_profile` accept `sum`, `mean`,
`max`, `min` and `median` (`LENGTH_AGGREGATORS`). They refuse two, at parse
time:

- `count`, because with a weight it counts base pairs, not fragments. The
  message tells the user to give `aggregator: sum` with `value: {value: 1}`
  for the covered base pairs. Thus `count` always means a number of
  fragments.
- `product`, because with a weight it raises a value to the power of a
  length.

### The default counts the fragments that start in a bin (F28)

An entry without `value` and `aggregate` is `value: {value: 1}` and
`aggregate: {mode: fragment_start, aggregator: sum}`, for every resource.
#1793 made this change, and ADR 0035 records it in its amendment of F8.
This record repeats it only because the new modes rest on it. With one
default for every resource, pooled resources cannot disagree about the
default aggregate. The old `aggregate: {score | value, aggregator}` became
two keys (F23): `value`, what one fragment adds, and `aggregate`, how a
bin reduces the values. The old keys `aggregate.score` and `aggregate.value` are
refused, and the message names the `value` key. There is no compatibility
shim.

### The `name` key (F30)

Each entry of both binner kinds accepts an optional `name`. It replaces the
base of the entry's track names:

| Entry | Without `name` (unchanged) | With `name: N` |
| --- | --- | --- |
| fragment, `pool: true` | `<resource_query>:<group>` | `N:<group>` |
| fragment, `pool: false` | `<resource id>:<group>` | `N/<resource id>:<group>` |
| position score | `<resource id>` | `N/<resource id>` |

The D10 rule of ADR 0025 stays: when a name and group repeat, each track of
that set gets `:<aggregator>`. Names that still collide are refused at
parse time, and the message now tells the user to add `name`.

ADR 0025 (D10) and ADR 0035 deferred `name`, because the `:<aggregator>`
suffix separated every pair of tracks that a run could hold. With three
modes, that is false. Two entries can have the same resources, group and
aggregator, and differ only in mode, for example a `fragment_length` mean
and a `coverage_profile` mean. The suffix cannot separate them. A
rename of `/tracks.name` after the run cannot help a run that is refused
before it starts. A suffix for the mode was rejected. It would make each
track name longer to separate a rare pair, and the user knows better than
a suffix what the two columns mean.

### One streaming fold engine in `gain.binning` (F27)

`gain.binning.fragment_folds` holds the bin folds as streaming objects,
ported from the prototype's `b.py` and adapted to gain (typed, linted, with
the gain empty-bin rule):

- `BinFragmentAggregator`, with `feed(fragment)` and `flush()`. A fragment is
  `FragmentValue(start, end, value)`, and a bin output is
  `BinValue(start, end, value)`. A fold answers each bin as soon as no later
  fragment can reach it, and `flush()` answers the rest. Thus every bin of
  the region is answered once, in order, also for a track without
  fragments.
- `BinFragmentStartAggregator`, `BinFragmentLengthAggregator` and
  `BinFragmentCoverageAggregator`. The coverage fold puts a
  `CoverageProfile` in front of a length fold, as the prototype does.
- The folds import `EMPTY_BIN_VALUES` and do not state the empty-bin rule
  again.

`FragmentScoreBinding.bin_region` makes one fold for each track before the
read starts, and writes each bin output directly into the dense block of
the region. The HDF5 writer does not change. All three modes use this
engine, `fragment_start` also, so the binner has one code path. The binner
no longer calls `fold_into_bins`. `fold_into_bins` and
`FragmentScore.get_scores_in_bins` do not change, and the position-score
binner and the score API continue to use them. This changes F4 of the
earlier design ("one fold, two entry points") for fragments. A test keeps
the start fold of the binner equal to `get_scores_in_bins` for each
aggregator, so the two start paths cannot diverge without a failing test.

### Provenance and the chunk key (F29)

`/tracks` gets `mode` (UTF-8 text) and `uncovered_value` (float64, NaN for
`null`). A position-score track has an empty `mode` and NaN.
`Track.parameters` includes both values, so a change of either one makes a
new chunk key, and a rerun calculates the chunks again. `--dry-run` reports
the mode and the uncovered value of each fragment entry. ADR 0025 records
the layout change.

## Deferred

- **Midpoint and cut-site (Tn5) assignment.** They stay deferred, as in
  ADR 0035. Each is a choice of what the interval means, which the
  resource does not declare. Each can be a fourth value of `mode` without a
  change to the decisions above.
- **A numpy-vectorised fold.** The benchmark of gain#1797 decides if it is
  necessary. There is no performance gate.
- **Modes for `position_score_binner`.** It already weights by base pair.
- **A key to choose the separator or the form of `name`.**

## Consequences

- A fragment matrix now needs its mode to be read correctly. A
  `fragment_length` sum is a number of base pairs, and a `coverage_profile`
  mean is a depth. `/tracks.mode` records which.
- A `coverage_profile` track is 0, not NaN, where a sample has no
  fragments, and on a contig that a resource does not have. A user who
  wants NaN there gives `uncovered_value: null`.
- The length-weighted modes read every fragment that overlaps a region, so
  a region reads more records than in `fragment_start` mode. The cost per
  mode is measured in gain#1797, not here.
- The binner has its own start fold beside `fold_into_bins`. The
  cross-check test is what keeps the two equal. A change to one fold
  without the other fails that test.
- `name` is the answer to every collision of track names. The refusal
  message says so, so a user who bins one query under two modes is told
  what to add.
