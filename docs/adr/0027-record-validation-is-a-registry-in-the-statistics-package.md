# 27. Record validation is a registry in the statistics package

- **Status:** accepted
- **Date:** 2026-09-09
- **Issues:** [gain#1269](https://github.com/iossifovlab/gain/issues/1269)
- **Amends:** [ADR 0008](0008-scan-owns-validation.md) — decision 4's *placement*, not
  its content; and [ADR 0001](0001-bulk-read-path-for-statistics.md)'s gain#1261
  Amendment, whose default-deny argument named the abstract methods this record moves

## Context

[ADR 0008](0008-scan-owns-validation.md) is titled "the statistics scan owns
validation; reads never validate", and its decision 4 gave each score kind its own
rule in its own body. Both halves were carried out, but in different places: the
*ownership* went to the scan, and the *rule* stayed on the read class.

So `validate_records` and `validate_record_arrays` were `@abstractmethod` on
`GenomicScore`, with one body per kind in `genomic_scores/position.py`, `allele.py`
and `fragment.py` — while their only callers were the scan's two doors
(`genomic_scores_impl/scan.py`: `scan_region` and `_validated_batches`) and
`statistics/alleles.py`'s `allele_arrays_folded_into`. A reader opening
`position.py` met a rule that nothing in the read path invokes, and the file layout
said the opposite of the record's own title.

That is the smell this record acts on: a class carrying a rule only its consumer
applies. Nothing about ADR 0008's decision changes — the scan still owns
validation, kinds still state their own rules, reads still never validate. What
changes is where the rule is written down.

## Decision

**1. The two validators are module-level functions in the statistics package.**
`gain/genomic_resources/statistics/record_validation.py` exports

- `validate_records(score, records)`
- `validate_record_arrays(score, batches, chrom)`

both `functools.singledispatch`, both dispatching on the score's class. Their
signatures are otherwise what the methods had; the receiver became the first
argument.

**2. The base registration refuses.** The registration for `GenomicScore` raises
`NotImplementedError` naming the class it was handed. This is decision 4's "no rule
nobody chose" restated for a registry: the three kinds are flat siblings of
`GenomicScore`, so a kind added later dispatches to the base registration and cannot
be scanned until someone registers a rule for it. What it may *not* do is inherit
one silently.

**3. Two rule bodies serve three kinds.** There are two rules, not six: a position
score refuses records that overlap *or touch* (`begin <= prev_end`); allele and
fragment refuse only a record that moves backwards (`begin < prev_begin`), and
differ solely in the noun their message names. So allele and fragment share one
private body per shape, parametrised by that noun, and position keeps its own. The
kind-to-rule map stays six explicit `register` lines in one module — the map is the
part worth reading in one place.

ADR 0008 accepted the six-way duplication for a reason that lapses here: what it
was unwinding was a *hidden* shared statement — one class attribute two validators
interpreted for themselves, which concealed that they enforced different subsets of
one rule. A body called explicitly from two registrations conceals nothing. The
divergence risk ADR 0008 names lives in shared *interpretation*, not in a shared
call.

**4. The span helpers follow their callers.** `GenomicScore._record_to_begin_end`
becomes a private helper of the new module, its only remaining caller having been
the three `validate_records` bodies. `GenomicScore._inverted_span_error` is promoted
to a public `resource_errors.inverted_span_error`, beside `overlapping_records_error`
and `backwards_records_error`, and the read-path callers raise it directly.
`GenomicScore` ends up with neither.

`inverted_span_error` takes **scalars** — `(chrom, pos_begin, pos_end, ref, alt)` —
not a `Record`, and that is not a style choice. The record slot constants live in
`genomic_position_table/record.py`, and `genomic_position_table/__init__.py` imports
`table_tabix`, which imports `resource_errors`. Any import of the slots into
`resource_errors` therefore executes that package's `__init__` and closes an import
cycle. Scalars keep `resource_errors` what it is today: a module that imports nothing
else from gain. Its two siblings already take scalars, so the file stays uniform.

## Why the statistics package, and not the implementation classes

The scan's task bodies receive a serialized resource and build a `GenomicScore`;
they never hold an implementation. `test_the_statistics_scan_does_not_import_the_implementation_classes`
forbids `genomic_scores_impl/scan.py` importing anything from its own package —
gain#1007 split it that way precisely because the implementation base imports `scan`
to schedule task bodies, so an import back closes a cycle. And `statistics/alleles.py`
sits below `implementations/` and calls the array validator too.

The statistics package is the one home both callers can already import from without
touching a fence. It is also where the neighbouring per-kind decisions already went:
gain#1177 moved the per-kind statistic *gates* onto `statistics/coverage.py` and
`statistics/fragments.py`.

## The enforcement tier changes, and what replaces it

This is the cost, and it is worth stating plainly rather than claiming the fallback
preserves what was there.

`@abstractmethod` on `GenomicScore` does not enforce at construction — nothing in
that MRO is an `ABC`, which ADR 0008 decision 4 records and accepts. But it *is*
enforced statically: mypy reports `Cannot instantiate abstract class … with abstract
attribute … [abstract]` and pylint reports `W0223`, both without any `ABCMeta`. A
kind that omitted the method was refused before anything ran.

A missing `@register` has no such analogue. No checker verifies that a
`singledispatch` registry is complete. So the refusal moves from lint time to run
time — the base registration raising when a scan reaches it — and the static half is
replaced by a test: `test_every_buildable_kind_is_registered` asserts that every
class `build_score_from_resource` can return appears in both dispatchers'
`.registry`.

This matters beyond bookkeeping, because [ADR 0001](0001-bulk-read-path-for-statistics.md)'s
gain#1261 Amendment retired the bulk scan's resource-kind condition on the strength
of exactly this guarantee: "`record_weight` and `validate_record_arrays` are
`@abstractmethod` on `GenomicScore`. A new kind cannot exist without stating its own
record semantics." Half of that sentence is now this record's responsibility, and
that Amendment is annotated accordingly. `record_weight` is untouched and still
abstract.

## Alternatives considered and rejected

**Leave the bodies on the classes and re-word ADR 0008.** The cheapest option, and
it keeps a rule in a file whose other readers never invoke it. ADR 0008's title is
not the thing that was wrong.

**Put the functions on the implementation classes.** Rejected by the fence above:
`scan.py` cannot import its own package, and the task bodies hold a score, not an
implementation.

**An `isinstance` chain instead of `singledispatch`.** This is *not* the
`isinstance(score, (PositionScore, AlleleScore, FragmentScore))` gate that gain#1261
considered and rejected, and the distinction is worth drawing because a reader
arriving from ADR 0001 will assume otherwise. That one was a **gate**: a boolean
answering a question the factory had already settled, which is why it was called a
relocated tautology. This is the **dispatch itself**, and its default is a refusal,
not a `False`. A hand-written `isinstance` chain would do the same job; it was
rejected only because it puts the kind-to-rule map in a chain of branches instead of
in six lines that read as a table, and because a chain's fallthrough is easy to write
as "the last kind" rather than as a refusal.

**Make `GenomicScore` a real `ABC` first, so the static check survives.** Rejected
for the reason ADR 0008 gives and this record does not revisit: `ABCMeta` would
retroactively enforce the abstract `get_schema` that `ResourceConfigValidationMixin`
declares in the same MRO, across every score kind and every third-party subclass. It
would also not help — the methods are leaving the class, so there would be nothing
abstract left to enforce.

## Consequences

- `GenomicScore` and its three kinds define no `validate_records`,
  `validate_record_arrays`, `_record_to_begin_end` or `_inverted_span_error`.
- The scan's two doors and `allele_arrays_folded_into` call module functions rather
  than methods. No shim: the methods were instance methods, an org-wide search found
  no caller outside gain, and the `genomic_scores` facade is untouched.
- A new score kind must be registered in `record_validation.py`. Forgetting is caught
  by `test_every_buildable_kind_is_registered`, and at run time by the base
  registration — not by mypy or pylint.
- No user-visible behaviour change: every message, including the per-kind noun, is
  what it was.
