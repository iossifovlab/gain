# 28. Wildcard expandability is parser policy, pinned against the annotators

- **Status:** accepted
- **Date:** 2026-09-10
- **Issues:** [gain#1334](https://github.com/iossifovlab/gain/issues/1334),
  following [gain#1329](https://github.com/iossifovlab/gain/issues/1329)
- **Relates to:** [ADR 0011](0011-deprecate-cnv-collection-vocabulary.md), whose
  legacy fragment-score spellings are two of the map's nine keys

## Context

A pipeline may write a wildcard in place of an annotator's `resource_id`:

```yaml
- position_score:
    resource_id: "hg38/scores/phastCons*"
```

`AnnotationConfigParser.query_resources` answers it. Since
[gain#1266](https://github.com/iossifovlab/gain/issues/1266) it does so through
a map from the annotator name a user may type to the **one** canonical resource
type that name's wildcard selects — nine entries, covering the four score
annotators under both their spellings, plus the deprecated `cnv_collection`
pair.

gain#1329 then gave every annotator that consumes a typed genomic resource a
class-level `ACCEPTED_RESOURCE_TYPES`, so that a wrong-type refusal reads the
same whichever annotator was configured. That left the same fact written twice:
`PositionScoreAnnotator.ACCEPTED_RESOURCE_TYPES` says `("position_score",)`,
and the parser's map says `"position_score_annotator": "position_score"`.

gain#1329 deliberately did not pin the two against each other, because pinning
would have prejudged the question gain#1334 asks: should the map be *derived*
from the annotators instead of listed?

## Decision

**1. Wildcard expandability is the parser's policy, not a property of the
annotator.** The map stays a list `AnnotationConfigParser` owns. It is not
derived from the entry points, from the annotator classes, or from anything the
annotators declare.

The map's own docstring already called it "the annotation layer's policy", and
the annotators bear it out: `gene_set_annotator` declares
`ACCEPTED_RESOURCE_TYPES` and is deliberately **not** wildcard-expandable,
because the two spellings it accepts are not an equivalence group (see
`GENE_SET_TYPES`) — so a wildcard keyed on either would answer only half of a
repository's gene sets. A derived map cannot express that without a second
declaration saying "…but not for wildcards", at which point the policy is being
declared anyway, just further from the parser that applies it.

**2. The cost objection gain#1334 raised is wrong, and is not the reason.**
gain#1334 argued that deriving would mean "importing every annotator module to
answer one query — on a path a web endpoint reaches per request." It would not.
`annotation_factory._load_annotator_factory_plugins` already calls
`entry.load()` on **every** registered entry point the first time any annotator
is built, and memoizes behind `_EXTENTIONS_LOADED`. Deriving would therefore
cost one import sweep per process, not one per request, and in any process that
has ever built a pipeline it would cost nothing at all.

This is recorded so a later reader does not re-derive a premise that does not
hold and reopen the question on it. Derivation is rejected on decision 1, and
on the plugin-API blast radius in decision 2b below — not on import cost.

**2b. Deriving would make wildcard expandability a plugin-API promise.**
Third-party annotators register through the same `gain.annotation.annotators`
group. If the map were derived from what an annotator declares, any third-party
annotator declaring `ACCEPTED_RESOURCE_TYPES` would silently acquire wildcard
expansion — a behaviour GAIn would then owe it, without ever having decided to.
Adding a *separate* declaration for expandability instead makes it a required
part of the plugin API, with an external blast radius, to state a policy that
is GAIn's.

**3. The agreement is pinned by a test, with an explicit exemption set.**
`AnnotationConfigParser` carries two class-level constants:

- `WILDCARD_RESOURCE_TYPES` — the map, moved out of `query_resources`' body
  unchanged, so a test can read it.
- `WILDCARD_EXEMPT_ANNOTATORS` — the annotators that declare
  `ACCEPTED_RESOURCE_TYPES` and still take no wildcard, each with its reason
  written beside it.

`core/tests/small/annotation/test_wildcard_annotator_map.py` walks the entry
points in the `gain.annotation.annotators` group, keeps those whose target
module is under the `gain.` package, resolves each factory, reads
`ACCEPTED_RESOURCE_TYPES` off the annotator classes defined in that module, and
asserts that each declaring name is in the map with value equal to
`ACCEPTED_RESOURCE_TYPES[0]`, **or** in the exemption set, and never both. It
also asserts that every map key and every exemption member names a registered
GAIn annotator that declares a resource type, so a stale or meaningless entry
fails.

The `gain.`-package restriction is deliberate: an installed third-party plugin
that declares accepted resource types must not fail GAIn's own pin, because the
policy the pin enforces is GAIn's.

**4. `ACCEPTED_RESOURCE_TYPES` leads with the preferred spelling, by
contract.** The tuple order was already load-bearing for the refusal message it
is rendered into; this promotes it to a stated contract and gives the pin
something exact to compare against. An annotator that comes to accept a further
spelling **appends** it.

Equality against element zero, not membership: membership would pass a map
entry pointing at `cnv_collection`, and a wildcard keyed on the deprecated
spelling answers only the resources that have not migrated — which is precisely
the silent miss gain#1266 closed.

**5. `gene_set_annotator` is the first and only exemption.** Whether it should
be — whether `GENE_SET_TYPES` should become a search equivalence group and the
annotator gain a wildcard — is
[gain#1365](https://github.com/iossifovlab/gain/issues/1365), not this record.

## Why this is scoped this way

The residual duplication is nine lines, and it is not unguarded. Since
gain#1266 an annotator name absent from the map is refused outright, with a
message listing the names that do accept a wildcard, rather than expanding to
nothing. What was *not* guarded is narrower: an annotator whose declared
canonical type drifts from its map entry, and an annotator that comes to accept
a second resource type while its map entry names only one. Both are silent, and
both are what decision 3 closes.

A test is the proportionate instrument for that. It costs a file, catches the
drift at CI time rather than at pipeline-authoring time, and — unlike a
derivation — leaves the policy visible at the place that applies it.

## Alternatives considered and rejected

**Register classes instead of factories.** The entry-point table would map a
name to an `AnnotatorBase` subclass, and the parser would read the attribute
off it. Rejected: the factories are not thin. `build_fragment_score_annotator`
and its siblings resolve resources, read parameters and wrap the annotator in
decorators; several names share one class (`allele_score` and
`allele_score_annotator`, and the legacy fragment-score pair both build
`FragmentScoreAnnotator`), so a class-keyed table loses the name distinctions
the map is keyed on. It is also a breaking change to every third-party
registration.

**An attribute on the factory function.** `build_position_score_annotator.
wildcard_resource_type = "position_score"` needs no new entry-point group and
no class registration. Rejected as decision 1 rejects all derivation carriers —
it puts GAIn's policy in the plugin's hands — and additionally because a
function attribute is invisible to every reader who does not already know to
look for it, and to mypy.

**A second entry-point group** (`gain.annotation.wildcard_types`). Keeps the
declaration out of the annotator class and lets a plugin opt in explicitly,
which answers decision 2b's silent-acquisition objection. Rejected because it
is still a plugin-API surface — versioned, documented and owed compatibility —
carrying nine entries that GAIn writes for itself, and it splits one policy
across two files that can disagree with no pin possible between them.

**Keep the map and pin nothing** — gain#1329's state. Rejected: it is the
status quo the issue was filed against, and the two silent drifts named above
stay silent.

**Pin by membership rather than by element zero.** Simpler, and tolerant of
reordering. Rejected for the reason decision 4 gives: it would accept a map
entry naming a deprecated spelling.

## Consequences

- `query_resources` reads `AnnotationConfigParser.WILDCARD_RESOURCE_TYPES`
  instead of building a local dict. Its refusal message, its semantics and its
  handling of the legacy and retired names are unchanged; no user-visible
  behaviour changes.
- A new score annotator must be added to `WILDCARD_RESOURCE_TYPES` or to
  `WILDCARD_EXEMPT_ANNOTATORS`. Forgetting fails
  `test_wildcard_annotator_map.py` at CI time, where before it failed for
  whoever first wrote a wildcard for it.
- An annotator that appends a second accepted spelling keeps working. One that
  *leads* with a new spelling changes what its wildcard selects, and the pin
  fails until the map is updated to match — which is the point.
- The plugin entry-point API is untouched. No new group, no new required
  declaration, no change to how factories are registered.
- Third-party annotators are outside the pin, and stay able to declare accepted
  resource types without acquiring a wildcard.
