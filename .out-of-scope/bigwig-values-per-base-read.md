# A per-base `pyBigWig.values()` producer for the bigWig bulk read

`BigWigTable.get_region_value_arrays` is served from `pyBigWig.intervals()`
behind the adaptive record-count window, and it stays that way. There is
no second producer built on `values(chrom, start, end, numpy=True)`, and
no rule that routes a chunk between the two on track density.

## Why this is out of scope

The maintainer declined the exploration at triage (2026-09-09). The
whole-corpus measurement the issue asked for was not run; the decision is
that it is not worth running, for these reasons.

**The only version worth having is the one the backend has just been
cleaned of.** The issue's own numbers rule out the simple change: a blind
switch to `values()` buys ~1.4x over the deployed corpus, because the
consumer costs per element and `values()` hands it one element per *base*
where `intervals()` hands it one per *interval*. On FitCons2 -- 127 of the
150 deployed bigWigs -- that is 33x more elements, and the end-to-end read
gets 4x *slower*. The ~3.3x projection only appears if a rule chooses
`values()` on per-base tracks and `intervals()` everywhere else. That rule
is a second dual-strategy read path in `BigWigTable`, and
`docs/adr/0002-remove-bigwig-fetch-buffering.md` records why the last one
(routing on query spacing) was removed: it had been argued into existence
and measured out of it. "Track density is a stabler routing key than query
spacing" is again an argument, not a measurement, and the backend's
single-strategy shape is the thing being protected.

**The win is confined to a workload that is not the hot one.** Only the
per-base conservation tracks (phyloP, phastCons: 5.3x) benefit. The bulk
read serves the resource-statistics and histogram rebuild, which is paid
once per resource at publish time, not per annotation. The binning work
(gain#1198, verdict D14) measured the same fold at ~0.55 µs/record with
storage format dominating -- bigWig already 3.3x faster than tabix on the
same score -- and declined a vectorised fold on that basis. A producer that
makes the rare rebuild of ~23 tracks faster does not move any workload
anyone waits on.

**`values()` re-opens what the record-count budget closed.** The adaptive
window exists so that chunk size is a *record* count and memory is bounded
regardless of density (see the chunking comment in `table_bigwig.py`).
`values()` allocates 8 bytes per base of the window, so for that producer
the budget becomes base pairs again -- the sizing rule the window was
introduced to replace. It also returns `nan` for uncovered bases, which the
consumer would have to drop rather than accumulate (absent and `nan` are
different things in a record stream), and it widens float32 to float64
slightly differently from `intervals()`, so the golden statistics fixtures
have to be re-argued rather than simply matched.

## Findings worth keeping (measured for gain#455, 2026-07-28)

These hold independently of the rejection -- do not rediscover them:

- **The fetch alone always wins with `values(numpy=True)`**, even where it
  returns far more elements: on 5 Mbp of chr1, local warm cache, medians
  of 3 -- phyloP100way 4139 ms → 279 ms (14.8x, same element count),
  FitCons2 E001 93 ms → 10.5 ms (8.9x, 32.7x more elements), LINSIGHT
  242 ms → 32.4 ms (7.5x, 10.6x more elements). The cost being removed is
  the Python list-of-tuples that `intervals()` materialises and
  `np.array(...)` re-walks.
- **End to end it inverts on wide-interval tracks**: phyloP 1996 → 376 ms
  (5.31x faster), FitCons2 63.6 → 256.7 ms (4x slower), LINSIGHT 197 →
  272 ms (1.4x slower), against the real `_accumulate_arrays` consumer.
- **The semantics are equivalent.** The histogram accumulation
  span-weights each record (`min(end, pos_end) - max(start, pos_begin) +
  1`), so one element per base at weight 1 is arithmetically the same.
  Verified on LINSIGHT over 200 kb: span-expanding the intervals gives
  196,728 values and `values()` gives exactly 196,728 non-nan values,
  identical as multisets. Sums agreed to ~1e-7 in float64 and were
  identical as float32.
- **Density is already measured for free**: `AdaptiveFetchWindow.retune`
  receives `records_fetched` for a window of known width on every call. If
  a routing rule is ever built, that is its input; it needs no new config
  and no open-time probe.
- The deployed corpus at the time: 150 bigWigs, 74.7 G records, FitCons2
  10.5 G, the per-base conservation tracks dominating the other 64.2 G.

## What is still in scope

**A whole-corpus number that contradicts the projection.** The refusal is
of a density-routed second producer on the strength of per-track numbers.
If someone runs the measurement over a representative sample of the
deployed bigWigs -- `grr_bench/scripts/compare_fetch.py` is the harness,
and a `values()` rung belongs beside its native `intervals()` floor -- and
the selective path wins by more than ~3x on a workload that is actually
waited on, that is a new issue and it should quote those numbers.

**A faster `intervals()` consumer.** Anything that cuts the per-element
cost of `_accumulate_arrays` helps every track and adds no routing; the
33x element blow-up only matters because the consumer is what is slow.

## Prior requests

- iossifovlab/gain#455 -- "Explore pyBigWig values(numpy=True) for the
  bulk column-array read"
