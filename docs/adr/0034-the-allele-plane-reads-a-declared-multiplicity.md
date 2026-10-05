# 34. The allele plane reads a declared multiplicity

- **Status:** accepted
- **Date:** 2026-10-05
- **Issues:** [gain#1749](https://github.com/iossifovlab/gain/issues/1749) (epic),
  slices [gain#1750](https://github.com/iossifovlab/gain/issues/1750)–[gain#1756](https://github.com/iossifovlab/gain/issues/1756);
  enforcement [gain#1757](https://github.com/iossifovlab/gain/issues/1757);
  found on the way [gain#1748](https://github.com/iossifovlab/gain/issues/1748);
  content [grr#44](https://github.com/iossifovlab/grr/issues/44),
  [grr_sfari#6](https://github.com/iossifovlab/grr_sfari/issues/6)
- **Design:** [the allele plane](https://github.com/seqpipe/genomics-toolbox/blob/master/docs/2026-09-30-gain-allele-plane-design.html)
  (2026-09-30, decisions A1–A11), which mirrors two earlier designs in the same
  directory: [the fragment-score API](https://github.com/seqpipe/genomics-toolbox/blob/master/docs/2026-09-02-gain-fragment-score-api-design.html)
  (2026-09-02, L1–L11) and [the allele folding read](https://github.com/seqpipe/genomics-toolbox/blob/master/docs/2026-09-03-gain-allele-folding-read-design.html)
  (2026-09-03, D1–D6)
- **Amends:** the read half of
  [`.out-of-scope/duplicate-allele-keys.md`](../../.out-of-scope/duplicate-allele-keys.md)

## Context

Three score kinds share `GenomicScore`. `PositionScore` and `FragmentScore`
each had a logical plane of `get_*` reads: a value tuple parallel to the
requested scores, the locus positional and everything else keyword-only, and a
singular form wrapped around each plural through `_resolve_single_score`.
`AlleleScore` had one logical read, the region fold
`get_allele_scores_in_region_agg` (gain#1132). Its point read was
`fetch_allele_scores(chrom, pos, ref, alt, scores=None, *, score_filter=None)`.
It was named `fetch_*` but sat on the logical plane. It returned a dict where
every other plane returns a tuple, took `scores` positionally, and had no
singular form. It answered the **first** row whose `ref` and `alt` matched.

The first-row rule mattered because repeated `(chrom, pos, ref, alt)` rows are
normal data. A full sweep of the GRR found them in 7 of 30 allele scores:
`hg38/scores/dbSNP`, `hg38/scores/dbNSFP4.9a`, `hg38/scores/AlphaMissense`,
`hg19/scores/AlphaMissense`, `hg19/scores/MPC`, and the gnomAD v2.1.1 liftover
`genomes` and `exomes`. Most of them come from a per-transcript dimension that
the config never declared (`.out-of-scope/duplicate-allele-keys.md` has the
sweep and the counts). The region fold saw all of those rows; the point read
saw one, chosen by file order. Nothing could ask for "every row of this
allele" or "the fold of this allele's rows". The accepted policy, first wins,
lived only in that out-of-scope note, and no test pinned it.

While mapping this, the design also found that `allele_score_mode` was parsed,
stored and documented but consulted by nothing. The annotator refactor of
2026-05-13 had dropped the dispatch. That was filed and fixed separately as
gain#1748; decision 4 below says how the two keys relate.

## Decision

1. **Two interfaces plus the fold, on a grid.** The plane has three loci and
   three reductions (design §3.1):

   | locus | bare: exactly one row | `_rows`: unreduced | `_agg`: folded |
   | --- | --- | --- | --- |
   | `for_allele(chrom, pos, ref, alt, *, …)` | built | built | built |
   | `at_position(chrom, pos, *, …)` | reserved | reserved | — |
   | `in_region(chrom, start, end, *, …)` | reserved | built | exists (gain#1132) |

   Every cell has a plural form and a singular form (the singular drops the
   `s` and resolves its one score through `_resolve_single_score`). The cells
   built are the ones that have a consumer:
   `get_allele_scores_for_allele` / `get_allele_score_for_allele`,
   `get_allele_scores_for_allele_rows` / `get_allele_score_for_allele_rows`,
   `get_allele_scores_in_region_rows` / `get_allele_score_in_region_rows`,
   and `get_allele_scores_for_allele_agg` / `get_allele_score_for_allele_agg`,
   beside the existing `get_allele_scores_in_region_agg`. The singular-plural
   marker already counts scores, so the reduction is a suffix: `_agg` already
   meant "rows folded", `_rows` means "rows unreduced", and the bare name means
   "exactly one row". The region `_rows` reads yield `AlleleEntry(pos, ref,
   alt, values)`, a `NamedTuple`, because there the position and nucleotides
   are new information and positional access would swap `ref` and `alt`. The
   `for_allele` reads return bare value tuples, because there the locus is the
   caller's own argument. `fetch_allele_scores` and `fetch_allele_records`
   were removed without a shim (gain#1755); neither had a caller outside gain.

2. **Multiplicity is a declared property of the resource.**
   `allele_multiplicity: one | many` is a top-level resource-config key,
   default `one`, exposed as `AlleleScore.multiplicity`
   (`AlleleScore.Multiplicity.ONE` / `.MANY`). A `one` resource promises
   unique allele keys and serves the bare read. A `many` resource has declared
   its extra dimension: the bare read refuses at the call, before reading
   anything, and callers use `_rows` or `_agg`. The `_rows` and `_agg` reads
   are allowed on both. Multiplicity means **rows**, not multi-valued fields
   inside one row (A1).

3. **Checked at build, with a per-read backstop.** The allele statistics build
   checks the declaration: on a `one` resource it reports the first repeated
   allele key and its row count (gain#1752). The bare read checks again on
   every call, counting the rows **before** the score filter, so a filter that
   hides one of the duplicates cannot turn a data error into a clean answer
   (gain#1753). That covers a payload that changed after its statistics did.
   Both raise `AlleleMultiplicityError(ValueError)`, which carries the
   resource id and, when known, the allele. Whether they raise or only warn is
   decided in one place, `allele_multiplicity_enforced()` in
   `gain.genomic_resources.resource_types`, which compares the installed
   version with `ALLELE_MULTIPLICITY_ENFORCEMENT_RELEASE` (`"2027.1.0"` when
   this was written). The constant sits beside
   `LEGACY_VOCABULARY_REMOVAL_RELEASE`, gain's existing pattern for two-step
   retirements ([ADR 0011](0011-deprecate-cnv-collection-vocabulary.md)).

4. **The plane is mode-blind (A10).** No read of the plane consults
   `allele_score_mode`. `get_allele_scores_for_allele` for an indel against a
   `substitutions` table honestly answers `None`; routing that indel to the
   region fold is the annotator's job, and since gain#1748 it does that again.
   `allele_multiplicity` and `allele_score_mode` are siblings: both are claims
   about the table's semantics, both sit at the top of the resource config, and
   both are read by the annotator. The annotator's allele mode has one rule
   per key: the mode decides whether a `VCFAllele` is matched exactly at all,
   and the multiplicity decides how. An exact match calls the bare read on a
   `one` resource and `get_allele_scores_for_allele_agg` on a `many` resource
   (A8). Every non-bool attribute already resolves an aggregator, so the
   `many` path needs no pipeline-config change.

5. **Absent, filtered and refused (A7), and the D3 asymmetry.**

   | situation | bare | `_rows` | `for_allele_agg` |
   | --- | --- | --- | --- |
   | no row for the allele | `None` | empty | `None` |
   | rows exist, the filter rejects all | `None` | empty | an aggregate over an empty selection, not `None` |
   | exactly one row | its values | one entry | fold of one |
   | 2+ rows on a `one` resource | first row and a warning; raises from the enforcement release | all rows | fold of all |
   | any allele on a `many` resource | raises at the call | all rows | fold of all |
   | unknown contig | raises, as every allele read does | | |

   The bare read and the `_rows` reads give one answer for both "nothing
   there" and "everything filtered": `None` for the bare read, and an empty
   list or an exhausted generator for `_rows`, never `None`. The folds keep the
   two apart, as D3 of the folding-read design decided: `None` is absence
   judged before the filter, and an all-filtered allele or region folds to an
   `AlleleAggregate` whose aggregators answered for no rows. The new
   `for_allele_agg` cell follows its region sibling rather than the bare read.
   This is a deliberate difference inside one plane. The region fold is
   shipped and documented, and the fragment plane already answers no `None`
   at all ([ADR 0017](0017-score-filtering-is-a-score-capability.md),
   Consequences, says why the score kinds differ here). This record states
   the asymmetry rather than changing either side.

6. **Two releases (A11).** The config schema is strict, so content cannot
   declare `many` until a gain release accepts the key, and a release that
   enforces `one` would break the seven resources until content catches up.
   - **Release N**, the first after 2026.9.7, ships the key, the plane, the
     annotator port, the removals and the build check. An undeclared
     duplicate makes the bare read answer the first row and warn once per
     resource per process, and makes the statistics build warn.
   - **Between N and N+1**, content declares `allele_multiplicity: many`, and
     per-score aggregators where needed, on the seven resources in `grr`
     (grr#44) and `grr_sfari` (grr_sfari#6).
   - **The release named by `ALLELE_MULTIPLICITY_ENFORCEMENT_RELEASE`**
     enforces (gain#1757). The bare read raises `AlleleMultiplicityError`, and
     the statistics build fails before writing anything.

## Why it was scoped this way

**Refuse rather than pick (A2).** File order does not define "the value", and
first-wins is a third semantic that no other plane has. The design was
reconsidered on 2026-10-01. The alternative was a bare read that folds each
allele's rows with the resource's per-score aggregator. That would have
removed the declaration, the build check and the two-release rollout. It was
reverted at the next step: without a gate the per-transcript dimension stays
undeclared, and a silent default fold is the same guess as first-wins with a
different rule. The net effect is stated on purpose. Undeclared duplicates fail
loudly; declared duplicates fold. The fold arrives only after the resource
author has said `many` and chosen the aggregator per score.

**A declaration, not a per-read refusal alone (A3).** A refusal only at the
read would raise in the middle of an annotation run, on whichever allele
happened to be duplicated, for data the user did not write. A default of
`many` was rejected too: the bare read would then refuse on every undeclared
resource.

**The build check is not an ADR 0027 record validator.** The design placed the
check in the record-validation registry of
[ADR 0027](0027-record-validation-is-a-registry-in-the-statistics-package.md).
Triage of gain#1752 ruled that out. The registry's array door has no `ref` /
`alt` columns ([ADR 0008](0008-scan-owns-validation.md)'s batch shape, which
the out-of-scope note defends). So the check rides the allele statistic's own
accumulator, which gain#780 already feeds the key columns on both the
per-record door and the bulk door. The registry is unchanged.

**The grid is fixed; cells are built when they have a consumer (A4).** Fixing
the grid now means a later `at_position` cell, or `_in_bins` on the allele
plane, has a reserved name and meaning. Building all twelve methods now would
have added reads that nothing calls.

**The plane does not enforce the mode (A10).** Retiring `allele_score_mode`
was the design's first recommendation. The history reversed it: nine GRR
declarations rely on the key, and losing it in May was an accident. Making the
plane enforce it would put routing policy into the reads. The reads stay
honest about the table, and the annotator keeps the routing.

## Consequences

- An `AlleleScore` read says in its name how many rows it answers. Code that
  needs every row of an allele says `_rows`; code that needs one value either
  relies on the resource's `one` promise or folds with `_agg`.
- Content owners of the seven resources must add `allele_multiplicity: many`,
  and a per-score `aggregator` wherever the class default (`max` for numbers,
  `list` for strings) is not wanted, before the enforcement release. Until
  then, every annotation run over one of them logs a warning per resource.
- Tests that pin the first-row answer exist only for the warning releases.
  Nothing else may depend on file order, and gain#1757 deletes those tests
  when it flips the constant.
- `ALLELE_MULTIPLICITY_ENFORCEMENT_RELEASE` and
  `LEGACY_VOCABULARY_REMOVAL_RELEASE` currently have the same value. The
  `core/tests/conftest.py` guard that collected legacy-vocabulary notices
  matched on the release string alone, so it picked up the new warning. It
  now matches the notices' full sentence. A third two-step retirement should
  expect the same collision.
- The plane has three reads that answer "nothing" in three shapes (`None`, an
  empty list or generator, and an aggregate over an empty selection). The
  table in decision 5 is the reference. The developer guide
  (`docs/source/development/resources/scores.rst`) repeats it for users.
- Out of scope, and still open: `at_position` cells; `_in_bins` on the allele
  plane; retiring `allele_score_mode`; changing `AlleleAggregate` or the
  region fold; treating an unknown contig as uncovered.
