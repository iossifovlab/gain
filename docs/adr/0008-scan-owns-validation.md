# 8. The statistics scan owns validation; reads never validate

- **Status:** accepted
- **Date:** 2026-08-03
- **Issues:** [gain#585](https://github.com/iossifovlab/gain/issues/585) (the epic this record belongs to), [gain#586](https://github.com/iossifovlab/gain/issues/586) (this record), [gain#553](https://github.com/iossifovlab/gain/issues/553) (the `open()`-time half), [gain#587](https://github.com/iossifovlab/gain/issues/587) / [gain#588](https://github.com/iossifovlab/gain/issues/588) / [gain#589](https://github.com/iossifovlab/gain/issues/589) / [gain#590](https://github.com/iossifovlab/gain/issues/590) / [gain#591](https://github.com/iossifovlab/gain/issues/591) / [gain#592](https://github.com/iossifovlab/gain/issues/592) (the implementation)
- **Supersedes:** the approach in [gain#521](https://github.com/iossifovlab/gain/pull/521), which is not being landed

> **Implementation status.** Carried out. This record was deliberately written
> first, so that the changes carrying it out could cite one decision instead of
> re-arguing it in seven review threads. gain#592 landed the last of them, with
> gain#553 — the `open()`-time config refusal of decision 5 — landing
> separately.

## Context

A genomic score's records have to hold to a rule its kind can mean. A position
score promises one value per position, so two records that touch are a data
error. An allele score and a fragment score may legitimately put several records
at one position, so only a record that moves *backwards* is an error.

That rule is enforced today on **every read**, in several places and several
shapes: a guard inside `PositionScore`'s region read, another inside
`AlleleScore`'s, a duplicate-`(ref, alt)` debug log, a point check in
`fetch_position_scores`, and the vectorized `_clip_keep_guard` on the bulk
statistics path. The `RECORD_ORDERING` class attribute was introduced to make
these one rule rather than several.

Three things are wrong with that arrangement.

**Every reader pays for a check only one reader needs.** The statistics scan is
per-record-Python-bound, and annotation reads the same path. Annotation re-derives,
on every variant, a property of the resource that does not change between reads.

**The one-rule claim is not true.** The comment above `RECORD_ORDERING` says it
exists so the rule is *"stated ONCE here and consumed by BOTH statistics scan
paths — the per-record one and the vectorized bulk one. Two statements of one
rule is how the paths drift."* The code falsifies that on both halves.

The attribute has exactly **one** reader: `_clip_keep_guard`, gated on `if
score.RECORD_ORDERING is RecordOrdering.DISJOINT`. The per-record path does not
consult it at all — `PositionScore` hardcodes its own overlap check (`left <=
prev_end`) and `AlleleScore` hardcodes its own backwards check (`pos <
prev_pos`). So the rule is not stated once and read twice: each per-record guard
states it independently, and the attribute claiming to unify them is read only by
the bulk guard.

And the one reader enforces **less** than the others. `_clip_keep_guard` does
nothing whatsoever for the shared case, so an allele or fragment score is checked
on the per-record path and not on the vectorized one. The drift the attribute was
meant to prevent is already there — the attribute simply made it look like
agreement.

This is not a corner. The bulk path covers position, allele and fragment scores on
tabix or bigWig tables — most production resources. For all of them, a completed
statistics scan certifies strictly less than the per-record path would, while
appearing to certify the same thing.

**And the rule cannot see what it needs to see.** Each kind normalizes a record
before yielding it: a position score clips to the queried region, an allele score
collapses a record to the point it sits at. That collapse *discards the record's
end*. So a record whose end precedes its begin — the signature of a tabix table
whose index and `pos_end` name different columns — is structurally invisible to
anything validating the normalized shape of an allele score. The two paths also
see different shapes, which is the mechanical reason the bulk guard could never
express more than the one rule it does.

## Decision

**1. Reads never validate.** `fetch_records`, `fetch_region_values`, the point
reads and the annotators do no checking at all. There is no `validate_ordering`
flag and no opt-out; the capability is removed rather than defaulted.

**2. The statistics scan always validates.** Unconditionally — on both the
min/max and the histogram pass, and on both the per-record and the vectorized
path. This is not a caller's choice. The scan reads through its own door, and the
door validates, so a pass added later is covered by construction rather than by
someone remembering.

**3. Validation reads raw records**, not the per-kind normalized span. The
methods are `validate_records` and `validate_record_arrays`; no rule reads a
score *value*, which is why neither is named for one.

**4. Each score kind states its own rule** in its own body. `RECORD_ORDERING` and
the `RecordOrdering` enum are removed.

Neither validator has a base-class default. A kind that inherited one would be
validated by a rule nobody chose for it, which is the failure this record exists
to undo — so `GenomicScore` declares both `@abstractmethod` over a
`NotImplementedError` body, and a new kind cannot be scanned without stating
what its records may look like.

The enforcement is at *call* time rather than at construction, which is worth
naming because it is the weaker of the two options. Making `GenomicScore` a real
`ABC` was rejected: nothing in its MRO
(`ScoreResource` → `ResourceConfigValidationMixin` → `Generic` → `object`) is an
`ABC` today, so introducing `ABCMeta` would retroactively enforce the abstract
`get_schema` that `ResourceConfigValidationMixin` already declares — a blast
radius across every score kind and every third-party subclass, for a stricter
failure time this epic has no need of. The chosen shape is also the one already
used in that MRO: `@abstractmethod` paired with a raising body, where the body
is what enforces.

**5. Rules are split by detectability.** A *config* error — a tabix table whose
index and `pos_end` disagree about which column ends a record — is refused when
the resource is **opened**, uniformly for every caller including annotation
(gain#553). The scan validators carry only rules that genuinely require reading
every record. Note the asymmetry this creates is deliberate: `open()` gains a
*refusal*, not a flag. The resource is turned away; the caller is not offered a
mode.

**6. A failure refuses the resource** with `MalformedResourceError`, which
subclasses `ValueError` so that it falls inside `RESOURCE_ERRORS` and is reported
as the resource's fault rather than as an unexpected internal error. It is named
for the resource's state rather than for a rule, because it covers both refusal
points.

### Why it is scoped this way

**Why the scan rather than a separate validation pass.** The statistics build
already reads the data up to twice — the min/max region tasks run to completion,
then the merge, then the histogram region tasks re-read the same regions sharing
nothing but a derived view range. A third independent sweep would re-derive facts
the second sweep had in hand and discarded. Validation is therefore free in I/O
terms: it costs a few integer comparisons per record on data already in memory.

**Why raw records rather than the normalized span.** Because of the allele
collapse described above: post-normalization there is no end left to check. Raw
is also the only layer at which the per-record and vectorized validators see the
same thing — the bulk path reads `pos_begin`/`pos_end` column arrays straight off
the backend, never normalized. Validating below normalization is what makes it
*possible* for the two to state one rule; validating above it is what made the
current divergence inevitable.

**Why per-kind bodies rather than one implementation reading an attribute.**
Because a shared attribute is what exists today and it did not work: two of the
three statements of the rule ignore it, and the one that reads it enforces the
least. It made the divergence look like agreement instead of preventing it.
Per-kind bodies at least put each kind's rule where a reader of that kind will
find it, and make a divergence between two kinds a visible difference between two
functions rather than a silent difference in what one attribute means to each of
its readers. There is a second tell: the
enum could not express every rule the validators needed, so rules were bolted on
outside it, and its own docstring had to carry a paragraph explaining why there
is no third value. An abstraction that must explain its own inexpressiveness is
the wrong abstraction. The cost accepted here is real — the allele and fragment
bodies read alike, and each kind's record and array validators must be kept in
agreement by hand. That is a visible duplication rather than a concealed one,
which is the trade being made.

**Why config errors move to `open()`.** A misconfigured resource should be
refused whatever anyone does with it. If the only detector rides the statistics
scan, such a resource is refused during a repair and then silently mis-annotated
for ever after, because annotation does not scan. One header read at open
refuses it uniformly and needs no scan at all.

## What a completed scan does and does not prove

Stated plainly, because the previous arrangement's guarantee was overstated and
that is the mistake most worth not repeating.

**It proves:** every record the scan read held to its kind's ordering rule, on
both read paths, for the regions that were scanned.

**It does not prove:**

- **Anything about a resource whose statistics were already current.** Statistics
  are rebuilt only when the manifest changed or the stored `stats_hash` no longer
  matches. Run a repair twice and the second run reads no records at all, so it
  re-validates nothing. This is intended — `stats_hash` keys on the data files'
  md5s, so the data cannot have changed without forcing a rebuild — but it means
  "was repaired at some point" is the actual claim, not "was validated just now".
- **Ordering across a region boundary.** The scan splits a contig into regions,
  each its own task, and a validator carries its predecessor within a region only.
  A violation straddling two regions is not seen. This is a pre-existing property
  of region splitting, not something introduced here, and it is not closed by this
  decision.
- **Anything at all about a resource that was never repaired.** Which is the cost
  below.

**On the vectorized path, the backwards-record rule is a rule no resource can
break.** Only tabix- and bigWig-backed tables are ever bulk-eligible; tabix
refuses to index a file whose positions decrease
(`[E::hts_idx_push] Unsorted positions on sequence`), and a bigWig's intervals
are sorted by its format. So `AlleleScore.validate_record_arrays` and
`FragmentScore.validate_record_arrays` cannot fire on any resource that reaches
them, and their tests reach them by substituting the backend. They are written
anyway, because the alternative is a kind that states no rule on one of its two
paths, and because "the backend happens to prevent it" is a property of today's
backends rather than of the kind. What #591 genuinely changed on that path is
the *position* rule: it moved from clipped spans to raw ones, and from the kept
records to all of them, which is a verdict that really did differ between the
two paths. Do not restate this as "the bulk scan used to certify backwards
allele records" — it could not read one.

## Consequences

**A resource that never went through `grr_manage resource-repair` is annotated
from unvalidated data.** Under the previous arrangement a read would have raised.
This is the price of the decision, and it should be stated rather than discovered:
it is not a silent wrong answer for a *deployed* resource, because a deployed
resource has been repaired — it is one for a hand-built or mid-build GRR that
someone annotates against directly. Config errors are the exception and are still
caught, for every caller, at `open()`.

**Annotation gets faster and simpler**, because the per-record ordering check and
the per-record debug log leave the hot path entirely.

**The scan and annotation no longer share a read entry point.** Two consumers with
genuinely different needs get two doors. The previous design tried to make one
path serve both, mediated by a flag, and spent a flag, a record and a trust
argument on the seam.

**Six validator bodies must be kept in agreement** — three kinds by two shapes.
Nothing enforces the pairing automatically; the record and array validators for
one kind agreeing is a review obligation. This is the known weak point of the
decision, and the divergence it risks is exactly the one being unwound here.

**A new score kind must state its own rule.** With no shared attribute to inherit,
adding a kind means deciding what its records may look like, rather than silently
acquiring another kind's answer.

## Alternatives considered and rejected

**A flag on the read path** — `open(validate_ordering=...)`, defaulting to on,
with the annotators opting out. This was the gain#521 design. Rejected: it makes
one read path serve a consumer that must verify and one that must not, and the
seam is not free — it costs a flag, a record justifying when the flag may be
turned off, and an argument that annotation is entitled to trust a resource's
statistics. Two consumers with different needs get two doors instead.

**Verifying the `stats_hash` at annotation time**, to establish that entitlement
rather than assume it. Rejected on two counts: `implementations/annotation_pipeline_impl.py`
already imports `gain.annotation`, so a check at the annotation layer closes an
import cycle; and hash currency and scan coverage are different claims — a
matching hash proves the statistics are up to date, not that every record was
read and checked.

**Validating the normalized `(begin, end, values)` span** rather than the raw
record. Rejected: the allele collapse leaves no end to check, and the per-record
and vectorized paths see different shapes, so they could never state more than
the weakest rule common to both.

**A `RECORD_ORDERING` class attribute with one shared implementation.** Rejected:
this is what the code does today, and its two readers enforce different subsets of
the rule. See *Why it is scoped this way*.

**A separate validation pass over the resource**, with its own recorded outcome.
Rejected: the statistics build already reads the data up to twice, and a third
sweep pays full I/O to re-derive what the scan is holding. The appeal was a
guarantee that could be stated directly rather than inferred from "the scan
finished" — that appeal is answered instead by the section above, which states the
guarantee and its limits explicitly.
