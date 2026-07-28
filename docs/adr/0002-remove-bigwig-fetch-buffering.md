# 2. Remove bigWig fetch buffering

- **Status:** accepted
- **Date:** 2026-07-28
- **Issues:** [gain#449](https://github.com/iossifovlab/gain/issues/449) (reintroduce it, gated on protocol)

## Context

`BigWigTable` had two fetch strategies. Both chunked a region into
`pyBigWig.intervals()` calls sized by an adaptive records-per-call budget; they
differed in what happened afterwards.

- **Direct** materialised a chunk, yielded it, and forgot it.
- **Buffered** kept the chunk in `self._buffer`, recorded the region it covered,
  and served later queries out of it by binary search (`_find`), refilling
  (`_fill`) when a query fell outside.

`get_records_in_region` routed between them on one number: if a query started
within `use_buffered_threshold` (default 500) base pairs of where the last one
started, it took the buffered path. The intent was sequential annotation — a run
that walks the genome in order should not pay one range query per variant.

Nobody had measured whether it did.

### The measurement

Against `/data/grr`: `hg38/scores/phyloP100way` (9.2 GB, per-base) and
`hg19/scores/Linsight`. Local warm page cache, medians of 3 interleaved rounds,
2000 point queries per run.

Point queries, µs/query:

| workload | never buffer | buffered (threshold 500) |
| --- | --- | --- |
| uniform 1 bp | 11.4 | **3.5** |
| uniform 50 bp | 15.2 | **11.6** |
| exponential gaps, mean 200 bp | **26.1** | 34.2 |
| exponential gaps, mean 700 bp | **23.4** | 81.6 |
| exponential gaps, mean 3000 bp | **25.0** | 90.8 |

Region scans, µs/record:

| resource | direct | buffered | `intervals()` calls |
| --- | --- | --- | --- |
| phyloP100way, 1 Mbp | **0.297** | 0.429 | 198 vs 200 |
| LINSIGHT, 1 Mbp | **0.304** | 0.425 | 11 vs 15 |

So the buffer won only on *uniformly dense* access, and lost everywhere else —
including on the region scan, where it saved no I/O at all and added 40–60%
pure bookkeeping.

**Exponential gaps are the realistic case.** Variants in a VCF are not evenly
spaced, and irregular spacing is what breaks the buffer: a short gap routes to
the buffered path and triggers a `_fill` that materialises ~5000 intervals, and
the next long gap routes to direct and discards it. The buffer is paid for and
thrown away, over and over.

Two things this is **not**, both checked before concluding:

- **Not a threshold that wants retuning.** Every `use_buffered_threshold` in
  {50, 100, 250, 500} loses to never-buffering on all three exponential
  workloads; even 50 costs 2.2× at mean gap 700.
- **Not a fill size that wants retuning.** At spacings of 250 and 500 bp,
  direct beats every `buffer_fetch_size` from 100 to 5000.

The buffer also made performance *unpredictable*: across two random draws of the
same mean-700 distribution it cost 35.3 and 81.6 µs/query, while direct held at
23.3 and 23.4.

### The one case where it wins

`FsspecReadOnlyProtocol.open_bigwig_file` accepts `s3`, `http` and `https` and
hands the URL straight to `pyBigWig.open`, so a bigWig read over the network is
a real configuration and not a hypothetical. With injected per-call latency
(mean-gap-700 workload):

| per-call latency | buffered (500) | never buffer |
| --- | --- | --- |
| 0 ms | 75.8 | **21.7** |
| 0.1 ms | **163.2** | 181.8 |
| 1 ms | **685** | 1084 |
| 10 ms | **5786** | 10113 |

Crossover is ~0.1 ms. Below it the buffer's call saving (225 vs 400) is
worthless; above it, it is a 1.6–1.75× win.

## Decision

**Remove the buffering layer entirely.** `BigWigTable` has one fetch strategy —
the chunked walk, now `_fetch` — and keeps no interval state across calls.
`_buffer`, `_buffer_region`, `_fill`, `_find`, `_fetch_buffered` and `_last_pos`
are gone.

`direct_fetch_size` is renamed `fetch_size`, with no alias: the capability
survives under a new name, so a config using the old spelling means something
specific and should fail validation rather than silently receive the default.
`buffer_fetch_size` and `use_buffered_threshold` are the opposite case — the
feature they configured no longer exists, there is nothing to rename them to,
and refusing a resource to report a key that changes nothing would take working
data offline. They stay in the schema, ignored, with a warning.

**Scoped to removal, not to replacement.** The protocol-aware version that would
recover the remote win is deliberately *not* part of this change; it is
gain#449. Landing them together would have meant shipping an untested latency
heuristic to fix a regression nobody is yet experiencing — every deployed CSHL
bigWig resource is read from local disk.

### Why not keep it for the dense case

The buffer is 3.2× faster at 1 bp spacing, and that is genuinely lost. It was
accepted because the workload it serves is one that should not exist: fetching
the same data with a single region query costs **0.27 µs/record** — 13× faster
than the buffer's best point-query number. The buffer was optimising a call
pattern whose correct fix is to call the API differently.

## Consequences

- Annotation against a local or cached GRR gets **23–72% faster** on realistic
  variant spacing, and region scans get 40–60% faster per record.
- A caller issuing thousands of point queries ~1 bp apart is **3.2× slower**.
  Nothing in this tree does that; an out-of-tree caller that does should be
  issuing region queries.
- Reading a bigWig over `http`/`s3` is **1.6–1.75× slower** at ≥1 ms per call
  until gain#449 lands. No deployed resource is configured that way today.
- Two invariants that needed their own regression tests are now unreachable
  rather than maintained: a reopened table cannot serve a previous open's values
  (gain#345), and a fetch cannot resume from retained state after `close()`. The
  second rule still applies — a generator mid-walk must notice a closed handle —
  and its guard moved into `_fetch`, checked once per chunk. A walk that fits in
  a single chunk now completes after `close()` instead of raising, because it
  has already read everything it will yield; the test was rewritten to span
  several chunks rather than relax the rule.
- `_find`'s insertion-point rule went with the buffer. It was subtle and it was
  a bug source: returning the left-hand neighbour on a miss emitted a score at a
  position the track does not cover. The *fixture geometry* that caught it is
  kept, re-pointed at the surviving path, in
  `test_a_fetch_yields_nothing_at_a_position_inside_a_gap`.

## Cost, honestly

The removal itself was mechanical. The expensive part was establishing that it
was safe, and that part is worth repeating rather than trusting: five separate
benchmark runs, two of which produced conclusions that had to be thrown away.

The first pass measured only uniform spacings and made the buffer look like a
straightforward win below 100 bp and a straightforward loss above 250 bp —
suggesting the fix was to lower the threshold. That was wrong, and only the
exponential-gap workload showed why: with irregular spacing *no* threshold
helps, because the cost comes from mixing the paths rather than from choosing
the wrong one. A conclusion drawn from evenly-spaced synthetic queries would
have shipped a retuned constant and left the real problem in place.

A group-sampled estimate of record density also picked an unrepresentative file
(LINSIGHT, 0.087 records/base) as the proxy for a group containing the per-base
conservation tracks (1.000 records/base), understating a related figure roughly
five-fold until it was recomputed per file.

Before deleting anything, an oracle property test was written that checks fetch
output against unchunked `pyBigWig.intervals()` over random track geometries and
query patterns, and it was run against **both** strategies — verified to drive
`_fill` 149 times and `_find` 393 times on the buffered arm. That is what
licenses the claim that the surviving path yields the records the retired one
yielded. Without it the removal would have rested on reading the two
implementations and believing they agreed.
