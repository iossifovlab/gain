# Naming ref/alt in the array door's inverted-span refusal

The statistics scan's array door (`validate_record_arrays` in
`gain/genomic_resources/statistics/record_validation.py`) will **not** be
widened to name an allele record's ref and alt when it refuses that record for
having an end before its begin. Its refusal stops at the position:

```
The resource record chr1:30-29 has a region with end 29 smaller than the beginning 30.
```

where the per-record door (`validate_records`, through `_record_to_begin_end`)
says `chr1:30-29 C->T has a region ...` for the same row. Same `OSError` type,
same record, same first fault — one suffix apart. That difference is a stated
fact, not an oversight: the door's docstring and `_refuse_first_fault`'s say
so, and `test_scan_array_door.py::test_an_allele_inverted_span_loses_its_ref_alt_at_the_array_door`
pins it. For a position score or a fragment score the two doors' messages are
identical.

## Why this is out of scope

### The narrowing can fire for almost no resource

Three things have to hold at once before the suffix is even missing:

- **The allele table declares a `pos_end` column.** Without one the table's
  end key falls back to its begin key
  (`GenomicPositionTable._set_core_column_keys`: `self.pos_end_key =
  self.pos_begin_key`), so every batch the door reads has
  `pos_end == pos_begin` and the span check cannot fire at all. Allele scores
  as we publish them are point records; a `pos_end` column on one is the
  unusual shape.
- **The row is exactly `pos_end == pos_begin - 1`.** That is the one inverted
  row tabix admits: htslib checks `beg > end` after converting the begin to
  0-based, so `30 29` arrives as the empty interval `[29, 29)` and passes
  `hts_idx_push`, while `pos_end == pos_begin - 2` is refused at index time
  (`test_tabix_refuses_to_index_a_row_ending_two_below_its_begin`). A bigWig
  cannot hold an inverted interval at all (pyBigWig refuses `end <= start`,
  `test_a_bigwig_cannot_be_written_with_an_inverted_interval`), so a
  bigWig-backed table never reaches the check.
- **The region is scanned bulk**, i.e. the score is bulk-eligible and the
  histogram pass was served nucleotides (`serves_allele_arrays`). A table that
  declares neither `reference` nor `alternative` is sent to the per-record
  read, whose refusal then carries no suffix either — `inverted_span_error`
  prints nothing for `ref=None, alt=None`. So on a ref/alt-less table the two
  doors already agree.

And when all three do hold, the refusal still locates the row: an inverted
span is a claim about one record's own two ends, and `chr1:30-29` finds it.
Ref and alt exist to tell apart several records at one position, which is what
the *ordering* rules need; the repair here is a one-line edit to the table
whichever door names it. gain#1558 itself rated the cost of leaving it as low.

### What closing it would cost

The door's contract is a transducer over `RecordArrays` — the
`(pos_begin, pos_end, {score_id: values})` 3-tuple that
`fetch_region_value_arrays` produces — and it yields exactly what it was given.
The ref/alt columns live on a *different* read, `fetch_region_allele_arrays`,
whose `AlleleRecordArrays` is that tuple widened by two fields; the shared read
was deliberately left untouched when gain#780 added the widened one (see
[duplicate-allele-keys.md](duplicate-allele-keys.md), the knock-on section).
Today the histogram pass's `allele_arrays_folded_into` folds the nucleotides
into the allele statistic and slices them off (`batch[:3]`) one line *before*
the door, so the door never sees them; and the min/max pass (`do_min_max_bulk`
→ `_validated_batches`) reads the plain arrays and never has them — and it
runs *before* the histogram pass whenever a number histogram lacks a view
range, so `repo-stats` would refuse the row from that pass without a suffix
even if the histogram pass had one.

gain#1558 sketched two routes, both touching the door's yield type and the
three accumulators behind it. Triage found a smaller one, recorded here so a
future proposal starts from the real cost rather than the larger one:

1. the two array rules read `batch[0]`/`batch[1]` instead of destructuring
   three names, and hand `batch[3][at]`/`batch[4][at]` to
   `inverted_span_error` when the batch is five wide — the door still yields
   what it was given, it merely accepts either width;
2. `allele_arrays_folded_into` moves its `[:3]` slice from before the door to
   after it;
3. `_validated_batches` asks `serves_allele_arrays` of the opened score and,
   when yes, reads `fetch_region_allele_arrays` and slices after the door;
4. the pinning test flips to equality, a min/max-pass case is added, and the
   three docstrings that state the narrowing (the door, `_refuse_first_fault`,
   `AlleleRecordArrays`) are updated.

Roughly thirty lines and no accumulator touched. It is still a contract
change on the one seam ADR 0008 and ADR 0027 keep narrow — a door that
accepts two batch widths is a door every future consumer has to think about —
and the min/max pass would start carrying two `object` columns it discards,
the per-row cost gain#665 identified and gain#780 accepted only because a
statistic consumed them. Paying that for a message suffix on a row that
almost no allele table can contain is the trade this record declines.

## What is still in scope

- **The refusal itself.** Both doors refuse `pos_end < pos_begin` with the
  same `OSError`, and a two-fault resource is refused for the same first
  fault down both — gain#1526 closed that gap, and the equivalence tests in
  `test_scan_array_door.py` hold it. Nothing here loosens that.
- **Position and fragment scores.** Their records carry no ref/alt on either
  path, so their messages are identical already.
- **A motivated proposal.** If a consumer arrives that needs the array door
  to see the key columns for a reason beyond this message — a rule that
  adjudicates between records at one position, say — the route above is the
  cheap way to carry them, and this record is the cost it must beat. (Note
  that the one such rule proposed so far, the duplicate-key rule of gain#663,
  is itself out of scope; see [duplicate-allele-keys.md](duplicate-allele-keys.md).)

## Prior requests

- iossifovlab/gain#1558 — "Array-door inverted-span refusal names no ref/alt for an allele score (follow-up from #1526)"
