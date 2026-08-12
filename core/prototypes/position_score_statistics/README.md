# PROTOTYPE — position-score statistics (coverage + segment lengths)

**Throwaway.** Not production code, not on `master`, no tests, no gain
imports. It exists to answer one design question for epic
[gain#770](https://github.com/iossifovlab/gain/issues/770) and its
children [#772](https://github.com/iossifovlab/gain/issues/772) (covered
positions) and [#775](https://github.com/iossifovlab/gain/issues/775)
(segment-length histograms).

## Run it

```bash
python core/prototypes/position_score_statistics/tui.py          # interactive
python core/prototypes/position_score_statistics/tui.py --dump   # every scenario, once
```

Stdlib only — it runs in a fresh worktree with no `uv sync`.

Keys: `1-9,0` or `n`/`p` pick a scenario · `k` position/fragment ·
`+`/`-` region size (`0` = unbounded) · `g` fold order · `s` sweep every
region size 1..40 · `a` add a row · `x` drop the last · `v` per-region
detail · `q` quit.

Each frame shows the rows, what each region task returns, the merged
result, and the **oracle** — the same scan run as one unbounded pass.
Chunking is only correct if those two agree, for every region size.

## The question

Covered positions merge trivially: chunks are disjoint and every row is
clipped to its region, so counts add. **Segments do not.** A segment cut
by a region boundary must not count twice; a segment spanning three
chunks must not count three times. So the per-chunk statistic has to
carry its extent plus the runs still open at its edges, and the merge has
to stitch them — which makes it order-sensitive, unlike the min/max and
histogram merges the scan already has.

Is that per-chunk shape sufficient, and is the merge correct for every
chunking?

## The model

`RegionStats` is a monoid carrying: the scanned extent, the segments
provably **closed** (touching neither outer edge) as a count plus a
fixed-log-bin length histogram, the open run at the left edge
(`head_len`), the open run at the right edge (`tail_len`), and
**`one_run`** — the flag for head and tail being the *same* run, i.e. a
chunk covered end to end.

`combine(a, b)` stitches `b` onto `a`; `finalize` closes what is still
open once no further region can extend the contig.

## Findings

1. **The head/tail pair alone is NOT sufficient. `one_run` is the load
   bearing part.** A segment spanning three or more chunks makes the
   middle chunks fully covered, where head and tail are one run — merge
   them as two and the count and the length histogram both go wrong.
   This is the case a "carry an open head and tail" description of the
   design does not obviously cover, and it is the one to write the test
   for (scenario 4).

2. **The merge is associative, given order.** A sequential left fold and
   a balanced pairwise fold agree on every scenario at every region size
   1..40. A distributed merge is therefore free to group regions as it
   likes.

3. **Out-of-order folding is detectable, and should be detected.**
   Carrying `(chrom, start, end)` lets `combine` refuse a pair that is
   not adjacent-and-in-order on one contig. Across 440 deliberately
   reversed folds, **0 produced a wrong answer that was not refused**.
   The real implementation should assert adjacency rather than trust the
   order its region tasks happen to arrive in — cheap, and it converts
   the one class of silent corruption this design admits into a loud
   failure.

4. **Coverage and segment statistics are chunk-invariant** for position
   scores, across every region size 1..40, including the unbounded
   (`--region-size 0`) path, for: a row split by a boundary, adjacent
   rows forming one segment, a segment across three chunks, a
   fully-covered contig, gaps, single-position rows, and two contigs.

5. **A pre-existing bug, found on the way: fragment-score value
   histograms are not chunk-invariant** (scenarios 8 and 9). A fragment
   is weighed 1 *per region that fetches it*, and a region fetches every
   fragment overlapping it — so a fragment spanning N regions is counted
   N times. Confirmed against real `gain` master, not just in this
   model: one fragment `8-14` histogrammed as **1** with a single
   region, **2** split at 10, **4** with 2bp regions. Position scores
   are immune (span weighting makes the split exact) and allele scores
   are immune (records are points). Filed as gain#816 — and #794
   (fragment count + fragment-length histograms) inherits it.

6. **Overlapping or touching rows are refused outright on a position
   score** (scenario 7) — the validators abort the whole statistics
   build. Union semantics is only reachable on fragment scores. This is
   the triage amendment already recorded on #772.

## What lifts, what does not

`segment_stats.py` is written to be liftable into
`gain.genomic_resources.statistics` — pure, no I/O, no gain imports. The
shapes worth keeping are `RegionStats` (extent + closed + head/tail +
`one_run`), `combine`, and `finalize`. `tui.py` is the disposable shell.

What the real implementation adds and this deliberately does not model:
serialization, the resource's JSON statistics file, the info-page
rendering, per-chrom keying beyond a plain dict, and the real scan's
record/array plumbing.
