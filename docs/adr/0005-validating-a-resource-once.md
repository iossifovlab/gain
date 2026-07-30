# 5. A resource is validated when its statistics are built, not on every read

- **Status:** accepted
- **Date:** 2026-07-30
- **Issues:** design discussion following [gain#421](https://github.com/iossifovlab/gain/issues/421); interacts with [gain#509](https://github.com/iossifovlab/gain/issues/509)

## Context

`fetch_region_values` did two jobs. It produced a region's values, and it
checked that the records' order was one the resource kind can mean — a
position score promising one value per position, an allele or fragment score
allowed several at one position but never one that moves backwards. Both jobs
ran on every read, per record.

That coupling cost three things.

**It made the rule impossible to state once.** The same invariant was written
out five times, in five shapes, with three different messages: the
`PositionScore` guard, the `AlleleScore` guard, the duplicate-`(ref, alt)`
debug log, the vectorized `_clip_keep_guard`, and `fetch_position_scores`'
point-query check. `RECORD_ORDERING` — added by #421 — *described* the policy
without implementing it anywhere except the vectorized guard.

**It made every reader pay.** The statistics scan is per-record-Python-bound,
and annotation reads the same path through `aggregate_region`. The check is a
per-record branch and a per-record comparison on both.

**And it could not be turned off even where it was demonstrably redundant.**
An annotation run reads resources that a statistics build has already scanned
end to end.

## Decision

Reading and checking are separated, and the check becomes a property of the
**open** rather than of the read:

```python
score.open(validate_ordering=True)   # the default
```

Each kind implements a pure `_region_values` producer. The base's concrete
`fetch_region_values` wraps it with `_enforce_ordering` when asked, applying
the rule from `RECORD_ORDERING` — so the rule has one implementation, and it
reads only the span every kind already yields. `fetch_position_scores`, the
point form of the same invariant, follows the same flag; with the check off
the first record wins, which is the record the region read would have yielded
first.

**The default checks.** A caller who has not thought about it gets the safe
answer, and out-of-tree callers are unaffected. Only two call sites opt out —
`ScoreAnnotator` and `FragmentScoreAnnotator`.

**The vectorized guard stays unconditional.** `_clip_keep_guard` is reached
only from a statistics build, which is the caller that must check, so nothing
would ever pass `False` there. Making it conditional would add a way to get it
wrong for no benefit. A test pins the asymmetry so it does not get tidied into
consistency later.

## Why trusting the statistics is sound

The evidence an annotator relies on is not "someone ran `resource-repair`". It
is the machinery's own refusal to record success it did not have:

- a region task that raises does not merely fail — `TaskGraph.process_completed_tasks`
  **prunes its dependants**, so the merge → save → `_store_stats_hash` chain
  never runs and no fresh `stats_hash` is written;
- `keep_going=True` means one bad resource does not block the repository, so
  the rest still build and the offender is reported by name;
- `_statistics_not_built` re-checks stored against computed hash after the run,
  catching a task that failed silently.

So: **a current `stats_hash` means the resource was scanned end to end without
an ordering violation.** That is a stronger claim than "the statistics are
current", and it is the claim annotation trusts.

It was not quite true when this was decided. `_get_chrom_regions` skipped any
contig whose length it could not determine, so those records reached neither
the statistics nor the checks — while the resource still recorded a fresh
hash. Closing that was a precondition of this decision, not a follow-up: a
contig whose length is merely *undetermined* is now scanned as one unbounded
region, while a contig *proven* to hold no records is still skipped, there
being nothing to scan or validate.

## Consequences

- **A resource that never went through `grr_manage resource-repair` is
  annotated from unvalidated data instead of raising.** This is the cost, and
  it is deliberate. It is not a silent wrong answer for a *deployed* resource,
  because a deployed resource has statistics; it is a silent wrong answer for
  a hand-built or mid-build GRR that someone annotates with directly. Anything
  other than an annotator still checks by default.
- Nothing verifies the hash *at annotation time*. Having the pipeline verify it
  was considered and rejected: `implementations/annotation_pipeline_impl.py`
  imports `gain.annotation`, so an annotation-layer check would close an
  import cycle, and verifying currency would not close the coverage gap
  anyway — the two are different claims.
- The score layer never learns what a `stats_hash` is. Whoever decides the
  flag is whoever knows the resource has been repaired, which keeps
  `genomic_scores.py` free of any import from `implementations/`.
- `SHARED`'s backwards-record rule is unreachable through any resource: tabix
  refuses to index a file whose positions decrease, the in-memory backend
  sorts on load, bigWig intervals are ordered by construction, and a VCF table
  is tabix-backed. It is kept and pinned directly rather than deleted, because
  it is the per-record half of a rule the vectorized scan also states.
- **gain#509 must preserve the empty/undetermined distinction.** Routing contig
  length through the tables' own `get_chromosome_length` replaces `None`
  returns with raises; a single `except ValueError: skip` would re-introduce
  the silently unscanned contig this decision depends on not existing.
