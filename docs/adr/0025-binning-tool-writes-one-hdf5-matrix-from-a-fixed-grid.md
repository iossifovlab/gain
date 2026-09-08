# 25. `binning_tool` writes one HDF5 matrix from a fixed grid, and a query is always a search

**Status:** accepted
**Date:** 2026-09-08
**Issues:** [#1198](https://github.com/iossifovlab/gain/issues/1198) (the epic),
[#1200](https://github.com/iossifovlab/gain/issues/1200) (tracer bullet),
[#1201](https://github.com/iossifovlab/gain/issues/1201) (validation),
[#1202](https://github.com/iossifovlab/gain/issues/1202) (docs and this record),
[grr_bench#3](https://github.com/iossifovlab/grr_bench/issues/3) (the D14 timing),
[#1214](https://github.com/iossifovlab/gain/issues/1214) (the D13 amendment: region bundles)

Design doc of record: `seqpipe/genomics-toolbox`
`docs/2026-09-04-gain-score-binning-design.md`, whose decisions are numbered
D1–D20. The numbers below cite it; the statements are this record's own.

## Context

An analyst with a growing set of genome-wide position-score tracks in a GRR —
ATAC pseudo-bulk bigWigs from several studies, conservation scores, predicted
epigenetic signals — wants to compare them across the genome: correlate,
cluster, feed to a model. Every such analysis starts the same way: cut the
genome into fixed-size bins and reduce each track to one number per bin.

That first step existed as a one-off script outside gain
(`iossifovlab/binning_tool_prototype`), built on `PositionScore.
get_score_in_bins` and the task graph. It wrote a text matrix tracks-by-bins
(the transpose of what numpy and pandas want) with provenance in a separate
file, did not validate what a query had matched before the cluster job
started, silently produced no column for a query that matched nothing, and
carried two bugs (a hardcoded `chr1`; a merge step reading a global instead
of its argument). Nobody else could run it, and its output could not be
reproduced from its inputs by anyone but its author.

`binning_tool` (package `gain.binning` in gain-core, #1200 and #1201) is the
tool that replaces it. This record states the decisions that shape it, on
the tool's own terms.

## Decision

### One HDF5 file, in a plain h5py layout (D2)

The output is one HDF5 file written with `h5py`, already a direct dependency
of gain-core, in a layout designed here:

- `/values`: float64, shape `(n_bins, n_tracks)`, NaN where there is no
  data, chunked in row blocks and gzip-compressed. Row blocks make "every
  track for one chromosome" a contiguous read; gzip collapses the NaN- and
  zero-heavy tracks.
- `/bins`: a compound dataset of shape `(n_bins,)` with fields `chrom`
  (fixed-length bytes, sized to the longest chromosome name in the run),
  `start` and `end` (int64). Fixed-length bytes rather than variable-length
  strings: contiguous, compressible, and a structured array pandas accepts
  directly; the consumer decodes the bytes once.
- `/tracks`: a compound dataset of shape `(n_tracks,)` with fields `name`,
  `resource_id`, `score_id`, `aggregator` (variable-length UTF-8; a few
  hundred rows at most) and `none_value_replacement` (float64, NaN when
  unset).
- Root attributes `input_reference_genome`, `bin_size`, `regions`,
  `coordinates = "1-based-inclusive"`, `gain_version`, `created`.

Row *i* of `/bins` describes row *i* of `/values`; row *j* of `/tracks`
describes column *j*. The file explains its own columns six months later,
and two files can be checked for comparability from their attributes before
being joined.

**Rejected: the AnnData `h5ad` encoding.** It was the obvious HDF5 layout to
adopt, and it was declined in favour of a structure any h5py reader
understands without a library. The consumer decides whether to build an
AnnData object; it is a few lines.

**Rejected: Parquet and TSV.** Parquet was the first revision's choice and
was withdrawn: a bins × tracks matrix with two side tables is what HDF5
expresses natively, while Parquet would carry the coordinates as columns
beside the values or in a second file. TSV is what the prototype wrote,
transposed. Each is a few lines for the consumer to produce from the HDF5.

### Bins follow a global grid anchored at position 1 (D5, D17)

Bins are `1–bin_size`, `bin_size+1–2·bin_size`, … on every chromosome,
regardless of where a region starts, as `get_scores_in_bins` already does.
Edge bins of a window that does not start on a grid boundary are clipped to
the window, so the bounds name exactly what was aggregated, and bins from
different runs, regions, or bin sizes that divide each other tile and can be
joined by coordinate. This is documented rather than hidden.

`start` and `end` are 1-based inclusive — the convention of every gain
reader and of the region notation — so a bin's `chrom:start-end` pastes into
any other gain tool unchanged. Rows follow the `regions` list order,
ascending inside a region. Tracks follow entry order, with each entry's
matches in sorted resource-id order, so two runs of one definition produce
identical files.

### A query is always a search (D7)

A `position_score_binner` entry has four keys: `resource_query` (required),
`search_term`, `aggregator`, `none_value_replacement`. Unknown keys are
parse-time errors: a mistyped key must not silently bin with the default.

`resource_query` is a repository search — an exact id, or a glob over ids
with an optional bracketed label filter — resolved through the repository's
`search_resources` and restricted to `position_score` resources by the
binner. There is no separate "is this a wildcard" detection; an exact id is
the search that matches one resource. `search_term` is the repository's
full-text filter, conjoined with the query; it needs an indexed repository,
and a repository without one refuses the entry at parse time rather than
mid-run. Matches are ordered by resource id.

**An entry matching no resource is a parse-time error naming the entry.**
This is the one deliberate departure from the prototype's language, which
silently produced no column. A typo must not shrink the matrix.

### The aggregator defaults to the score's own (D8)

Without `aggregator:`, a track is reduced by the `position_aggregator` its
score declares in its resource configuration, so a conservation score and a
coverage track are each reduced the way their authors intended.
`aggregator:` on an entry overrides that for every resource the entry
matches. Two entries for two aggregators of one resource; there is no
per-resource override inside one entry.

### A resource with several scores is refused (D9)

Every position score is assumed to define exactly one score. A matched
resource that defines several is a parse-time error naming the resource and
listing its scores, surfaced by `--dry-run`. A key to select among several
scores was considered and **deferred** until such a resource turns up in a
binning run: which of several the user meant is not the tool's to guess,
and no resource in the public GRR needs the key today. (The design doc
marks D9 "withdrawn" — what was withdrawn is the selector key; the refusal
is what shipped.)

### A track is named by its resource id, suffixed only on repetition (D10)

A track's `name` is its resource id. When the same name would occur more
than once in the expanded track list — the same resource matched by two
entries — every member of that group gets `:<aggregator>` appended,
symmetrically, so the naming does not depend on entry order. If two tracks
still share a name (same resource and aggregator, differing at most in
`none_value_replacement`) it is a parse-time error naming both entries.
Names in `/tracks` are therefore unique. There is no `name:` key; renaming
is a one-liner on the `/tracks` table.

### Numeric only, stored as float64 (D11)

A score declared as a string type, or an aggregator whose output is not a
number, is a parse-time error naming the entry; the existing aggregator
validation is reused, and the numeric set is read off each aggregator's
declared output type rather than kept as a list. Every value is stored as
float64: `/values` is one matrix with one dtype, and HDF5 has no null for
integers. Integer results (`count`, `max` of an int score) become floats.

### One task per (track, region), one serial writer (D13)

The design doc says "(resource, region)"; what shipped is one task per
*track*, so a resource binned under two aggregators is two tasks, each
with its own chunk. Each (track, region) task writes its column chunk as a `.npy` float64
vector in the work directory. One final writer task creates the file,
preallocates `/values` with the known shape, and for each region in order
loads that region's chunks for every track, stacks them into a row block and
writes it as one chunk-aligned row slab; then writes `/bins`, `/tracks` and
the attributes. HDF5 has a single writer, so no task other than the writer
touches the file. Peak memory is one region times all tracks.

**Rejected: column-wise writes as tasks finish.** Each would rewrite every
row chunk of `/values`, since the chunks are row blocks; with hundreds of
tracks that is hundreds of full rewrites of the matrix.

Chunks are named by everything that decides their values — resource,
score, aggregator, replacement, bin size, region — so a rerun matches them:
a rerun with the same work directory computes only the chunks that are
missing and assembles the file only if it is missing (a run whose chunks
and output are all present does nothing), and two run definitions sharing
a work directory share exactly the chunks they compute identically. The
writer's one input is the chunk *directory*, whose mtime moves whenever a
chunk is created, so a second definition in the same work directory makes
the writer run again instead of leaving a stale file. A rerun does not
notice a resource changed underneath it; `--force` is how to recompute.

**Amended by #1214 (2026-09-08): one task per (track, bundle of regions).**
With `regions` omitted, a region is a whole chromosome, and a UCSC-style
hg38 has 455 sequences, 408 of them under 1 Mb and together under 2 % of
the genome — so one task per (track, region) made over 100k tasks for a
few hundred tracks, three files each in the work directory, for no CPU
gain: the per-task setup measured under 1 ms, and the tasks are bound by
the per-record fold whatever their size. Consecutive regions are now
packed, in order, into bundles of at most `--task-budget` bases (default
50 Mb; a region is never split, so a chromosome longer than the budget —
on hg38 every primary one but chr21 and chrM — stays a task of its own;
0 restores one task per region), and a task writes one chunk
per region of its bundle **under the same name as before**. The chunks,
the writer, the file and the chunk sharing between run definitions are
unchanged; only the task count and the rerun granularity move — a task
missing any of its chunks is recomputed in full, and its id spans its
bundle (first region, last region, count) rather than listing it, so it
stays a file name in the task-status directory. Splitting a chromosome
across tasks was considered and dropped: the writer assembles one slab per
user region and the chunk name encodes the region bounds, and the tail it
would shorten is one chromosome-sized task.

### The read path is `get_scores_in_bins`, unchanged — decided by measurement (D14)

The binner consumes `PositionScore.get_scores_in_bins` as it stands; it is
the semantic reference for the global grid, the boundary split and
first-record-wins. It folds one Python iteration per record, so the design
doc deferred the question of adding a vectorised binned fold over
`fetch_region_value_arrays` to a chr21 timing (grr_bench#3, reported on
#1198 on 2026-09-06):

- The fold costs 0.54–0.59 µs per record on every backend — bigWig, tabix,
  and a dense ATAC bigWig — so its total cost is a function of record count
  alone. (A sparse ATAC track measures ~1.5 µs per record, because the fold
  iterates position *runs* and a sparse region carries a gap run between
  every record; that is the same loop, not a different cost.)
- For the workload the tool exists to serve (the prototype's ~92 ATAC
  tracks) a whole-genome run is 0.02–5.4 CPU-hours, already split across
  the per-(resource, region) tasks. The fold is ~58% of that on bigWig, so a
  *perfect* replacement saves at most ~3 CPU-hours spread over 92
  independently scheduled tasks; the measured ceiling is 2.5×.
- Storage format dominates the fold: the same per-base conservation score
  over chr21 took 35.4 s from bigWig and 116.5 s from tabix, a 3.3× gap —
  larger than anything a vectorised fold could buy. Whole-genome and
  chr21-only tabix indexes were within 1%.

**The vectorised fold is not filed.** The trigger to re-open it is recorded
here: per-base conservation tracks becoming routine in binning runs, where
the fold is 60% of binned time on bigWig with a 2.5× ceiling.
`grr_bench/scripts/time_binning.py` re-runs to re-open the question.

**Rejected: native `pyBigWig.stats` binning.** Its results depend on the
storage format of the input — it aggregates the raw float32 payload and
cannot honour the score's declared value type or `na_values` — so two forms
of one score would bin to different matrices. The same rejection, measured
for region aggregation in annotation, is recorded in
`.out-of-scope/bigwig-stats-pushdown.md`; this is its sibling.

One finding from the timing is documented for users rather than acted on: a
bigWig stores float32, so the bigWig and text forms of one score do not
produce bit-identical matrices (agreement ~1e-8 through a mean over 10 kb
bins). Nothing in this design depends on that, but a reader of `/values`
might otherwise expect two formats of one score to be equal.

## Why scoped this way

- **Position scores only, through an entry-point group.** Binner kinds are
  discovered through `gain.binning.binners`, registered like every other
  gain plugin group, with exactly one member. A fragment-score binner is
  the intended second member (#1203) and the group is the extension point;
  building it now would have widened the first slice past what the timing
  could validate.
- **A fixed grid only.** BED-defined regions and unclipped bins anchored at
  a window's start are follow-ups (#1204). The global grid is what makes
  files from different runs joinable, and that property is the point of
  the tool.
- **No second output format, no `dtype:` option.** float32 would be a
  two-line addition if size ever matters; adding it now would be a second
  matrix layout to keep equivalent forever.
- **No release-notes entry.** `changes.rst` is composed at release time.

## Consequences

- The output is a contract: `/values`, `/bins`, `/tracks` and the six
  attributes, with their dtypes, are what downstream analysis reads. A
  change to the layout is a change to every consumer's read-back code.
- Every track is one float64 column. A binner kind that produced
  non-numeric or list-valued cells has no place in `/values` and would need
  its own layout decision.
- `get_scores_in_bins` semantics are the tool's semantics. A change to the
  boundary split or first-record-wins there changes every binned matrix,
  which is why the tool's tests pin the file it writes rather than
  re-testing the score.
- Per-base tracks are expensive genome-wide (~40 min CPU per track from
  bigWig, ~2 h from tabix). The user page says so and tells users to prefer
  the bigWig form; when that stops being enough, the measurement and the
  trigger above say what to build.
- A rerun trusts its chunks. A user who updates a resource in the GRR and
  reruns without `--force` gets the old matrix, by design; the user page
  says so.
