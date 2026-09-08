# 26. Annotation output spells a flag `yes`/`no`, in every sink

**Status:** accepted
**Date:** 2026-09-08
**Issues:** [#1222](https://github.com/iossifovlab/gain/issues/1222)
(follow-up from [#1192](https://github.com/iossifovlab/gain/issues/1192),
ADR 0024)

## Context

`gain.utils.stringify.stringify` is the one place a value becomes the text a
reader sees: the tabular sink renders every cell through it, the VCF sink
renders every INFO value through it, and `allele_key` renders a suffixed
score through it so that a key's spelling cannot drift from the output's.

It rendered a bool asymmetrically. `True` was `yes`; `False` was the sink's
missing-value marker — the empty string in a table, `.` in a VCF — which is
byte-for-byte what `None` renders. A reader of an annotated file could not
tell a flag that is false from a flag that has no value, and a suffixed
allele key named a false record and a valueless record identically.

This was unreachable from a text table until ADR 0024: `bool`-typed scores
answered `True` for every non-empty cell, so a `False` never reached the
formatter. It was always reachable from a VCF-backed score, where pysam
decodes an absent `Flag` on a present record to a real `False` — dbSNP's 35
flags rendered an unset flag exactly like a variant dbSNP had never seen.

## Decision

**A bool renders `yes` or `no`, in both sinks.** `None` alone takes the
sink's missing-value marker. The three values are pairwise distinct
everywhere `stringify` is used, and a suffixed allele key follows for free.

**The two sinks do not differ for a bool.** Every attribute the VCF sink
declares itself is `Type=String, Number=A`, not a VCF `Flag`, so there is
no presence-is-true convention to honour; `.` stays reserved for "no value
for this allele", and the sink's writer drops the key from INFO only when
no allele of the record has a value. (A key the *input* VCF already
declares keeps the input's type; if that type is `Flag`, the value never
reaches `stringify` and a false flag is still written as absent. That edge
is pre-existing and outside this record.)

## Why `no` and not the parser's `False`

ADR 0024 closed the *input* vocabulary of a `bool` score to eight spellings
— `True`/`true`/`TRUE`/`1` and `False`/`false`/`FALSE`/`0` — and
deliberately refused `yes`/`no`. Spelling the output `True`/`False` would
have let an annotated table be re-read as a bool score with no
configuration, which is attractive. It was not chosen because it would also
change every `True` already in every user's output from `yes` to `True`,
for a round-trip nobody has asked for. `no` is the smallest change: nothing
that rendered before renders differently, and only the value that had no
spelling gains one.

The two vocabularies are therefore stated separately and on purpose,
exactly as ADR 0024 keeps `str2bool` and `_resolve_read_only` apart: the
parser reads a flag from a machine-written table, the formatter spells a
flag for a person. A future maintainer who wants annotated output to
round-trip as a score should widen the parser to accept `yes`/`no` as its
own decision, not re-spell the output.

## Consequences

* Every annotated output over a bool score changes at its false cells,
  from empty/`.` to `no`. With dbSNP's flags that is every dbSNP-known
  variant whose flag is unset. That is the intended correction and it is
  visible; positions with no record are unchanged.
* `include_attributes` over a bool score now yields distinct keys for a
  false and a missing value.
* gpf's parquet pipeline imports `stringify` but applies it only to
  `str`-typed attributes; bools reach parquet natively and are untouched.
* gain#1221 — a VCF score named without `type:` parses its `Flag` as
  `1.0`/`0.0` — is a parser defect and is not affected by this record.
* The guarantee is for a scalar attribute value. The VCF sink renders the
  members of a dict-valued attribute with `str()` rather than through
  `stringify`, so a bool inside one still spells `False` there. That is a
  pre-existing gap in the sink, not in the helper, and is left alone here.
