# 25. `binning_tool` writes one HDF5 matrix from a fixed grid, and a query is always a search

**Status:** accepted
**Date:** 2026-09-08
**Issues:** [#1198](https://github.com/iossifovlab/gain/issues/1198) (the epic),
[#1200](https://github.com/iossifovlab/gain/issues/1200) (tracer bullet),
[#1201](https://github.com/iossifovlab/gain/issues/1201) (validation),
[#1202](https://github.com/iossifovlab/gain/issues/1202) (docs and this record),
[grr_bench#3](https://github.com/iossifovlab/grr_bench/issues/3) (the D14 timing),
[#1214](https://github.com/iossifovlab/gain/issues/1214) (the D13 amendment: region bundles),
[#1211](https://github.com/iossifovlab/gain/issues/1211) (the D14 amendment: an absent
contig is one uncovered run),
[#1301](https://github.com/iossifovlab/gain/issues/1301) (the D13 amendment: a budget of
0 or less is one task per track, opening its resource once),
[#1742](https://github.com/iossifovlab/gain/issues/1742) (the D2 amendment: the
`/tracks` layout of the fragment kind, `group` and `resource_ids`, and the
local-file root attributes; the kind's own semantics are
[ADR 0035](0035-a-fragment-belongs-to-the-bin-of-its-start.md))

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

**Amended by #1742 (2026-10-05): `/tracks` gains `group`, `resource_id`
becomes `resource_ids`, and a run that reads a local file records it.**
The `fragment_score_binner` kind (#1203, #1741, #1759; its semantics are
ADR 0035) made two things true that the layout above could not say: a
track can be computed from many resources (a pooled entry merges every
matched sample into one column), and a resource gives many tracks (one per
group of its fragments). The fragment-binner design of record is
`seqpipe/genomics-toolbox` `docs/2026-09-30-gain-fragment-binner-design.md`,
decisions F1–F21; F7 and F12 are the ones this amendment carries.

- `/tracks` has a `group` column, variable-length UTF-8: the track's group
  (`all` for a fragment entry with no grouping, a class, a sample-prefixed
  barcode), and the empty string for a position-score track.
- `/tracks.resource_id` is renamed `resource_ids`, **for every track**: the
  comma-separated list of the resources the track was computed from, in
  resource-id order. A position-score track and an unpooled fragment track
  hold one id and no comma; a pooled track holds every matched id. The
  separator is safe because a resource id is restricted to `a-zA-Z0-9/._-`
  (`RESOURCE_ID_CHARACTER_CLASS` in `repository.py`, one constant since
  #1352), so a comma never occurs inside one. The `resource_query` string a pooled track was named after
  is not stored; it is the track's `name` prefix.
- A run whose fragment entry reads its cell metadata from a local file
  (`meta: {file_name: …}`, F7) writes three more root attributes,
  `metadata_files` (absolute paths), `metadata_file_sizes` (bytes) and
  `metadata_file_mtimes` (UTC, ISO 8601), one element per distinct local
  file in job order. A local file is outside the GRR, so the run is
  reproducible only where that file is; the attributes at least state which
  file it was. A run that reads no local file writes none of them, so the
  six attributes above remain the whole set for every other run. (F12 says
  "root attributes are unchanged"; F7, added the same day, is what added
  these, and the code follows F7.)

**This is the one deliberate break of the layout since the 2026.9
releases.** A reader of `/tracks.resource_id` must switch to
`resource_ids`, and tells the two layouts apart by the field itself
(`'resource_ids' in h5['tracks'].dtype.names`); the user page says so
beside the table. The `gain_version` root attribute does not tell them
apart: a development build made after the 2026.9.7 release reports a 2026.9
version but already writes the new layout. No layout version attribute was
added: the field's presence is the test.

Rejected:

- **A `/track_resources` pair table** (track index, resource id), with
  `/tracks` unchanged. It was the first decision and was withdrawn: it
  keeps `resource_id` meaningful only for single-resource tracks, makes
  every reader join two tables to answer "what was this column computed
  from", and is a fourth dataset to keep consistent with the others for a
  question one column answers.
- **Keeping `resource_id` beside a new `resource_ids`.** It would spare old
  readers the rename, at the price of two columns with one meaning, one of
  them empty or arbitrary for every pooled track, kept in sync forever.
- **Storing the query and re-resolving it at read time.** The matched set
  depends on the repository's contents the day it is resolved; a file
  that says "whatever `sc/atac_fragments/*` matches" stops describing its
  own columns the first time a sample is added.

Accepted cost: a pooled track repeats the whole id list. In the largest
study of the single-cell demo GRR a pooled entry matches 518 resources,
so every class track of that entry carries a string of 518 ids, tens of
kilobytes per row of `/tracks`. Next to `/values` that is noise, and it was
judged not worth a second table.

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

*Amended by gain#1212:* the type restriction is the search's own
`resource_type` filter, not the binner's. It was the binner's because
`search_resources` could only answer a type out of the full-text index, which
a repository need not have; a type with no `search_term` beside it is answered
from the resources themselves now, so the binner asks for what it wants and
the expansion of equivalent `type:` spellings comes with it. The sentence
about `search_term` stands: it remains the one key that needs an index.

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

> **Note (gain#1828):** the resource-level key is now `aggregator`. It
> replaced `position_aggregator` in 2026.7.5 (gain#462).

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
0 or less restores one task per region), and a task writes one chunk
per region of its bundle **under the same name as before**. The chunks,
the writer, the file and the chunk sharing between run definitions are
unchanged; only the task count and the rerun granularity move — a task
missing any of its chunks is recomputed in full (the executor's check of
a task's output files), and its id names its bundle by its first and
last region rather than listing it, so it stays a file name in the
task-status directory. The packing itself is `bundle_regions` in
`gain.utils.regions`, beside `split_into_regions`, which cuts the other
way. Splitting a chromosome
across tasks was considered and dropped: the writer assembles one slab per
user region and the chunk name encodes the region bounds, and the tail it
would shorten is one chromosome-sized task.

**Amended by #1301 (2026-09-09): `--task-budget` 0 or less is one task per
track, and a task opens its resource once.** Two changes to the amendment
above. First, 0 or less no longer means one task per region — it means no
cut at all, so a whole run is one bundle and a run is one task per track.
One task per region is a budget of 1, which reaches it by the ordinary
rule, since a region is never split. The default is unchanged and both
ends stay opt-in. Second, `Binner.bin_track` is now bundle-level: it takes
the bundle's regions and *yields* one float64 array per region, in order,
so `PositionScoreBinner` opens its score once per task instead of once per
region, and the caller saves each array as it arrives — peak memory stays
one region, which is what makes a whole-run bundle affordable.

This buys task hygiene, not time: the open is part of the sub-1 ms setup
measured for #1214, so a run saves well under a second by it. What the
budget really trades is parallelism against task count, and at 0 a track's
work is one task, so the achievable parallelism is the track count — right
for many tracks on few workers, wrong for a handful of tracks on many.
The rerun granularity moves with it, as it did for #1214: at 0 a failed
task recomputes its whole track. Chunk names, task ids, the writer and
chunk sharing between run definitions all stay as they were; a whole-run
bundle simply gets an id spanning its first to its last region.

**Amended by #1325 (2026-09-30): a task binds a *job*, and the binding is
a context manager; `bin_track` is gone.** This reverses, on purpose, the
generator shape the #1301 amendment introduced, while keeping everything
that amendment bought. `Binner.parse_entry` now resolves an entry into
*jobs* — what one task binds to, each knowing its tracks at parse time —
and `Binner.bind(job, grr)` returns a context manager whose value answers
`bin_region(region, bin_size)` with a float64 block of shape
`(bins of the region, tracks of the job)`. The task function holds the
binding in a plain `with`, asks for each region of its bundle in turn,
and saves column *i* of each block to track *i*'s chunk before asking for
the next — so it is still one resource open per task and one region of
memory, and a block of any other shape fails the task before any of that
region's chunks is written. `RunDefinition` carries the jobs; its flat
track list, which the writer and `/tracks` read, is every job's tracks in
job order. A `position_score_binner` job holds exactly one track, so
tracks, their order, task ids, chunk names and the file are unchanged —
checked byte for byte against the pre-change tool at the default budget,
1 and 0.

Why the generator went:

- **The lifetime was prose, not type.** A generator holding a resource
  across its yields is released only when exhausted or closed, and a
  failed save keeps the exception, whose traceback keeps the suspended
  generator. So the caller had to wrap it in `contextlib.closing`, and
  why was explained in two docstrings. A context manager makes the
  release the `with`'s job, and the type says so.
- **The `Generator` return type confined every plugin.** `closing` calls
  `.close()` unconditionally, so the protocol had to name `Generator`:
  a custom iterator class, or a plain iterator without `close()`, was
  a type error or an `AttributeError` at the end of the `with`. `bind`
  may return any context manager, from any class.
- **Yield order was unenforced.** The caller paired arrays to regions
  with `zip(..., strict=True)`, which counts but does not order, so a
  binner yielding out of turn wrote good arrays under the wrong names.
  The caller now names the region it asks for, so there is no order to
  keep.

The job, not the track, is the unit because a binner that reads several
tracks from one resource open (the planned `fragment_score_binner`,
#1203) needs the task to hand it all of them at once; the caller already
saves a multi-column block without assuming one track per job.
`.out-of-scope/binner-lifetime-protocol.md`, which recorded the earlier
decline of this change pending a second binner kind, is removed with it.

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

**A contig the score never mentions is part of that read, not of the binner
(#1211).** The tracer bullet shipped a stand-in in the binning module,
`_uncovered_bins`, because the region read refused an absent contig: it
rebuilt, per bin, the fold this read already performs. A genome-wide run
over a track that skips a chromosome is the normal case rather than an
error, so the two *aggregating* reads of `PositionScore` —
`get_scores_in_bins` and `get_scores_in_region_agg`, with their singular
forms — now treat a contig absent from `get_all_chromosomes()` as a single
`(None, region_width)` run and fold it as they fold any uncovered run. The
choice of run source is made above the shared run generator, so the
per-position read keeps refusing and no argument travels down to say which
consumer is reading (ADR 0008). The backend is never asked for the absent
contig, so the per-kind record transform's refusal and each table's own
stay exactly as they were.

The cost of the exemption, accepted deliberately: an unknown contig is a
plausible typo, and these two reads no longer catch it — a misspelled
contig bins to NaN rather than raising. That is the price of the absent
contig being ordinary, and it is confined to the reads that answer a
question about a *window*; every read that materialises a contig's
positions, the point read included, still refuses.

The annotation path is unaffected either way: `PositionScoreAnnotator`
short-circuits on `get_all_chromosomes()` for every annotatable shape
before it picks a read, so neither the point read nor the region-aggregating
read has ever been reached with a contig the score does not have.

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
