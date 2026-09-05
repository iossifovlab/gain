# 24. A bool score reads its cell's text, and declares no NA sentinels

**Status:** accepted
**Date:** 2026-09-05
**Issues:** gain#1192 (follow-up from gain#1111); follow-ups gain#1221, gain#1222

## Context

`SCORE_TYPE_PARSERS` maps a score's declared value type to the callable that
turns one raw cell into one value. Three of its four entries were builtins —
`str`, `float`, `int` — and the fourth was Python's `bool`.

`bool` is not a parser. It is a truthiness test: it never reads text, never
raises, and answers `True` for every non-empty string. So a `bool`-typed score
read from a text table answered `True` for `False`, for `0`, for `yes`, and for
every other cell that was not empty. The literal `False` could not be expressed
in a table at all.

Read off the three incumbents, the seam's actual contract is:

* `Any -> value`;
* raise `ValueError` on text it cannot read;
* be **idempotent** on a value that is already parsed.

The third clause is not decoration. `parse_vcf_scoredefs`' config-override
branch copies `value_parser` from the *config-derived* definition for every
value type, so a VCF score declared `type: int` runs the `int` builtin over
pysam's already-decoded `int`, and works because `int(3) == 3`. `float`, `int`
and `str` satisfy all three clauses for free. `bool` satisfied none of them.

The defect reached the built statistics, not only the reads: a bool score's
histogram is categorical, so every row of a text bool column landed in the
`True` bin and the published histogram said the flag was always set.

The blast radius on deployed content was nil. The only `type: bool` scores in
`grr` and `grr_sfari` are dbSNP's 35 flags, and dbSNP is VCF-backed, where
pysam decodes a `Flag` to a real `True`/`False` and presence-is-true is correct.
The bug was latent, waiting for the first text or tabix table with a bool
column.

## Decision

**`bool` parses its cell's text against a closed set of eight spellings** —
`True`, `true`, `TRUE`, `1` for true; `False`, `false`, `FALSE`, `0` for false.
A value that is already a `bool` is returned unchanged. Everything else raises
`ValueError`, which the existing guard in `GenomicScoreDef.parse_value` logs and
turns into a non-value.

**`bool` declares no default `na_values`**, where `float` and `int` declare
`""`, `nan`, `.` and `NA`.

## Why the vocabulary is closed

A table is machine-written, and an author who means false has a way to say so.
Widening to `yes`/`no`, `T`/`F` or locale forms would buy a handful of
resources at the cost of a vocabulary nobody can state — and the entire point
of the fix is that the text is *read* rather than guessed at. `gain.utils.helpers.str2bool`
is the lenient version of this decision (it accepts `yes`, `t`, `y`, and answers
`False` for anything it does not recognize); it is deliberately **not** reused
here, and it is dead code.

There is a structural twin in `fsspec_protocol._resolve_read_only`, which also
does bool-passthrough then closed-set lookup then a `ValueError` naming the
sorted spellings. Its vocabulary is deliberately different — a `read_only` flag
is human-written configuration and accepts `yes`/`on`/`off` case-folded, where
a score cell is machine-written data. Two closed sets, stated separately, beat
one parameterized helper serving two call sites.

## Why bool declares no NA sentinels — the alternative that will look attractive

Giving `bool` the numeric types' sentinels is the obvious tidy-up. It reads
better: a `.` or an empty cell would be a silent non-value instead of the parse
failure it now is. It was measured and rejected.

`na_values` is serialized into a resource's statistics hash
(`GenomicScoreImplementation.calc_statistics_hash` emits
`str(sorted(str(na) for na in score_def.na_values))` per score). Populating the
default therefore moves the hash of **every bool score published anywhere**:

```
before  "na_values": "[]"
after   "na_values": "['', '.', 'NA', 'nan']"
```

That is dbSNP's 35 flags in two GRRs, each scheduled for a genome-wide
statistics recompute that would arrive at byte-identical numbers — a VCF flag
reaches the parser as a `bool` and never as one of these text tokens.

So the default stays empty, and a missing cell stays a refusal. The value is
`None` either way; what differs is the report. **The cost is real and worth
stating plainly:** `parse_value` reports with `logger.exception`, so an
unreadable cell costs a formatted traceback, measured at ~43 µs against ~22 ns
for the old truthiness answer. A 50%-sparse text bool column of 10M rows would
spend minutes formatting tracebacks.

The mitigation is configuration, not code: such a resource declares
`na_values: ["", "."]` and the sentinels are then tested *before* the parser is
called. This is why the `na_values` paragraph in `docs/source/grr.rst` is
load-bearing for performance and not only for authoring clarity — it is the
documented escape hatch for a cost the default deliberately accepts.

A future maintainer who wants to flip this needs a statistics-recompute plan
for the deployed dbSNP resources, not just the one-line edit. `bool`'s empty
default is asserted by a test that names this ADR, so filling it in is a
deliberate act.

## Why bool is not in `_NA_COERCIBLE_TYPES`

Coercion adds each sentinel's *parsed* form to the sentinel set, so that a
numeric backend's raw `-1.0` matches a configured `"-1"`. For `bool` that is
not merely unnecessary but destructive: a sentinel that parses to `False` would
install `False` itself as an NA value, and every false datum in every bool
column would read as no value at all. This holds whatever sentinels a resource
configures, so the exclusion is permanent, not a default.

## Why the fix is scalar-only

`BULK_PARSEABLE_VALUE_TYPES` excludes `bool`, so `parse_array` — a hand-written
vectorized duplicate of `parse_value`, held to it only by
`test_parse_array_agrees_with_parse_value_fuzz` — has no bool path to keep in
step. Adding one would create a second bool parse with no consumer, which is
the shape that let this class of bug exist in the first place. If `bool` ever
does join that tuple, the vectorized form must be derived from
`_BOOL_TEXT_VALUES` rather than restating the eight spellings.

## Consequences

* A text or tabix table can express a boolean score. Its categorical histogram
  now counts both values in their real proportions.
* Any such resource built before this change carries a statistics artifact that
  says the flag was always set, and **will not self-invalidate**: the statistics
  hash covers config, histograms, table definition, file md5s and per-score
  `id`/`type`/`name`/`index`/`na_values` — not the parser. None exist in the
  published GRRs, but a private GRR needs a manual rescan.
* A resource that declares `type: bool` over a column spelled `yes`/`no` stops
  annotating as `True` and starts annotating as empty, with one logged
  exception per cell. That is the intended correction, and it is loud.
* Two adjacent defects were found while verifying this and left alone, because
  neither is caused by it: a VCF score named in a `scores:` block *without* a
  `type:` key gets `value_type` from the header but `value_parser` from the
  config's `float` default, so its `Flag` reads `1.0`/`0.0` (gain#1221); and
  `stringify` renders `False` identically to `None`, so annotation output still
  cannot show a false flag (gain#1222). The second means the user-visible
  symptom in gain#1192's own repro is only half closed at the CLI.
* Four score-value-type tables are now keyed by the same four type names
  (`SCORE_TYPE_PARSERS`, `_DEFAULT_NA_VALUES`, `_NA_COERCIBLE_TYPES`,
  `BULK_PARSEABLE_VALUE_TYPES`), plus several more across the score kinds,
  `histogram` and `scan`. Consolidating them into one per-type object is
  attractive and was deliberately not done here: it would bury a one-entry fix
  under a cross-module refactor, the aggregator defaults are per score *kind*
  rather than per value type and so cannot join, and the valuable half — making
  the resource schema enumerate the legal value types, which it currently does
  not — would start refusing configs that open today.
