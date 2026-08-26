# 20. Extended score statistics: segments are value-aware, coverage is not

- **Status:** accepted
- **Date:** 2026-08-24
- **Issues:** [gain#770](https://github.com/iossifovlab/gain/issues/770) (the
  epic and its decision record), [gain#771](https://github.com/iossifovlab/gain/issues/771)
  (this record), [gain#773](https://github.com/iossifovlab/gain/issues/773)
  (the allele-classification amendment),
  [gain#848](https://github.com/iossifovlab/gain/issues/848)
  (the scanned-tuple amendment)

## Context

The GRR resource info page documents a tabular score today with value
histograms and min/max — it says what values a score holds, and nothing about
**where** it holds them or **how its content is structured**. "Does this score
cover chrX?", "is it one value per base or long constant runs?", "what does
this allele score actually contain — SNVs, indels, something else?" are all
questions the page cannot answer. gain#770 extends the computed statistics to
answer them, per chromosome and globally, for `position_score` and
`allele_score` (fragment scores ride the shared machinery; resources still
typed `np_score` take the allele path automatically).

Doing that required giving two everyday words a precise meaning, because both
are used loosely everywhere else: what is one *segment* of a score, and when
is a position *covered*? The definitions decide what the statistics mean, so
they were decided first, before any statistic is implemented — and one of them
was decided **twice**. The original decision record (2026-08-11) defined a
segment value-blind; on 2026-08-24, before any implementation existed, that
was reversed to the value-aware definition below. The reversal is part of this
record, because the rejected definition will look attractive again — it is
simpler and it made an identity hold for free.

## Decision

### Segment — value-aware

A **segment** is a maximal run of touching-or-overlapping table rows carrying
**equal values**: the row's **scanned** score-column tuple compares equal,
with NA equal to NA, and floats compared exactly as stored. *Scanned* means
the score columns the statistics scan fetches — every declared score except
those whose histogram config resolves to the null histogram, whether asked
for, fallen to, or nullified at build time when no min/max could be computed
(the resolution rules live with the histogram-config builder). Per-resource —
one segmentation regardless of how many score columns the resource declares.
A row that touches its neighbour but differs in any scanned score column
starts a new segment.

Three details of "equal" are decisions, not defaults:

- **Whole scanned tuple**, not per-column *(amended 2026-08-24, gain#848 —
  originally "whole-row tuple": the statistics scan never fetches a
  null-histogram column, so such a column never entered the comparison, and
  the wording now records the implemented decision, made in gain#772 as "no
  read-set change")*. However many score columns a resource declares, it has
  one segmentation; a change in any *scanned* column breaks the segment.
- **NA equals NA.** NA is a value like any other, so a run of NA rows merges
  into a segment. Segments exist wherever rows exist; a segment is not
  evidence of a usable value.
- **Floats compare exactly, as stored.** No tolerance. A tolerance would need
  a constant nobody can justify, would make segmentation non-transitive
  (a≈b, b≈c, a≉c), and would make the statistics depend on comparison policy
  rather than on the data.

A segment is also **not a table row as stored** — that is a *fragment* (the
fragment-score vocabulary, ADR [0003](0003-fragment-score-vocabulary.md)):
fragments are unmerged and overlapping fragments each count, deliberately the
opposite of the merged segment view. Both views are collected, because they
answer different questions.

### Covered position — value-blind

A position is **covered** when at least one table row spans it — union
semantics, ignoring values entirely, computed independently of segments. This
is the definition the coverage documentation needs: "is there data here at
all?" is a question about row extents, not about values.

The two definitions deliberately do not compose. **Σ segment lengths is not
the covered-position count**: where rows with different values overlap, their
segments overlap too, and the sum double-counts the shared positions. Equality
holds only for a resource with no different-valued overlap. Nothing may
present the sum of segment lengths as coverage; coverage has its own
statistic.

### Allele classification — strict VCF anchoring

Allele-score rows are classified from ref/alt as written, VCF-anchored:
**substitution** is strictly 1→1; **insertion** and **deletion** are the
anchored forms (length = the length difference); **complex** is everything
else, *including MNVs*; a counted **other** bucket absorbs what does not parse
(N, symbolic alleles, malformed pairs), so the class totals always sum to the
row count. MNVs are not "substitutions of length n": keeping substitution
strictly 1→1 is what makes the 4×4 ref→alt substitution matrix exact rather
than a projection of multi-base events.

*Amended by gain#773:* "as written" left **case** unstated, and byte-exact
anchoring makes case load-bearing — `a→ag` anchors, `A→ag` does not, so the
same event written two ways would land in two different classes. Both alleles
are therefore **upper-cased before classification**: a soft-masked lowercase
base classifies as the base it masks instead of silently inflating `complex`.
What is still not `ACGT` afterwards — `N`, a symbolic allele such as `<DEL>`,
the missing-allele `*`, an empty string — is exactly what `other` counts. The
same amendment settles the identity pair `A→A`: the 1→1 rule is read
literally, so it is a substitution and the substitution matrix has a populated
diagonal. Whether to *render* that diagonal belongs to the matrix, not to the
classification.

Two further consequences of "the classes sum to the row count", both decided
the same way. A row may carry **no allele at all** — a table configures its
ref and alt columns independently, and a VCF `ALT` of `.` yields a record with
no alternative — and such a row is `other`, not an error and not a dropped
row: a score with no ref column classifies as entirely `other` rather than
failing its build, because coverage documentation is not a validation gate
(ADR 0008 keeps validation in the scan). And the ins/del **length is signed**
— positive when the alternative adds bases, negative when it removes them —
so one number serves both anchored classes and the histograms can share a
binning; "length = the length difference" above is that difference taken
alt-minus-ref, not an absolute value.

### Bins, storage, rollout

- **Fixed log-scale bins, a code-level constant.** Length histograms
  (segments, fragments, indels) use one fixed binning everywhere, so
  per-chromosome results merge into exact global ones at build time — no
  second pass, no approximate merge, and chunked scans merge exactly for the
  same reason.
- **Raw counts stored; fractions at render.** The statistics file holds
  counts only. Coverage *fractions* need chromosome lengths, which belong to
  a reference genome, not to the score — so they are computed at render time
  from a resolvable genome (bigWig header as fallback). The stored statistics
  stay genome-independent, and rendering can improve without rebuilding any
  resource.
- **Lazy rollout; `calc_statistics_hash` untouched.** The new statistics do
  not enter the statistics hash, so no existing resource is invalidated.
  Statistics appear as resources are rebuilt; the page renders "not computed"
  where they are absent.
- **The rollout lever is a forced rebuild** — verified and pinned in #774.
  Forcing is therefore the only deliberate way to put the new statistics on
  an already-built resource: `grr_manage resource-stats -r <resource_id> -f`
  for one named resource, or `resource-info -f` when its page must re-render
  too. The scope of each command, and the follow-up needed for the
  repository-global artifacts, belong to the `grr_manage` documentation
  rather than here.

*Amended by gain#779:* the bullet above names the length histograms that
share the fixed ladder — segments, fragments, **indels** — and the ins/del
histograms duly use it, binning the length change absolutely, since the class
already names the direction and the shared bin index refuses a length below 1.
The **complex `(len_ref, len_alt)` grid deliberately does not.** Its cells are
the two lengths **exactly**, each clamped at a code-level maximum of 64, so
the grid is a sparse map over a bounded 64×64 square.

The ladder was the obvious choice and is wrong here, for a reason specific to
this statistic. Its first bin is exactly length 1, which no complex pair can
have — a 1→1 pair is a *substitution* — so the grid's `(0,0)` cell would be
empty by construction. Its second bin is `{2, 3}`, so a 2→3 complex would land
in the same cell as a 2bp *and* a 3bp MNV, and the diagonal would stop meaning
"MNV of n bases" — which is precisely what the grid exists to show. With exact
cells, `(n, n)` is an MNV of exactly n bases and `2→3` sits one cell off the
diagonal from `3→3`.

The clamp is **total**: every complex row lands in exactly one cell, so the
grid's total is the `complex` class count and no overflow counter is needed.
It also bounds the stored cells, which is what keeps the merge exact and
order-independent — a *cap on distinct keys* would not, since which keys
survived would depend on the order the rows arrived in, and this file is
required to be byte-identical however a resource was chunked. For the same
reason the cells are **written sorted**, not in encounter order.

One caveat follows from the clamp and is worth stating rather than
discovering: `(64, 64)` is the one diagonal cell that does not mean "MNV". A
pair whose sides are both ≥64 but unequal — a 5000→70 complex — lands there
too.

Like the clamp, the ladder constant is part of the stored format: neither may
change once resources carry statistics built from it.

## Rejected alternatives

**Value-blind union segments — the original choice, reversed.** As first
decided, a segment was a maximal run of touching-or-overlapping rows with
values ignored. It bought two real simplifications: covered positions =
Σ segment lengths *by construction*, and no definition of "equal" needed — no
multi-column question, no NA question, no float question. It was given up
because a value-blind segment documents only row adjacency, and row adjacency
is what the coverage statistics already state: its total duplicates the
covered count, and its length histogram describes gap structure, which the
coverage table and the fragment view carry between them. The epic's goal is
coverage *and content structure*, and only a value-aware segment says anything
about content — one value per base and a megabase constant run are different
resources, and value-blind segmentation cannot tell them apart. The price of
the reversal is recorded honestly below.

**Per-score-column segmentation.** Each score column with its own
segmentation — the value-aware definition a statistician might expect.
Rejected: N columns mean N segment counts and N histograms per chromosome,
multiplying the statistics surface and the page for a distinction no current
consumer asks about, and losing the one-segmentation-per-resource shape the
rest of the statistics share. The scanned tuple still breaks wherever any
scanned column changes, so it bounds every per-scanned-column segmentation
from below; a per-column view can be added later without disturbing this one.

**Widening the scan to the literal whole row** *(gain#848)*. Fetching
null-histogram columns during the statistics scan, so segment equality could
compare the whole row as this record first worded it. Rejected: it would
fetch columns nothing else in the statistics build uses, and it could
silently change segment counts wherever a null-histogram column varies
inside an existing run.

**Per-score-column NA-aware coverage.** Counting a position as covered per
column, only where that column has a non-NA value. Rejected for coverage:
it conflates "the table has no row here" with "the row has no value for this
column here", which are different claims (the same distinction this project
has been burned by before — see the search-index entries in `CONTEXT.md`).
Coverage documents the first; per-column NA-ness is visible in the value
statistics. This also keeps coverage per-resource — one table, not one per
column.

**Hash-versioned rollout.** Folding the new statistics into
`calc_statistics_hash` so every resource rebuilds on upgrade. Rejected: it
invalidates the statistics of every deployed resource in every GRR at once,
turning a documentation improvement into a fleet-wide rebuild obligation. The
lazy path costs a mixed state — some resource pages showing "not computed"
indefinitely — which is accepted and made visible rather than hidden.

## Consequences

- **Two numbers that used to be one.** Covered positions and Σ segment
  lengths are separate statistics that agree only absent different-valued
  overlap. Rendering must never derive one from the other.
- **The chunk merge carries values.** A region-chunked scan stitches segments
  across chunk boundaries, so each chunk's open head/tail segments must carry
  their value tuples — equality is now part of the stitch. The merge is
  order-sensitive, unlike the min/max and histogram merges.
- **Exact equality is sensitive by design.** A score whose stored values
  differ in the last bit segments finely; that is a fact about the data, and
  the statistics report it rather than smooth it.
- **NA runs are segments.** A consumer reading segment counts must not read
  them as "runs of usable values".
- **Fragment segments are unpublishable until fragments have an exact run
  algebra** *(amended 2026-08-24, gain#848)*. For overlapping fragment rows,
  run identity is approximate twice over — bulk-vs-per-record and
  chunked-vs-unchunked — while covered counts stay exact. Value-aware
  segments are a position-score statistic; nothing may publish a fragment
  segment count or length histogram before giving fragments an exact run
  algebra.
- **The rollout is visibly incomplete for a while.** Resource pages show
  "not computed" until each resource is rebuilt; that state is intended, not
  a defect.
- The definitions are vocabulary now: `CONTEXT.md` carries **segment**,
  **covered position** and the five **allele classes**, and issue text and
  docstrings in the statistics area are held to them.
