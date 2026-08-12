# 17. Record filtering is a genomic-score capability, not an annotator one

- **Status:** accepted
- **Date:** 2026-08-12
- **Issues:** [gain#805](https://github.com/iossifovlab/gain/issues/805);
  the error-naming rule it preserves is
  [gain#477](https://github.com/iossifovlab/gain/issues/477)

## Context

Two annotators grew the same feature independently. The allele score
annotator's `allele_filter:` and the fragment score annotator's
`fragment_filter:` (legacy `cnv_filter:`) each carried a Lark grammar, a
recursive tree-to-predicate compiler and a filtering loop — near-duplicates
that had drifted apart in two ways that matter to whoever writes the YAML:

- the fragment grammar's identifiers were **letters only**, so a score named
  `1000G` — the shape the GRR actually publishes — could not be filtered on
  at all, while the allele grammar accepted it;
- the fragment grammar's numbers were **unsigned**, so `> -1` parsed on one
  annotator and failed on the other.

Neither divergence was a decision; both were what happens when one copy is
fixed and the other is not.

Worse, the copies agreed on two behaviours nobody chose. A filter naming a
score that the resource does not define was only discovered at *read* time:
the allele side raised `KeyError` per record from inside a fetch loop, and
the fragment side read `None` out of the extracted dict and silently
selected nothing — a filter that quietly matches zero records is the worst
of the two. And a comparison against a missing value was undefined: `None >
0.1` raised `TypeError`, while `freq == other_freq` on a record carrying
neither answered **True**, because `None == None` does.

Filtering is not an annotation concern. It reads score values off a record,
against score definitions, both of which belong to the score.

## Decision

**A filter is compiled by the score and passed back to its reads.**
`GenomicScore.compile_filter(expression)` returns an opaque `ScoreFilter`;
`fetch_records`, `fetch_allele_record`, `fetch_allele_scores` and
`fetch_fragment_scores` take it as `score_filter`. `None` is exactly the
pre-existing behaviour. Every score type inherits the capability from the
base, so a position score can be filtered without anyone adding a feature.

**One grammar, the superset of the two it replaces.** Digits are allowed in
identifiers and numbers may be negative — the union, so no expression that
parsed before stops parsing.

**The operator set is frozen** at `>`, `<`, `==`, `in`, `and`, `or`.
Extending it changes what every filter in every deployed pipeline may mean,
and is a separate decision with its own issue.

**Variable names are validated at compile time**, against
`score_definitions`, refusing an unknown name with the valid names listed.
The check happens once per pipeline build rather than once per record.

**A comparison with a missing operand is False.** Missing means an NA cell
(which parses to `None`) or a real `nan`. False, not an exception and not a
skipped record: the *clause* fails, so the record can still be selected by
the other arm of an `or`. This is decided once, for all operators, because
no operator in this language can say anything about a value that is absent.

**The annotators keep their configuration surface and nothing else.** The
`allele_filter` / `fragment_filter` / `cnv_filter` spellings, their
resolution, and the `AnnotationConfigurationError` that names *the spelling
the user actually wrote*, all stay in the annotators — the score is handed
an expression and does not know which key it came from (gain#477).

## Consequences

A filter may now name any score the resource defines, including one outside
the `scores` a fragment read requested: the predicate runs on the record,
not on the dict handed back. Previously such a name read `None` and matched
nothing.

Two expressions that were accepted and are now refused, both at
configuration time and both by intent: one naming a score that does not
exist, and one whose number is malformed (`0.5.5` parsed and then failed on
`float()`; it now fails to parse). Each was already an error; each now
reports as one, earlier and by name.

The region path of the allele annotator still filters *outside* the fetch.
It must distinguish "no records here" (absent data) from "no record the
filter kept" (an empty selection), and those are different answers to the
caller; pushing the filter into `fetch_records` would collapse them.

## Alternatives rejected

**Share the grammar, leave the compilers in the annotators.** Removes the
drift that is visible in the YAML but not the duplicated tree-walking, and
leaves the missing-value and unknown-name semantics defined twice — which is
where the two copies had *already* diverged silently.

**Keep filtering in the annotators and pass a callable into the fetches.**
The score would have to accept an arbitrary predicate it cannot validate,
so unknown names stay a read-time failure and the compile-time refusal —
the main thing wrong with the old behaviour — becomes impossible.

**Make a missing operand raise.** Honest, but it turns a data property into
a crash mid-annotation: NA cells are normal in these resources, so the
filter must be total over the data it is pointed at.
