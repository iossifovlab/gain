# Per-score bulk/per-record dispatch in the statistics scan

The statistics scan chooses between the bulk (column-array) path and the
per-record path **per table**, not per score. GAIn will not split that
choice so that one region read serves some scores in bulk and feeds the
rest per record.

## Why this is out of scope

The maintainer declined the feature at triage (2026-09-24). The request
(#419) was written against the first cut of the bulk scan (#385), whose
gate sent a whole table down the per-record path whenever any histogram
was not a `NumberHistogramConfig`. The motivating example was a tabix
table with six float scores and one string score. #419 said to measure
before building it: "if every real GRR resource is homogeneous, this is
not worth the complexity." The measurement says it is not worth it:

- **The headline case is already served, by another route.** #406
  (`f56576b8a`) gave `CategoricalHistogram` its own `add_batch` and turned
  the gate into a pairing: a number histogram takes a `float` or an `int`
  score, and a categorical histogram takes a `str` score. That is the second
  of the two options #419 weighed ("give `CategoricalHistogram` a batch
  API of its own so everything goes through arrays"). A float + str table
  now takes the bulk path as a whole table.
- **What is left is two pairings #406 keeps off the bulk path on purpose:**
  a categorical histogram over an `int` score, and a `bool` score. The bulk
  read yields an int column as float64, and `3.0` is not the categorical
  key `3`. A batch of the wrong shape is a coercion failure inside
  `add_batch`, not a value the histogram can refuse one at a time. See the
  `can_bulk_histogram` docstring in
  `genomic_scores_impl/scan.py`.
- **No real resource has those pairings AND a backend that serves the bulk
  read.** A scan of all 1,598 score resources in `grr`, `grr_sfari`,
  `grr_bench` and `grr_nygc_single_cell_demo` found exactly one resource
  with a score the bulk gate refuses: `hg38/scores/dbSNP`, the same resource in
  `grr` and `grr_sfari`, with 41 int/bool scores under categorical
  histograms. It is a VCF-backed `allele_score`, and a VCF table serves no
  column arrays, so it stays per-record whatever this dispatch does.
  Defaulted histograms never trigger the refusal: `build_default_histogram_conf`
  gives `int`/`float` a number histogram and `str` a categorical one.

Splitting the dispatch would add a per-score `ScoreLine`
reconstruction or a second accumulation shape inside one batch. That
path would have to stay bit-exact with the per-record one forever, and
it would speed up no resource that exists.

## When to reconsider

If a tabix- or bigWig-backed score resource appears that has a categorical
histogram over an `int` score, or a `bool` score, next to bulk-eligible
scores, and its statistics build time matters, the cheaper fix is
probably not per-score dispatch. It is a third bulk pairing, for example an
int column parsed to `int64` for `CategoricalHistogram.add_batch`, held to
the same equivalence #406 holds the other two to.

## Prior requests

- iossifovlab/gain#419 — "Per-score dispatch for tables mixing number and categorical scores"
