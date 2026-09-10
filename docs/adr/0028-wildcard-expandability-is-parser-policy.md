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
type that name's wildcard selects — nine entries: the position, allele and
fragment score annotators under both their spellings, `gene_score_annotator`
(which has only the one), and the deprecated `cnv_collection` pair.

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

The argument that carries this on its own is the plugin API. Third-party
annotators register through the same `gain.annotation.annotators` group. If the
map were derived from what an annotator declares, any third-party annotator
declaring `ACCEPTED_RESOURCE_TYPES` would silently acquire wildcard expansion —
a behaviour GAIn would then owe it, without ever having decided to. Adding a
*separate* declaration for expandability instead makes it a required part of
the plugin API, with an external blast radius, to state a policy that is
GAIn's. That holds whatever the in-tree annotators happen to look like.

`query_resources`' own docstring already called this "the annotation layer's
policy about the result", and today one in-tree annotator sits on the
distinction: `gene_set_annotator` declares `ACCEPTED_RESOURCE_TYPES` and takes
no wildcard, because the two spellings it accepts are not related by
`equivalent_resource_types`, so a wildcard keyed on either would answer only
the gene sets declaring that one. That instance is *supporting* evidence, not
the argument — `GENE_SET_TYPES` records that its absence from the equivalences
"is the behaviour as it stands, not a considered position", and gain#1365 may
remove it. If it does, decision 1 stands unchanged on the paragraph above.

**2. The cost objection gain#1334 raised is wrong, and is not the reason.**
gain#1334 argued that deriving would mean "importing every annotator module to
answer one query — on a path a web endpoint reaches per request." It would not.
`annotation_factory._load_annotator_factory_plugins` already calls
`entry.load()` on **every** registered entry point the first time any annotator
is built, and memoizes behind `_EXTENTIONS_LOADED`. Deriving would therefore
cost one import sweep per process, not one per request, and in any process that
has ever built a pipeline it would cost nothing at all.

This is recorded so a later reader does not re-derive a premise that does not
hold and reopen the question on it. Derivation is rejected on decision 1's
plugin-API blast radius — not on import cost.

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

Equality against element zero, not membership. For the fragment score's pair
the two rules agree today and the difference is hygiene: `search_resources`
expands whichever spelling it is given through `equivalent_resource_types`, so
a map entry naming `cnv_collection` would select the same resources as one
naming `fragment_score`.

They do not agree in general, and that is the reason. `equivalent_resource_types`
relates a **narrower** set of spellings than the annotators accept — its own
docstring says so, and `GENE_SET_TYPES` is exactly such a pair, unrelated by
search. For a pair like that, a map entry naming the non-preferred spelling
would answer only the resources declaring it, and membership would pass that
entry. Element zero also fails loudly rather than silently if the fragment
score's equivalence is ever narrowed.

**5. `gene_set_annotator` is the first and only exemption.** Whether it should
be — whether `GENE_SET_TYPES` should become a search equivalence group and the
annotator gain a wildcard — is
[gain#1365](https://github.com/iossifovlab/gain/issues/1365), not this record.

## Why this is scoped this way

The residual duplication is nine lines, and it is not unguarded. Since
gain#1266 an annotator name absent from the map is refused outright, with a
message listing the names that do accept a wildcard, rather than expanding to
nothing. What was *not* guarded is narrower: an annotator whose declared
canonical type drifts from its map entry, silently. That is what decision 3
closes.

gain#1334 named a second silent case — an annotator that comes to accept a
second resource type while its map entry names only one — and decision 3 does
**not** close it, by design. The map names one canonical type on purpose, and
`search_resources` expands it through `equivalent_resource_types`, so a second
spelling of the *same* kind needs no map change. A second, genuinely different
kind would need one, and the pin would not ask for it: it compares against
element zero, which such an annotator would not have changed. No annotator is
in that position, and putting one there is a large enough change to be noticed
without a pin.

A test is the proportionate instrument for that. It costs a file, catches the
drift at CI time rather than at pipeline-authoring time, and — unlike a
derivation — leaves the policy visible at the place that applies it.

## Alternatives considered and rejected

**Register classes instead of factories.** The entry-point table would map a
name to an `AnnotatorBase` subclass, and the parser would read the attribute
off it. It would work — the table stays keyed by name, so the two spellings per
annotator keep their own entries, and the pin test in this change does very
nearly this to build its expectation. Rejected on decision 1: the attribute it
would read is the annotator's, and reading policy off it is derivation by
another route. Rejected additionally on cost: it is a breaking change to every
third-party registration, and two of the five factories
(`build_gene_score_annotator`, `build_gene_set_annotator`) do real work —
resolving a resource and reading parameters before construction — which a class
in the table cannot carry.

**An attribute on the factory function.** `build_position_score_annotator.
wildcard_resource_type = "position_score"` needs no new entry-point group and
no class registration. Rejected as decision 1 rejects all derivation carriers —
it puts GAIn's policy in the plugin's hands — and additionally because a
function attribute is invisible to every reader who does not already know to
look for it, and to mypy.

**A second entry-point group** (`gain.annotation.wildcard_types`). Keeps the
declaration out of the annotator class and lets a plugin opt in explicitly,
which answers decision 1's silent-acquisition objection. Rejected because it
is still a plugin-API surface — versioned, documented and owed compatibility —
carrying nine entries that GAIn writes for itself, and it splits one policy
across two files that can disagree with no pin possible between them.

**Keep the map and pin nothing** — gain#1329's state. Rejected: it is the
status quo the issue was filed against, and the silent drift named above
stays silent.

**Pin by membership rather than by element zero.** Simpler, and tolerant of
reordering. Rejected for the reason decision 4 gives: it would accept a map
entry naming a deprecated spelling.

## Consequences

- `query_resources` reads `AnnotationConfigParser.WILDCARD_RESOURCE_TYPES`
  instead of building a local dict. Its refusal message, its semantics and its
  handling of the legacy and retired names are unchanged; no user-visible
  behaviour changes. The constant is a `MappingProxyType`: the local dict was
  rebuilt per call, so an in-place edit could not outlive one, and a plain dict
  class attribute would have made that edit process-wide.
- A new score annotator must be added to `WILDCARD_RESOURCE_TYPES` or to
  `WILDCARD_EXEMPT_ANNOTATORS`. Forgetting fails
  `test_wildcard_annotator_map.py` at CI time, where before it failed for
  whoever first wrote a wildcard for it. Choosing the exemption is a second
  edit, not a cheaper one: the exemption set's exact content is pinned too, so
  a new exemption has to be written down deliberately and given its reason.
- The pin is only as wide as its walk, which reads the annotator class from
  the module its entry point names. An annotator whose class lived in a shared
  base module and was merely imported by its factory's module would be invisible
  to it. That is why a second test asserts the floor: every declaring annotator
  class GAIn has loaded is one the walk attributed to a registered name.
- An annotator that appends a second accepted spelling keeps working. One that
  *leads* with a new spelling changes what its wildcard selects, and the pin
  fails until the map is updated to match — which is the point.
- The plugin entry-point API is untouched. No new group, no new required
  declaration, no change to how factories are registered.
- Third-party annotators are outside the pin, and stay able to declare accepted
  resource types without acquiring a wildcard.
- A **third** statement of the annotator-to-resource-type pairing still stands
  unguarded, one package up: `web_api/web_annotation/editor/views.py`'s
  `resource_default_annotators_mapping` holds the inverse relation, and the
  editor's per-annotator templates state it again per configuration field. This
  record does not close that — the pin here is core's, and what the editor
  offers by default is a different question — but "the residual duplication is
  nine lines" above is about `annotation_config`'s copy alone.
