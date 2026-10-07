# 35. A fragment belongs to the bin of its start, and its grouping is declared

- **Status:** accepted
- **Date:** 2026-10-05
- **Issues:** [gain#1738](https://github.com/iossifovlab/gain/issues/1738) (the epic);
  slices [gain#1739](https://github.com/iossifovlab/gain/issues/1739) (`sum` and
  `product`), [gain#1325](https://github.com/iossifovlab/gain/issues/1325) (the
  `bind` / `bin_region` protocol), [gain#1740](https://github.com/iossifovlab/gain/issues/1740)
  (the shared binned fold), [gain#1203](https://github.com/iossifovlab/gain/issues/1203)
  (the kind), [gain#1741](https://github.com/iossifovlab/gain/issues/1741) (the
  convention, the defaults, the dry-run report, the dropped counts),
  [gain#1759](https://github.com/iossifovlab/gain/issues/1759) (raw-value grouping),
  [gain#1742](https://github.com/iossifovlab/gain/issues/1742) (the docs and this record),
  [gain#1793](https://github.com/iossifovlab/gain/issues/1793) (the amendment: the
  `value` / `aggregate` keys and the fragment-count default);
  content [grr_nygc_single_cell_demo#3](https://github.com/iossifovlab/grr_nygc_single_cell_demo/issues/3)
- **Design:** `seqpipe/genomics-toolbox`
  `docs/2026-09-30-gain-fragment-binner-design.md` (decisions F1–F21). The
  numbers below cite it; the statements are this record's own.
- **Amended by:** [ADR 0037](0037-a-fragment-can-be-binned-by-its-overlap-or-its-coverage.md),
  which adds the `fragment_length` and `coverage_profile` modes, the
  `uncovered_value` key and the `name` key. Start assignment stays the
  default mode.
- **Amends:** [ADR 0025](0025-binning-tool-writes-one-hdf5-matrix-from-a-fixed-grid.md),
  whose D2 amendment of the same date records the `/tracks` layout this kind
  needed (`group`, `resource_ids`, the local-file root attributes).

## Context

`binning_tool` (ADR 0025) shipped with one binner kind, `position_score_binner`:
one resource, one score, one track, reduced per bin by the score's aggregator.
The entry-point group `gain.binning.binners` was the extension point, and a
fragment kind was the intended second member.

The workload behind it is single-cell ATAC. A `fragment_score` resource holds
one sample's fragments, each an interval carrying the barcode of the cell it
came from (the `cell` score) and, usually, the read pairs behind it (`count`).
The single-cell demo GRR has 662 such resources across four studies, the
largest of them 518 samples. What an analyst wants per bin is not one number
per resource but one per *group of cells*: per cell type, pooled across the
samples of a study; or per cell, for a per-cell matrix; or, for a plain
interval collection, the number of intervals.

A prototype outside gain did this. It anchored bins at each region's start,
created group aggregators lazily as groups appeared (so its output was
ragged), passed `None` through for empty bins, guessed that a resource was
"single-cell" from its score names, built a pandas query string from label
values, and fell back to the unfiltered metadata table when a sample label
was missing. Most of those are what this record decides against.

The same decisions will be asked again of the next kinds — a CNV-collection
binner, an allele-score binner — so they are recorded here as the ones a new
kind is measured against.

## Decision

### A fragment belongs to the bin containing its start, and to no other (F5)

A fragment contributes to exactly one bin: the one holding its start
position, however far it extends. This is the partition the fragment plane's
`get_fragment_scores_starting_in_region` read already defines (a record is
owned by the window containing its begin), so adjacent regions — and adjacent
tasks — see every fragment exactly once; a count column sums to the fragment
count; and pooling resources is exact, because merging the per-resource
streams by start position and folding them gives the same answer as folding
one merged file.

Every other assignment would break one of those. Overlap assignment counts a
fragment in every bin it touches, so column sums stop meaning anything and a
region boundary needs de-duplication across tasks. Midpoint and cut-site
assignment are reasonable for ATAC specifically (the Tn5 cut site is the
biological event), but they are a choice of *what the interval means*, which
the resource does not declare. They are deferred, not rejected (below).

### An empty bin holds the aggregator's empty value; there is no replacement (F9)

A bin with no fragment of a group holds `0` for `count` and `sum`, which have
an answer for nothing, and `NaN` for `mean`, `max`, `min`, `median` and
`product`, which have none. The values live in one table,
`EMPTY_BIN_VALUES` in `gain.genomic_resources.genomic_scores.aggregation`,
which is also the list of aggregators the kind accepts: an aggregator
without an empty value is refused at parse time.

A contig a resource lacks is treated exactly as empty bins, by the same rule.
A pooled column must not be poisoned by one sample that has no fragments on a
contig, and a run over a genome's contig list must not fail on the first
sample that skips one. This is the fragment counterpart of the
position-score decision in ADR 0025 (#1211) that an absent contig is one
uncovered run.

Unlike `position_score_binner`, the kind has **no `none_value_replacement`
key**. A replacement is fed to the aggregator for each uncovered *position*;
fragments have no positions to be uncovered, only bins without fragments, and
for the two aggregators where "no fragments" has a numeric answer the rule
already gives it. A user who wants `0` instead of `NaN` for a `mean` is
asking for a different statistic, and `fillna` is one line on the matrix.

### Grouping is declared by a label, never sniffed (F8)

Without a `group` key, a resource is grouped by its cell metadata if and only
if it carries the label `cell_meta_resource_id`; any other resource is the one
group `all` (`DEFAULT_GROUP`), so its track is `<resource id>:all` and every
fragment track has a group. The prototype decided "single-cell" from the
presence and type of `cell` and `count` scores; that guess is exactly what
this record refuses: the label is the declaration, written by a curator on
purpose, and a score's name is not. A resource that *looks* single-cell (it has `cell` and `count`) but has
no label is binned as `all`, and `--dry-run` warns — the warning is the whole
of the heuristic that remains.

Without `value` and `aggregate` keys, every resource has the fragments that
start in each bin counted, as `value: {value: 1}` and `aggregate: {mode:
fragment_start, aggregator: sum}`, whatever scores it has. The read pairs
behind each fragment are summed only on request, with `value: {score_id:
count}`.

A pooled entry is one job, so its resources must fall in the same tier: all
labelled or none, all naming one metadata table. Disagreement is a
parse-time error listing both sides, never a majority vote.

**Amended by #1793 (2026-10-06): the default no longer depends on a `count`
score.** As first accepted, an entry without `aggregate` summed the `int`
score named `count` (`COUNT_SCORE`) of a resource that had one, and counted
the fragments of any other; a pooled entry's resources therefore also had to
agree on having an `int` `count`. That trigger was a score's name deciding
what a column means, which is the sniffing the grouping rule above refuses:
two resources of one study, one with `count` and one without, gave columns
of different statistics from the same two-line entry, and the pooled refusal
existed only to keep that guess consistent. The number of fragments is the
common ATAC count matrix and needs no score, so it is the default for every
resource (decision F28 of `seqpipe/genomics-toolbox`
`docs/2026-10-06-gain-fragment-binner-modes-design.md`), and the pooled
`count` agreement rule goes with the trigger. The same change split the old
`aggregate: {score | value, aggregator}` into `value` (what one fragment
adds) and `aggregate` (how a bin reduces the values; F23).

### The metadata convention is fixed, and checked before any work runs (F7)

| Where | What | Name (constant) |
| --- | --- | --- |
| fragment resource label | id of the `data_frame` metadata resource | `cell_meta_resource_id` (`CELL_META_RESOURCE_ID_LABEL`) |
| fragment resource label | this sample's key in the table | `sample_id` (`SAMPLE_ID_LABEL`) |
| metadata table column | sample key | `sample_id` (`SAMPLE_ID_COLUMN`) |
| metadata table column | cell barcode | `barcode` (`BARCODE_COLUMN`) |
| metadata table column | default group | `class` (`CLASS_COLUMN`) |

An omitted `meta` is `{resource_label: cell_meta_resource_id, filter:
[{column: sample_id, label: sample_id}]}`, and the barcode is the fragment
score `cell`. The constants live in `gain.binning.fragment_binner`, and every
other mention refers to them.

All of it is checked at parse time, in the parent, before a task is built: the
label exists and names a `data_frame` resource the repository has; the table
has every column the entry uses; every filter label exists on the resource and
is a single value (a list-valued label is an error, not an alternative); no
barcode occurs on two of one resource's selected rows; the selected rows name
at least one group; a pooled entry's resources resolve to one table. A
convention discovered wrong on a worker an hour into a cluster run is the
failure this prevents.

The filter is applied as a boolean mask over the table, conjunct by conjunct,
and never formatted into a query string: label values are data, and a sample
id with a quote in it must not change the query's meaning. There is no
fallback to the unfiltered table when a label is missing; a missing label is
an error.

The explicit `group` / `meta` forms are always available for a table that
predates the convention, so the binner does not depend on the demo GRR's
adoption of it (grr_nygc_single_cell_demo#3).

### Raw-value grouping prefixes a pooled group with its sample (F21)

`group: {group_score_id: S}` makes one track per distinct value of the `str`
score `S` — with `S = cell`, a per-cell matrix. The group set is not discovered
by reading the data: it is read at parse time from the score's **full**
categorical histogram in the resource's statistics (the full file of
ADR 0032, not its truncated sidecar), sorted. So the tracks are known before
any task runs, exactly as for every other entry, and the writer preallocates
`/values` as before. A resource whose full histogram is absent, unreadable,
truncated or annulled is a parse-time error saying to build (and, in a DVC
repository, pull) its statistics. The grouping score may not be the `value`
score.

Unpooled, the group is the bare value. Pooled, the same 10x barcode recurs in
every sample while naming a different cell, so the group is
`<sample_id>:<value>`, by the resource's `sample_id` label, and the track
`<resource_query>:<sample_id>:<value>`. Every pooled resource must carry the
label as one value, without a `:` (so a group splits back one way only), and
no two may share one (so two samples' cells never reach one track); each is a
parse-time error naming the resources.

**Rejected: refusing pooled raw-value grouping outright.** It was the simpler
rule — a pooled per-cell matrix is unpooled per-cell matrices side by side —
and it lost to the prefix, which is also how a study already names a cell:
zemke2023's metadata table spells a cell `<sample>_<barcode>`.

Accepted costs: the full histogram of a `cell` score is tens of megabytes per
resource, read at parse time; and a per-cell matrix is 5,000 to 15,000
columns per sample. `--dry-run` reports the group count read from the
histograms, so the width is announced before the matrix is built.

Raw-value grouping was first deferred, on the assumption that the group set
is unknown until the data is read. It is not — the statistics list it — so it
shipped. It is **not** among the deferrals below.

### A local metadata file is accepted, warned about, and recorded (F7)

`meta: {file_name, file_format, file_separator}` reads the table from a local
CSV, TSV or Excel file with the same column conventions. A relative path is
resolved against the run definition's directory, not the process's working
directory, so a run definition and its table move together.

A run definition naming a local path cannot be re-run elsewhere, which is the
reproducibility that `input_reference_genome` and the GRR-only resource
queries exist to give (ADR 0025, D6). The source was first deferred for that
reason, and then accepted with the cost stated rather than hidden: `--dry-run` warns that the run is not reproducible
elsewhere, and the output file records each local file's absolute path, size
in bytes and modification time (UTC, ISO 8601) in the `metadata_files`,
`metadata_file_sizes` and `metadata_file_mtimes` root attributes (ADR 0025's
D2 amendment). The attributes say which file it was; they do not make the
run reproducible.

### Dropped fragments are counted, not fatal (F13)

With a grouping, a fragment whose barcode is not among its resource's
selected rows, whose row's group is empty, or whose raw value is missing or
not listed by the histogram reaches no track. A metadata table that lists
only some of a sample's barcodes is ordinary, so this is not an error. It is
counted per (resource, region) and logged, so a
table that matches almost nothing is visible in the log rather than as a
quietly empty matrix.

## Deferred

From the design's current Out of Scope list. Each is a key or a kind that
could be added without changing what is decided above.

ADR 0037 adds overlap assignment, as the `fragment_length` and
`coverage_profile` modes, and the `name` key. It also adds
`uncovered_value` to those two modes. Midpoint and cut-site assignment
stay deferred.

- **Overlap, midpoint and cut-site assignment**, and any `assign:` key. Start
  assignment is the only one; the others are an interpretation of the interval
  the resource does not declare.
- **Splitting a pooled job across samples with a reduce step.** A pooled job's
  parallelism comes from regions only; `--task-budget` counts bases, so a
  pooled job doing N times the I/O per base is visible through the dry run,
  not corrected for. Accepted for now, including for the 518-resource study.
- **A key choosing the pooled raw-value prefix.** It is always the `sample_id`
  label.
- **A `name:` key**, on any kind (ADR 0025, D10's reasons hold).
- **Per-resource aggregator overrides inside one entry.** One aggregator per
  entry; two entries for two.
- **Metadata from anything but a `data_frame` resource or a local CSV, TSV or
  Excel file.**
- **List-valued labels as filter operands.** A label whose value is a list is
  a parse-time error in a `filter`.
- **A `none_value_replacement` key on this kind** (see above).
- **CNV-collection and allele-score binners.** The shared binned fold
  (`fold_into_bins`, F4) and the `/tracks` layout of ADR 0025's D2 amendment
  are designed so that they can reuse both; a CNV collection is a
  `fragment_score` resource and can already be counted by this kind under
  start assignment.

## Consequences

- Start assignment is the kind's semantics. A change to it, or to the
  starting-in read it rides on, changes every fragment matrix.
- The convention's five names are now a contract between the binner and the
  GRR's curators. Renaming a label or a column in the GRR silently changes
  which resources are grouped by default (a resource losing
  `cell_meta_resource_id` becomes an `all` track, with a warning only if it
  has `cell` and `count`).
- A per-cell matrix can be very wide, and its group set comes from
  statistics, not data: a resource whose statistics are stale bins against
  the stale value list, and fragments with values the histogram lacks are
  dropped and counted, not added.
- A run that reads a local metadata file is reproducible only where that
  file is; the root attributes record it, nothing enforces it. The file is
  an input of every task, so a rerun after editing it recomputes the
  chunks. A rerun does not notice a changed resource, or a changed table
  read from a `data_frame` resource; `--force` recomputes.
- A future binner kind is expected to answer the same questions this one did,
  in the same places: which bin a record belongs to, what an empty bin
  holds, how its grouping is declared rather than inferred, and what it
  writes to `/tracks.group` and `/tracks.resource_ids`.
