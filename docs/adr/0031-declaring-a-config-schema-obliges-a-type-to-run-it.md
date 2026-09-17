# 31. Declaring a config schema obliges a type to run it

- **Status:** accepted
- **Date:** 2026-09-17
- **Issues:** [gain#1075](https://github.com/iossifovlab/gain/issues/1075)
  (this record); [gain#1067](https://github.com/iossifovlab/gain/issues/1067)
  (the genome, decided one type at a time);
  [gain#654](https://github.com/iossifovlab/gain/issues/654) and
  [gain#1053](https://github.com/iossifovlab/gain/issues/1053) (the chain's
  tolerance of a malformed `labels`, which this record reverses)
- **Related:** [ADR 0008](0008-scan-owns-validation.md) — decision 6 names
  the exception a refusal raises, and its rejection of `ABCMeta` on the
  score hierarchy is why the obligation below is a rule, not a mechanism

## Context

`ResourceConfigValidationMixin` gives a resource type two things: an abstract
`get_schema()` and a `validate_and_normalize_schema()` classmethod that runs
the schema through cerberus and hands back the normalized document. Nothing
connects the two. A type inherits the mixin, satisfies the abstract method,
and is free never to call the validator — and two types did exactly that.

`LiftoverChain` declared a complete schema (base fields, `filename`, and a
nested `chrom_prefix` of `variant_coordinates` / `target_coordinates`, each
with `del_prefix` / `add_prefix`) and read its config raw. A comment in the
constructor said so. A key the schema did not know, or a `chrom_prefix`
spelled the way a *genome* spells it (a flat string), passed construction
and failed later, at the first read that tripped on it. The published docs
had drifted the other way: `grr.rst` documented `filename` alone and said
the section had "only filename", while the runtime and the schema both read
`chrom_prefix`, and one published chain in `grr_sfari` used it.

`GeneSetCollectionImpl` inherited the mixin with a `get_schema()` that raised
`NotImplementedError`. It had never declared a schema. The collection it
builds validates its config through pydantic's `GeneSetResourceSchema`, so
the type was already validating on construction — in a vocabulary the mixin
knows nothing about — and the mixin was a dead base class making a promise
the type had no intention of keeping.

gain#1067 had closed the same gap for `ReferenceGenome`, where the drift had
reached every published genome (each carried a `chrom_prefix` the schema
omitted). That record's review asked the question this one answers: does
declaring `get_schema()` oblige a type to run it, or is validation
opt-in-per-type forever?

Two things had been decided per type that this record decides for all:

- The mixin refused with a bare `ValueError("Invalid configuration: …")`,
  while ADR 0008 decision 6 says a config refusal is a
  `MalformedResourceError`. Four types were refusing under the wrong name.
- gain#654 had made the chain *tolerate* a `meta.labels` that is not a
  mapping — both genome ids left unset — because the alternative at the time
  was a bare `AttributeError` naming nothing. Every type that runs the base
  schema refuses the same config at construction, since the base schema
  holds `labels` to a mapping.

## Decision

**1. A type that inherits `ResourceConfigValidationMixin` runs
`validate_and_normalize_schema` in its constructor**, and reads its config
from the document that comes back. The schema is not documentation; it is
what the type accepts. `LiftoverChain` does so now, alongside `GeneModels`,
`GenomicScore`, `GeneScore` and `ReferenceGenome`.

**2. A type that validates through another vocabulary does not inherit the
mixin.** `GeneSetCollectionImpl` validates through pydantic and no longer
inherits it; the reason is recorded at the class. There is no stub
`get_schema()` anywhere — a type either declares a schema and runs it, or
declares none.

**3. Where a family shares a base, the base runs the schema.**
`ScoreResource.__init__` keeps the resource, validates, and keeps the
document; `GenomicScore` and `GeneScore` call it rather than each repeating
the three lines.

**4. The refusal is `MalformedResourceError`**, with the message prefix
`Invalid configuration: <resource id>` that `resource_errors` already builds
for a score's definition, so a reader sees one wording for both. It
subclasses `ValueError`, so no caller and no test catching the old type
changed.

**5. A `meta.labels` that is not a mapping refuses the resource, for every
validating type — the chain included.** gain#654's goal was a failure that
names the resource instead of a crash that names nothing; a refusal by id
meets it. `labels: null`, and no `labels` at all, still build.

**6. Switching validation on for a type is preceded by a content sweep.**
Every published resource of that type in `grr` and `grr_sfari` is validated
against the schema before the call lands, and the docs are brought level
with the schema in the same change. For the chain: seven published
resources, all passing the schema unchanged; `chrom_prefix` documented.

### Why it is a rule and not a mechanism

Three ways of making the obligation structural were considered during the
gain#1067 review and are declined here.

**Validating inside `GenomicResource.get_config()`** would switch validation
on for every unvalidated type at once, with no content sweep behind any of
them — gain#1067 needed a nine-resource sweep to enable *one* type. It would
also need the repository to reach the type→implementation registry to find
the schema, which is an import cycle.

**Giving the mixin an `__init__`** does not help: a subclass must still call
`super().__init__()`, so forgetting stays silent — the same failure, moved.
It would also cost the mixin its statelessness, which is what lets it sit
beside `GenomicResourceImplementation` and `InfoImplementationMixin` in a
class's base list without an MRO argument about whose `__init__` runs.

**`ABCMeta` on the hierarchy** was examined and rejected in ADR 0008 for the
score classes: nothing in that MRO is an `ABC`, and introducing one would
retroactively enforce this very `get_schema` across every score kind and
every third-party subclass.

The per-type call is therefore the recorded cost of that trade. What guards
it is this record and the pattern being uniform: every mixin user has the
call in its constructor, and a type that does not want it does not inherit
the mixin. A reviewer who sees the mixin in a base list looks for the call.

## Consequences

- A chain with a key its schema does not know, a genome-style
  `chrom_prefix`, or a non-mapping `labels` is refused at construction,
  naming the resource. No published chain is affected.
- The gain#654 test that pinned the chain's tolerance now pins the refusal.
  A future type switching validation on should expect the same shape of
  change: some tolerance a reader added for one type becomes a refusal, and
  the test has to say which one was decided.
- The published docs for a type are held to the schema. A key the runtime
  reads and the schema accepts is documented; a key documented is in the
  schema. `chrom_prefix` on the chain was the case in hand.
- Code catching `ValueError` around construction keeps working, and code
  that wants to tell "this resource's own config is bad" from other
  `ValueError`s can now catch `MalformedResourceError` and get every
  validating type at once.
- A type that adds the mixin later inherits the whole obligation: schema
  complete, validator called, content swept, docs level. This record is
  where to point the review.
