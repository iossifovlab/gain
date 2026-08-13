# PROTOTYPE — gain#823, per-record allocations

**Throwaway.** Nothing here is imported by `gain`, and nothing here should be
merged to `master`. It exists to answer one question; the answer is at the
bottom.

## The question

gain#823 says `fetch_region_segment_scores` allocates two per-record objects
nobody reads, and that removing them is **1.96x faster** with output
element-wise identical. Those numbers were measured against installed
`gain-core` 2026.8.3 (master `24f7c0651`), before the
`fetch_region_values` → `fetch_region_segment_scores` rename and before the
score-filter work landed.

This prototype re-asks it on **current master**, with each removal on its own
switch:

1. Does each removal individually pay, and by how much?
2. Do they compose to the claimed ~1.96x?
3. Is the output still element-wise identical?

Three independent toggles rather than one before/after switch, because the
issue attributes specific costs to specific objects (0.154 µs/rec to the
`_fetch` tuple, 0.089 to `_record_to_begin_end`, 0.071 to the extractor call)
and a single combined number cannot check that split.

| toggle | file it is about | what it removes |
| --- | --- | --- |
| `raw-parser` | `table_bigwig.py` | the `_fetch` 3-tuple **and one generator level** — the parser takes the raw interval and does the `+1` itself |
| `inline-span` | `genomic_scores.py` | `_record_to_begin_end`'s return tuple, whose CHROM the only caller discards. The `pos_end < pos_begin` check is kept, in place |
| `inline-extract` | `genomic_scores.py` | the `get_score_values_from_record` call — extractor hoisted, list built inline |

Every variant preserves what gain#823 says it preserves: the six-slot record
contract (so the ADR 0008 `validate_records` seam is untouched), the
`pos_end < pos_begin` validation, the pluggable extractor, and the values list.

## Run it

```bash
uv run python core/prototypes/gain823_per_record_allocs/tui.py
```

Keys are listed at the bottom of every frame: `[1] [2] [3]` toggle the three
removals, `[m]` measures the candidate against today's method and against
native `pyBigWig.intervals()`, `[a]` measures all eight combinations, `[v]`
verifies the candidate is element-wise identical, `[r]` cycles region size.

Non-interactive (all eight + identity checks, then exit):

```bash
uv run python core/prototypes/gain823_per_record_allocs/tui.py --auto --passes 7
```

Defaults to `phastCons100way-bw` from the local `grr_bench` directory GRR.
Override with `PROTO823_GRR` and `PROTO823_RESOURCE` — pointing it at
`phastCons100way-chr21-tabix` is how the "benefits every backend" claim was
checked.

`variants.py` is the part worth keeping: pure, no terminal, no I/O of its own.
`tui.py` is the shell and is disposable.

## The answer

Measured on **Apple M1 Pro / macOS 26.6 / CPython 3.12.8**, gain master
`e9def6467`, `chr21:10,000,000-11,000,000`, 487,856 records, warm page cache,
7 interleaved passes, medians.

| variant | µs/rec | vs today | vs native |
| --- | ---: | ---: | ---: |
| baseline (today) | 0.737 | 1.00x | 3.16x |
| inline-extract | 0.694 | 1.06x | 2.98x |
| inline-span | 0.673 | 1.09x | 2.89x |
| raw-parser | 0.665 | 1.11x | 2.86x |
| inline-span+inline-extract | 0.637 | 1.16x | 2.74x |
| raw-parser+inline-extract | 0.628 | 1.17x | 2.70x |
| raw-parser+inline-span | 0.599 | 1.23x | 2.57x |
| **all three** | **0.560** | **1.31x** | **2.41x** |
| native `pyBigWig.intervals()` | 0.233 | 3.16x | 1.00x |

**1. Every removal pays, and the ranking holds.** raw-parser > inline-span >
inline-extract, the same order as the issue's table, and each one is real
rather than noise.

**2. All eight combinations are element-wise identical to today's method** —
487,856 segments compared, exact equality. This is the part of the issue that
reproduces without qualification.

**3. The 1.96x does not reproduce here — this machine gets 1.31x.** Not a
contradiction, a different bottleneck. The issue's box read the file at 0.113
µs/rec native, 15% of its 0.748 baseline; this one reads it at 0.233 µs/rec,
**32%** of the same 0.737 baseline. Removing Python objects cannot touch that
third. Even a *zero-cost* per-record path would only be 3.16x here, against
the 6.61x ceiling the issue was working under. The absolute saving also came
out about half the issue's: 0.177 µs/rec here vs 0.366 there, consistent with
allocation-and-call-heavy CPython code running roughly twice as fast on an M1
Pro as on the measuring host.

So the headline is hardware-specific and should be written as a range, not a
constant. The decision it supports is unaffected: the change is strictly less
work per record with an identical contract and provably identical output, and
it is worth doing at 1.3x as much as at 1.96x.

**4. The "benefits every backend" claim is true in absolute terms and much
weaker in relative ones.** The same two `genomic_scores.py` removals, against
the tabix copy of the same data (`phastCons100way-chr21-tabix`, same 487,856
records, 5 passes):

| variant | µs/rec | vs today |
| --- | ---: | ---: |
| baseline (today) | 1.788 | 1.00x |
| inline-extract | 1.736 | 1.03x |
| inline-span | 1.726 | 1.04x |
| inline-span+inline-extract | 1.689 | **1.06x** |

They save about the same ~0.1 µs/rec they save on bigWig — but a tabix record
costs 1.79 µs to produce, so the same saving is 6% instead of 16%. Both remain
element-wise identical. Worth stating in the issue, because "benefits every
backend" invites the reader to carry the bigWig percentage across.

## One trap, recorded so nobody re-finds it

The first tabix run reported all three variants as DIFFERING from record 1
onward. That was the harness, not the variants: the verifier stepped the
reference and candidate streams in lockstep, and a tabix-backed score answers
both fetches from **one `pysam.TabixFile` handle** whose cursor the two live
iterators share — each was eating the other's records. bigWig was unaffected
(`intervals()` returns a materialised list per chunk), which is exactly why it
passed first and hid the bug. `verify_identical` now materialises the
reference before opening the candidate.
