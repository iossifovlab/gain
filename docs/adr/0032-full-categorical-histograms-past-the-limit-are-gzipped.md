# 32. Full categorical histograms past the limit are gzipped

- **Status:** accepted
- **Date:** 2026-09-30
- **Issues:** [gain#1733](https://github.com/iossifovlab/gain/issues/1733)
  (this record and the encoding); [gain#1634](https://github.com/iossifovlab/gain/issues/1634)
  (the decision to keep the full histogram, which this follows from);
  [gain#1736](https://github.com/iossifovlab/gain/issues/1736) (the epic);
  [gain#1734](https://github.com/iossifovlab/gain/issues/1734) (dropping the
  other encoding's leftover on a rebuild);
  [gain#1735](https://github.com/iossifovlab/gain/issues/1735) (the warning
  when the gzipped file is not DVC-protected);
  [gain#718](https://github.com/iossifovlab/gain/issues/718) (the truncated
  sidecar)

## Context

A `categorical` histogram a curator declares (`enforce_type=True`) carries no
`UNIQUE_VALUES_LIMIT`. A fragment resource's `cell` score is one: its
histogram holds every barcode. For `atac_fragments/T23_b17_Thymus_PCW17` in
`grr_bench` that is 1,768,713 values, written as 52 MB of pretty-printed
JSON at `statistics/histogram_cell.json`. There are 668 such files across
`grr_bench` and `grr_nygc_single_cell_demo`, each moved into DVC by hand.

gain#718 gave every such histogram a small truncated sidecar at
`statistics/truncated/histogram_<id>.json`: the displayed values plus the
`unique_values` / `total_count` of the full histogram. Info pages and
`truncated=True` loads read the sidecar and never the full file.

gain#1634 asked whether the full file should exist at all, and decided that
it should, in its current in-memory and transfer representation. That left
its encoding on disk: pretty-printed JSON of millions of short strings is
highly compressible, and nothing about it needs to stay human-diffable.

## Decision

1. **The full file is the complete record; the sidecar is the bounded
   default read.** The full histogram keeps every value and its count, in
   the dict schema `CategoricalHistogram.to_dict()` produces. A `plot_function`
   and a default `get_score_histogram(score_id)` read it. Everything that
   only renders reads the sidecar.

2. **Gzipped JSON, and only past the limit.** When a statistics build saves
   a categorical histogram whose `unique_values` exceeds
   `CategoricalHistogram.UNIQUE_VALUES_LIMIT`, the full histogram is written
   to `statistics/histogram_<id>.json.gz`, in the same schema, and no plain
   `statistics/histogram_<id>.json` is written. Every other histogram
   (numeric, null, categorical within the limit) is written as before, byte
   for byte. The writer decides from the histogram in hand, never from the
   stored manifest, so the first build past the limit already gzips.

3. **The sidecar stays plain, keyed by score id.** Its name comes from the
   plain `statistics/histogram_<id>.json`, whatever encoding the full file
   has (`ScoreResource.get_truncated_histogram_filename`), and its content
   is unchanged. An older gain or gpf release finds it where it always did
   and keeps rendering info pages.

4. **gain warns, and does not `dvc add`.** Whether a gzipped full histogram
   belongs in DVC is the GRR's policy, so gain never runs DVC. The warning
   for a gzipped file that is neither DVC-tracked nor git-ignored is
   planned work (gain#1735); until it lands, gain does not warn.

5. **The sidecar decides the encoding a reader loads.** All full-histogram
   reads go through `ScoreResource.get_histogram_filename`:
   - sidecar listed in the manifest: `histogram_<id>.json.gz` if the manifest
     lists it, else the plain `histogram_<id>.json` (a GRR built before this
     change);
   - no sidecar: the plain `histogram_<id>.json` (or the legacy `.yaml`).

   So a reader at this version does not serve a leftover of the other
   encoding as current, as long as the sidecar is reconciled: a rebuild
   that shrinks below the limit drops the sidecar, and the plain file loads
   even while an old `.json.gz` is still present. The exceptions are listed
   under Consequences. The existing
   errors carry over: a sidecar whose full file is absent or unpulled is a
   `HistogramError`, and `truncated=True` loads the sidecar. `load_histogram`
   decompresses by the `.json.gz` suffix.

6. **Lazy rollout.** There is no migration. Existing plain full files stay
   valid indefinitely, and a resource switches on its next statistics
   rebuild. The statistics hash is an input hash with no code version
   (`.out-of-scope/statistics-hash-code-version.md`), so this change does not
   invalidate any resource, and forcing it to would mean rescanning every
   resource for a change that alters no statistic.

7. **Deterministic gzip.** The file is written through `gzip.GzipFile` with
   `mtime=0`, an empty embedded file name and a fixed compression level
   (`GZIPPED_HISTOGRAM_COMPRESSLEVEL`). The same histogram gives the same
   bytes, and so the same md5, on every rebuild. Without that, rebuilding
   unchanged statistics would churn the manifest and create a new DVC blob.

## Rejected alternatives

- **A summary-only store** (gain#1634 option B): keep only `unique_values`,
  `total_count` and the top values, and drop the full counter. It loses the
  complete record that a `plot_function` (the per-cell fragment-count
  distribution) and ad-hoc analysis read. gain#1634 kept the full record.
- **Parquet or `.npz`.** A columnar encoding would be smaller and faster to
  load, but it is a second schema to maintain next to `to_dict()`, it needs
  a reader dependency on every consumer, and gzip already removes most of
  the size. Gzipped JSON keeps one schema and a reader that is a suffix check.
- **Gzip always.** Gzipping every histogram would change the bytes, and the
  md5, of every numeric and small categorical histogram in every GRR, for no
  gain in size worth having.
- **A forced sweep.** Rebuilding every affected resource at once would be a
  fleet-wide rescan plus content-repo commits, for files that stay valid as
  they are.
- **A conversion command.** Converting plain files to `.json.gz` in place
  would be a second writer of the same file that has to agree with the build
  byte for byte. The next rebuild does it with the one writer.

## Consequences

- An older release reading a resource rebuilt past the limit uses the
  sidecar for its `truncated=True` loads and info pages, which keep
  working. Its *full* load reads only the plain `histogram_<id>.json`.
  When no plain file is present, that load raises the same `HistogramError`
  it raises today for a full histogram whose DVC blob is not pulled. That is
  the compatibility cost, and it falls only on full loads.
- Every rebuild drops the full histogram the earlier build left in the other
  encoding, and removing a score (or nulling its histogram config) drops its
  `.json.gz` with the rest of its files (gain#1734). The deletion goes
  through `drop_stale_histogram_file`, so a DVC-tracked leftover is reported
  and kept, not deleted. For full loads that closes two cases:
  - **Within the limit, rebuilt past it:** the earlier plain
    `histogram_<id>.json` is dropped, so an older release's full load raises
    rather than returning that stale file as current. A DVC-tracked one is
    reported instead, for the curator to `dvc remove`.
  - **Past the limit, rebuilt within it, with a DVC-tracked sidecar:**
    the sidecar is kept with a warning, but the old `.json.gz` is dropped,
    so a reader at this version loads the plain file the rebuild wrote.

  One case remains:
  - **Rebuilt past the limit by an older release:** that release rewrites
    the plain file and the sidecar but never touches the `.json.gz`, so a
    reader at this version loads the stale `.json.gz`. Only a rebuild by a
    release that has gain#1734 drops the `.json.gz` an older release's
    rebuild left.
- Nothing outside `ScoreResource` names the full histogram file; a new
  reader must go through `get_histogram_filename` / `get_score_histogram`.
