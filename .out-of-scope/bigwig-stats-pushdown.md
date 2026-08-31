# bigWig summary pushdown (`stats()` / `aggregate_region`)

GAIn will **not** push region aggregation down into the bigWig backend via
`pyBigWig.stats()`. There is one aggregation path — the generic weighted
stream over fetched records — and every backend feeds it.

## Why this is out of scope

The maintainer declined the feature at triage (2026-08-31). The trade-off on
record:

- **The performance motivation largely evaporated.** The pathology that made
  bigWig aggregation slow was the region fetch issuing one range query per
  50 bp (#259, fixed). What the pushdown would still buy — tens of
  milliseconds down to sub-millisecond on a 1 Mb region — does not justify
  what it costs.
- **It creates a second semantics-bearing code path.** The generic weighted
  path (#260) is the definition of aggregation semantics; a pushdown is a
  parallel implementation that must be proven equivalent forever
  (property tests, sparse fixtures, gap/edge/empty-region cases) and can
  drift silently the moment anyone touches `na_values`, `value_parser`, or
  an aggregator's semantics.
- **The correctness guards are real, not theoretical.** Triage of #263
  measured both failure modes: a score declared `type: int` over payload
  `[1.7, 2.2, 3.9]` means to **2.0** through GAIn's parse-then-aggregate
  path but **2.6** through `stats()` on the raw floats; and `na_values`
  sentinels would be silently *included* in a pushed-down mean while the
  generic path excludes them. A safe pushdown therefore has to decline in
  exactly the configurations where users would expect it to work.
- **It complicates the layering seam.** `stats()` is a whole-region
  collapse, so the design needed a negotiated `aggregate_region` capability
  crossing the `genomic_resources` ↔ `annotation` boundary (aggregator
  passed by name) purely to avoid a dependency inversion. That machinery
  exists only to serve the pushdown.

## Findings worth keeping (verified during the #263 triage)

These hold independently of the rejection — do not rediscover them:

- The pushdown equivalence itself **does** hold when the guards pass:
  30/30 exact matches against the real `PositionScore` over a sparse bigWig
  (gaps, block edges, empty regions, whole chromosome), including the
  1-based-closed → 0-based-half-open translation. The full table is on
  #263.
- `pyBigWig.stats()` must be called with `exact=True`. The default
  zoom-level path is ~14× faster but **approximate** (measured 0.147% off —
  zoom bins do not align with query boundaries).
- `bw.header()`'s `minVal` / `maxVal` / `sumData` look like a free exact
  min/max for resource statistics but are returned as **truncated
  integers** by pyBigWig (a true max of `9.625` reads back as `9`). Anyone
  who needs those numbers must use `stats()`.

## Prior requests

- iossifovlab/gain#263 — "bigWig summary pushdown: aggregate_region via pyBigWig stats(exact=True)"
