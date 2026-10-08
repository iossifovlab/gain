# Skipping `parse_array`'s defensive copy when there is nothing to mask

`GenomicScoreDef.parse_array` always returns a fresh array. Its float
branch copies a numeric column even when the score's `na_values` holds
no numeric sentinel. Then the NA mask is all `False`, and nothing writes
to the copy. GAIn keeps that copy. `parse_array` does not return its input
when the cells are already `float64`.

```python
# score_def.py, _parse_float_array: kept as is
if cells.dtype.kind == "f":
    values = cells.astype(np.float64, copy=True)
    values[self._na_mask(cells)] = np.nan
    return values
```

## Why this is out of scope

The maintainer declined the change at triage (2026-10-08).

**The win is small, and it is on a workload nobody waits on.** #459
measured 4.6 ns per record, 1.3%, end to end on `hg38/scores/phyloP100way`.
Over the deployed corpus that is about 6 minutes of a 10-hour statistics
rebuild. The bulk read serves the resource-statistics rebuild, and a GRR
pays that rebuild once per resource, at publish time. The maintainer
declined the per-base bigWig producer (`bigwig-values-per-base-read.md`)
and the float-parser swap (`bulk-scan-float-parser-swap.md`) on the same
grounds.

**The copy buys an unconditional contract.** Today `parse_array` returns a
fresh array for every input. Without the copy, the result is safe only
because of two facts in two other modules:

- Only `BigWigTable.get_region_value_arrays` yields `float64` cells.
  `TabixGenomicPositionTable.get_region_value_arrays` yields `dtype=object`
  cells, so tabix takes the text branch.
- A bigWig resource declares exactly one score
  (`bigwig_scores.validate_bigwig_scoredefs` refuses more). So two score ids
  cannot share one bigWig column, and the returned arrays cannot alias.

A change to either fact makes the skip unsafe, and no test near
`parse_array` would detect it. A 1.3% gain on a publish-time rebuild does
not justify that coupling.

## What was checked at triage

The premises in #459 were rechecked against `origin/master` on 2026-10-08.
They still hold, so a later request does not have to start from zero:

- #455 (pyBigWig `values(numpy=True)`) was closed as out of scope, so no
  new route delivers float cells.
- The consumers of the parsed arrays do not write to them:
  - `scan._accumulate_arrays` and `scan._accumulate_min_max` read through
    `values[keep]`, which allocates.
  - `statistics/coverage.py` passes the arrays unmasked to
    `RegionCoverage.add_interval_batch` when every row is kept. That
    method only compares, gathers and calls `.tolist()`. It does not write
    to the arrays and does not keep them.
- `AlleleScore._allele_array_batches` reads through the same
  `GenomicScore._parsed_column_batches` loop. An allele score is never a
  bigWig, so the float branch never serves it.

Reconsider the change in two cases only. The first is a bulk read that
serves a workload per annotation. The second is a profile that shows the
copy as a larger part of a rebuild.

## Prior requests

- iossifovlab/gain#459: "parse_array: skip the defensive copy when there is nothing to mask"
