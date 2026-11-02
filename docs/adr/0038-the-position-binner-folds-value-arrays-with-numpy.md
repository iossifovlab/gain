# 38. The position binner folds value arrays with numpy, bit for bit

- **Status:** accepted
- **Date:** 2026-10-16
- **Issues:** [gain#1841](https://github.com/iossifovlab/gain/issues/1841) (the
  measurement and the prototype),
  [gain#1957](https://github.com/iossifovlab/gain/issues/1957) (the fold),
  [gain#1969](https://github.com/iossifovlab/gain/issues/1969) (the `median`
  fold)
- **Amends:** [ADR 0025](0025-binning-tool-writes-one-hdf5-matrix-from-a-fixed-grid.md),
  decision D14. D14 kept `get_scores_in_bins` as the read of the position
  binner and did not file a vectorised fold. This record files the fold.
  `get_score_in_bins` stays the semantic reference for the bins.

## Context

D14 measured a fold cost of about 0.55 µs per record and recorded a trigger
to re-open the question. After gain#1843, the tabix array read parses each
batch with the pandas C parser. The position binner did not get faster,
because `PositionScoreBinding.bin_region` did not read the arrays.

gain#1841 measured the binner again on `pooh`, at gain `29a04a194`. The data
was chr21 of `phastCons100way-chr21-tabix` and `phastCons100way-bw` from
grr_bench: 37,211,610 records in each resource. The bin size was 10,240 and
the aggregator was `mean`. The tables give the median of 3 timed passes.

| Resource | Rung | µs per record |
| --- | --- | ---: |
| tabix | `fetch_region_segments_scores` | 1.996 |
| tabix | `get_scores_in_bins` | 2.950 |
| tabix | `fetch_region_value_arrays` | 1.344 |
| bigWig | `fetch_region_segments_scores` | 0.450 |
| bigWig | `get_scores_in_bins` | 1.331 |
| bigWig | `fetch_region_value_arrays` | 0.362 |

The fold overhead was 0.954 µs per record on tabix and 0.880 µs per record
on bigWig. That is 32.3% and 66.2% of the binned time. A py-spy profile of
`get_score_in_bins` gave this split:

| Resource | Read | Parse | Fold |
| --- | ---: | ---: | ---: |
| tabix | 32% | 35% | 32% |
| bigWig | 27% | 5% | 66% |

A prototype fold over `fetch_region_value_arrays` did the same work in
1.270 µs per record on tabix and 0.408 µs per record on bigWig. These are
2.25 times and 3.23 times faster than `bin_region`. In the prototype, the
fold was 2% of the tabix time and 7% of the bigWig time.

The prototype bins were identical on bigWig. On tabix, `mean` and `sum`
differed by up to 2.3e-13. The prototype added each batch with
`np.add.reduceat` and then added the batch totals. That order of additions
is not the order of the aggregators.

## Decision

`PositionScoreBinding.bin_region` folds the batches of
`fetch_region_value_arrays` with numpy (`gain.binning.position_fold`) when
all of these conditions are true:

- The score serves the array read: `supports_region_value_arrays` is true.
- The value type of the score is `float`.
- The aggregator is `count`, `sum`, `mean`, `max`, `min` or `median`.
- The `none_value_replacement` is exact as a float sum over one bin:
  `replacement_is_exact` is true. The Consequences section gives the
  reason.

In all other cases, `bin_region` folds `get_score_in_bins`, as before. The
maintainer decided three points on 2026-10-16.

### 1. The bins are bit-identical

The array fold gives the bins of `get_score_in_bins` bit for bit. A
tolerance is not accepted, because the CLI tests compare `/values`
exactly, and two runs of one track must give one matrix.

The `sum` and `mean` aggregators add `value * length` once for each part of
a run, from left to right, from the start of the bin. The run encoder does
not merge adjacent records with equal values. The array fold does the same
float operations in the same order:

- `np.add.reduceat`, `ndarray.sum` and `np.sum` use pairwise summation, so
  the fold does not use them for a sum.
- The fold adds one part at a time for each bin. It does one numpy step for
  each part rank, across all bins. When a bin has more parts than the batch
  has bins, it calls `np.add.accumulate` along each bin instead. Both add
  in sequence.
- An open bin at the end of a batch continues its running sum in the next
  batch. The fold does not add batch totals.
- `sum` starts its accumulator at `-0.0` and `mean` at `0.0`, so a signed
  zero comes out as the aggregators give it. `max` and `min` keep the tie
  rule of the Python built-ins for the two zeros.

The parity test has a bin of 20 parts where `np.add.reduceat` gives another
sum. The test asserts that difference, so the test is not vacuous for the
order of the additions.

### 2. An `int` score keeps the present fold

The array read gives an `int` score as `float64`. The aggregators keep an
`int` sum exact past 2**53, and an `int` `sum` past the float range stores a
signed infinity. The array fold cannot give these results, so an `int`
score keeps the present fold.

### 3. `product` keeps the present fold

The `product` gap is not a permanent choice. An open issue tracks it:
gain#1970 vectorises `product`. gain#1969 closed the `median` gap. The
section below records its decisions.

## The `median` fold (gain#1969)

`MedianAggregator` keeps one `(value, weight)` pair for each part of a run
with a value. The weight is the length of the part. At the end, it sorts
the pairs by value with the stable `list.sort` and selects the middle ranks
in the weight-expanded order. The maintainer decided three points on
2026-11-02.

### 4. A bin with a NaN value goes to `MedianAggregator`

A NaN value is a value (see the Consequences section). The fold gives a bin
with a NaN value to `MedianAggregator` itself. It adds one pair for each part
of the bin that has a value, in position order, as `get_score_in_bins` does.
The parts that the replacement fills are in that order too. The fold adds
each value as a Python `float`, so an overflow in `(first + second) / 2`
gives no numpy warning.

The reason: Python sorts a list with a NaN in an order that depends on the
input, and a numpy sort puts NaN last. A probe on `MedianAggregator` gave
`nan` for the pairs `[(3.0, 1), (nan, 1), (1.0, 1)]`. A numpy weighted median
gave `3.0` for the same pairs. A numpy fold cannot give the Python order,
and NaN values are rare.

### 5. A bin without a NaN value uses a vectorised weighted median

The fold sorts the parts by bin and by value with a stable sort. Then it
searches the cumulative weights of each bin for the middle ranks.

The reason: a stable sort keeps two equal values in record order, as
`list.sort` does. So `-0.0` and `0.0` come out as `MedianAggregator` gives
them. The probe confirmed this for `[(-0.0, 1), (0.0, 1)]` and
`[(0.0, 1), (-0.0, 2)]`.

### 6. The fold keeps pairs only for the open bin

The open bin is the bin that holds the cursor of the fold: the first
position that no record claimed yet. Every bin before the open bin is
closed, because the next batch starts at the cursor. The fold computes the
median of each closed bin at the end of the batch. Only the pairs of the
open bin carry into the next batch. When the cursor is on the first
position of a bin, the open bin has no pairs.

The reason: one region can be a whole chromosome. All the pairs of chr1
would take several GB, and the binner runs several jobs in parallel.

The `replacement_is_exact` gate applies to `median` as it is. Under the
gate, `float(r)` equals an `int` replacement `r`. So the binner gets the
same float in each of these cases:

- An odd total weight selects `r`.
- A float value and `r` are the two middle values.
- Two middle values are `r`.

## Consequences

- On chr21, the position binner is expected to run 2.25 times faster on
  tabix and 3.23 times faster on bigWig. The maintainer runs
  `grr_bench/scripts/time_binning.py` again after the merge.
- After this fold, the read and the parse are the cost. On tabix, the line
  join and the pandas C parser took 57% of the prototype time.
- The array read refuses a contig that the score does not have. The fold
  does not call the read for such a contig, and folds it as one uncovered
  run (gain#1211).
- An `int` replacement keeps the array fold only while the replacement
  times the bin size stays within 2**53. The aggregators keep an `int`
  replacement exact, and the fold adds it as a float. The two agree in
  that range. A larger replacement keeps the present fold.
- The array read gives `nan` both for a missing value and for a cell that
  parses to a NaN value. An example of a NaN value is the text `NaN`, which
  the case-sensitive default `na_values` do not hold, or a NaN payload in a
  bigWig file. The present fold adds a NaN value, and a missing record only
  claims its positions. So the binner reads the parse that
  `fetch_region_value_arrays` is made of
  (`GenomicScore._parsed_column_batches`), which also gives the raw cells.
  For each `nan` value, `parse_value` of its cell decides: `None` is a
  missing value, and NaN is a value. Only the `nan` cells cost a scalar
  parse.
- Two folds now give the bins of a position track. The parity tests in
  `core/tests/small/binning/test_position_fold.py` pin their agreement. A
  change to the aggregators or to `get_score_in_bins` must keep those tests
  green.
