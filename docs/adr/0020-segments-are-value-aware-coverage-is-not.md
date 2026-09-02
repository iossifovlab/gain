# 20. Extended score statistics: segments are value-aware, coverage is not

- **Status:** accepted
- **Date:** 2026-08-24
- **Issues:** [gain#770](https://github.com/iossifovlab/gain/issues/770) (the
  epic and its decision record), [gain#771](https://github.com/iossifovlab/gain/issues/771)
  (this record), [gain#773](https://github.com/iossifovlab/gain/issues/773)
  (the allele-classification amendment),
  [gain#848](https://github.com/iossifovlab/gain/issues/848)
  (the scanned-tuple amendment),
  [gain#926](https://github.com/iossifovlab/gain/issues/926)
  (the fragment-segments amendment),
  [gain#1041](https://github.com/iossifovlab/gain/issues/1041)
  (the coverage-denominator amendment)

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

*Amended by gain#1118: an **allele score** has no covered-position count at
all.* Its rows collapse to points, so the span union above never applied to the
kind — it is deliberately excluded from the coverage scan — and what stood in
its place was a DISTINCT-position count kept inside the allele statistic. That
count is removed. An allele score's statistic answers only what its rows *are*,
and its info page renders no Coverage section rather than one permanently
reading "not computed". Position and fragment scores are unaffected: they are
span-union scanned, and a position score whose statistics are not yet built
still says "not computed", where the message is true and a rebuild acts on it.

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
  (segments, fragments, ~~indels~~) use one fixed binning everywhere, so
  per-chromosome results merge into exact global ones at build time — no
  second pass, no approximate merge, and chunked scans merge exactly for the
  same reason. *(The indel groups left the stored ladder in gain#1118; see
  the amendment below. Segments and fragments still store it.)*
- **Raw counts stored; fractions at render.** The statistics file holds
  counts only. Coverage *fractions* need chromosome lengths, which belong to
  a reference genome, not to the score — so they are computed at render time
  from a resolvable genome (bigWig header as fallback). The stored statistics
  stay genome-independent, and rendering can improve without rebuilding any
  resource.

  *Amended by [gain#1041](https://github.com/iossifovlab/gain/issues/1041):
  the fraction's **denominator is the whole resolved reference**, not the
  contigs the score happens to touch.* As first implemented, the denominator
  was restricted to the chromosomes present in the stored counts, so a score
  touching only chr1 reported a global percent as if the rest of the genome
  did not exist — a number that answered "what part of what I already cover
  do I cover", which is not a question anyone has. The denominator is now the
  sum of **all** the genome's contig lengths; on the bigWig rung, the whole
  contig list that backend serves cleanly off an open table
  (`get_chromosomes()`, already in reference space).

  **The two rungs answer the same question about different universes**, and
  that is worth stating rather than discovering. A bigWig header is the
  *file's* universe, not a reference: a chr21-only bigWig reads as nearly
  fully covered unlabelled, and as a percent or two once labelled hg38. Only
  the genome rung answers this bullet's title question; the table rung
  answers "what part of what this file declares". Under a `chrom_mapping`
  **file** it is narrower still — `get_chromosomes()` is then the mapping's
  contigs — so that rung's denominator is the resource's *declared* universe.
  The label is what upgrades the number, which is why the resource-authoring
  documentation now says so plainly.

  Three consequences are decisions rather than fallout:

  - **The untouched remainder is one roll-up row** — "N contigs with no
    values (X bp)", carrying a 0.00% in the percent column — not a row each
    and not silence. A reference carries
    hundreds of contigs a score never touches, and per-contig zero rows would
    bury the contigs that do have values. Membership is **zero covered
    positions**, not absence from the stored statistic: a bigWig scan visits
    every header contig and stores a `0` for the empty ones while a tabix
    scan visits only the contigs its index lists, so rolling up by absence
    would render the same data two ways.
  - **The degradation guards are unchanged and now also gate the roll-up.** A
    covered contig the resolved reference does not list is proof the label is
    wrong, and an implausible length (a zero-length `.fai` record, a contig
    shorter than the positions the score holds on it) is proof for that
    contig. Either degrades to raw counts as before — that contig's row, and
    with it the global fraction; sibling rows keep their percentages — and
    the roll-up is withheld with the global fraction, because "these contigs
    have no values" is a claim about the reference being the right one. An
    implausible length on an *untouched* contig degrades nothing visible: it
    simply leaves the universe, contributing neither denominator nor roll-up.
  - **Still render-time only.** No stored statistic changes and no resource
    rebuilds: this bullet's own rule is what makes the correction free.
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

*Amended by gain#779:* the **Fixed log-scale bins** bullet above names the
length histograms that share the ladder — segments, fragments, **indels** — and
the ins/del histograms duly use it, binning the length change absolutely, since
the class already names the direction and the shared bin index refuses a length
below 1.

*Amended by gain#1118: the indel groups leave the ladder as a STORED format,
and keep it only as a rendering choice.* Each group now stores an **exact
`{length: count}` map**, clamped at a code-level `INDEL_LENGTH_CLAMP` of 8192,
alongside four exactly-mergeable scalars — `count`, `sum`, `min` and `max`, all
accumulated on the *unclamped* length.

The ladder was wrong here for the reason it is wrong for the complex grid, and
the two amendments are the same finding reached twice: its second bin is
{2, 3} and its third {4, 5, 6, 7}, which is precisely where indels live. **No
exact minimum, maximum, mean or median survives it** — and those four are what
the info page's indel statistics table exists to show. The scalars are what
keep the clamp from becoming a lie: min, max and the mean stay exact however
far the tail runs, and only a median landing in the overflow bucket degrades,
which the page renders as a floor rather than as a number.

The ladder remains the **stored** format for segments and fragments, and
remains what the indel *chart* is drawn on — derived from the map at render
time rather than stored beside it, so the picture and the statistics beneath it
cannot drift. The derived bins are identical to the stored ones, because the
plot already sums every bin at or above its display cap into one overflow bar
and the clamp is equal to that cap.

The rollout is the one this ADR already describes: **deserialization reads the
map only**, so an allele score built before this renders "not computed" for its
indel groups until it is rebuilt with `--force`. One reader rather than a
compatibility branch, deliberately — a branch reading the old histograms could
publish no exact sum, min or max at all, so every figure in the table would be
a guess at bin resolution presented as a number.

The **complex `(len_ref, len_alt)` grid deliberately does not share the
ladder either.** Its cells are
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
change once resources carry statistics built from it. The same now holds for
`INDEL_LENGTH_CLAMP` (gain#1118), with one added constraint of its own: it must
never fall **below** the histogram's display cap, because the indel chart's
bins are derived from the map and a bin between the two would be drawn from
lengths the map had already folded away. The two are equal today, which is the
tightest that rule allows — so the display cap, documented as a rendering
choice free to change, is no longer free to be *raised* without a
stored-format change here first.

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

**Fragment segments — not wanted** *(amended 2026-08-31, gain#926)*. Giving
overlapping fragment rows a segmentation of their own, so a fragment score
could publish a segment count and length histogram beside its coverage. The
2026-08-24 amendment below left this open — unpublishable *until* fragments
have an exact run algebra — and the epic's decision record named the remedy as
"a separate child of this epic". That child is gain#926, and its answer is
**no**: the question is closed, not deferred. Three reasons, none of which
turn on the mergeability problem:

- The **value-blind** definition arrives pre-rejected, by the entry above and
  more strongly for this kind. Both carriers that argument names already exist
  for a fragment score: it is in the coverage scan, so its covered-position
  union is published, and gain#794 shipped the fragment count and
  fragment-length histogram — exactly the unmerged "fragment view". A
  value-blind fragment segment would restate two statistics fragments already
  publish.
- No consumer question survives for a **value-aware** run. For the motivating
  ATAC-fragment resources the score columns are a cell barcode and a count, so
  a run of equal values across overlapping fragments is noise rather than
  signal; naming a question that fragment coverage and fragment
  counts/lengths cannot already answer between them is the burden, and nothing
  meets it.
- The info page's needs are met without it — fragment coverage, the fragment
  count and length histogram (gain#794), and per-value custom plots via
  `plot_function`, which a resource can already configure (grr_bench's
  `atac_fragments/T23_b17_Thymus_PCW17` draws fragments-per-cell that way).

So the exact-run-algebra question is **not open work**. Because nothing will
consume it, the run bookkeeping is not merely left unpublished: it is not
executed. `RegionCoverage.add_interval` opens no run for a kind whose rows
overlap, `add_interval_batch` takes a value-blind union collapse that reads no
value column, and the per-record scan feed hands it bare spans — the work is
gated off at the largest tables in the stack rather than computed and
discarded. Reopening this means reopening the *consumer* question first; a
mergeable definition on its own is not a reason to build one.

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
- **Fragment segments are not wanted** *(amended 2026-08-24, gain#848; the
  question closed 2026-08-31, gain#926)*. For overlapping fragment rows, run
  identity is approximate twice over — bulk-vs-per-record and
  chunked-vs-unchunked — while covered counts stay exact. gain#848 read that
  as "unpublishable *until* fragments have an exact run algebra"; gain#926
  settles that there is nothing to publish either way, for the consumer
  reasons recorded under *Rejected alternatives*. Value-aware segments are a
  position-score statistic. Nothing publishes a fragment segment count or
  length histogram, and a kind whose rows overlap now builds **no runs at
  all** — the algebra is gated off, not computed and discarded.
- **The rollout is visibly incomplete for a while.** Resource pages show
  "not computed" until each resource is rebuilt; that state is intended, not
  a defect.
- The definitions are vocabulary now: `CONTEXT.md` carries **segment**,
  **covered position** and the five **allele classes**, and issue text and
  docstrings in the statistics area are held to them.
