# 18. The score filter's precedence is declared, and its names are narrow

- **Status:** accepted
- **Date:** 2026-08-13
- **Issues:** [gain#806](https://github.com/iossifovlab/gain/issues/806);
  supersedes the "operator set is frozen" clause of
  [0017-score-filtering-is-a-score-capability.md](0017-score-filtering-is-a-score-capability.md)

## Context

[ADR 0017](0017-score-filtering-is-a-score-capability.md) unified two drifted
filter grammars into one and froze its operators at `>`, `<`, `==`, `in`,
`and`, `or` — deliberately, so that the same-behaviour refactor and the
language extension would not travel together. Extending it was left as a
separate decision. This is that decision, and reading the grammar in order to
make it turned up two things that were not decisions at all.

**`and` bound tighter than `or`, but nothing said so.** The rules were
mutually recursive — a comparison could be a connective and a connective's
arms could be comparisons — which makes the grammar ambiguous. Both readings
of `a and b or c` were derivable, and Earley's ambiguity resolution picked
one. It happened to pick the conventional one, and the behaviour was pinned
by a test, so the *language* was right. But nothing in the grammar chose it,
which is not a foundation to add `not` and grouping to: any new rule can
shift how the ambiguity resolves, and the shift is silent.

**`(`, `)` and `!` were already legal in a name.** The single `word` terminal
admitted them along with `@#$%^&*+`. So `(freq > 0.1 or freq > 0.2) and x`
parsed *today* — as a comparison between variables named `(freq` and `0.2)`.
Before 0017 that silently misfiltered; since 0017 validates names at compile
time it is refused, but as an unknown name rather than as a syntax error.
That same terminal was also the body of a quoted string literal, where the
punctuation is genuinely wanted and documented.

## Decision

**The language gains `>=`, `<=`, `!=`, `not` and parenthesized grouping.**
Nothing else about it changes.

**Precedence is declared by a rule cascade** — `or`, then `and`, then `not`,
then a primary that is either a comparison or a parenthesized expression.
Precedence no longer depends on how the parser resolves ambiguity — no
expression the grammar accepts has more than one derivation — and adding an
operator means choosing its level in the cascade rather than hoping. `and`
over `or` is unchanged; the pre-existing
tests that pin it by evaluation stayed green across the restructure, which is
what makes this a restructure rather than a redefinition.

**A variable name gives up exactly three characters; a quoted literal gives
up none.** The one terminal became two. A name loses `(`, `)` and `!` —
the characters the new syntax needs — and keeps everything else it had,
including a leading run of digits (`1000G`) and the `@#$%^&*+` that made it
odd in the first place. A literal keeps the old class exactly — it is
derived from the name's, plus the three characters, so the two cannot drift —
and narrowing it would have quietly broken filters the annotator
documentation advertises. Note that this leaves a literal a punctuation
whitelist rather than "anything between quotes"; `.`, `-`, `/` and spaces
are still out, which is the deferred widening below, not a property of the
quotes.

Splitting rather than narrowing both is the whole point. Sharing one terminal
is what made "make `(` mean something" and "keep `"path(o)genic"` working"
look like opposites.

The narrowing was very nearly wider. Restricting a name to letters, digits
and `_` gives a grammar with no odd corners and closes the whole class of
future operator-versus-name collisions, and it was chosen on a survey that
found no affected score id. That survey was wrong — see the consequences
below. The rule that replaced it is: **a character leaves the identifier only
when the language needs it**, because a name is not ours to reshape. The GRR
publishes the names; the filter language reads them.

**A negation is not a comparison, and does not inherit its missing-value
rule.** 0017 decided that a comparison with a missing operand is False. That
carries to `>=`, `<=` and `!=` unchanged. It cannot carry to `not`, which
negates what a clause *answered*: on a record missing `x`, `x != v` is False
and `not (x == v)` is True.

The two are therefore not synonyms, and De Morgan does not cross the
comparison boundary. We chose this over the alternatives because it is the
only reading in which one rule governs comparisons and one rule governs
connectives; any other makes `not` inspect the operands of whatever it
negates.

## Consequences

**A score whose id contains `(`, `)` or `!` can no longer be filtered on.**
The resource may still define it and every other read still returns it — only
the filter language cannot name it. Of 832 distinct score ids in the local
GRR mirror, **none** contains any of the three. Two contain parentheses
(`hg19_pos(1-based)`, `hg18_pos(1-based)`) but they also contain `-`, which
was never legal in an identifier, so they were unfilterable before this
change and remain so for the same reason.

The private SFARI GRR was **not** scanned, so that is evidence and not proof.
If such a name turns up, the fix is a quoted form for names, not a re-widened
bare name — which would take the syntax back out.

**The first survey of that blast radius was wrong, and review caught it.**
It reported 317 ids and no affected names; the real figure is 832, and the
wider narrowing this ADR originally recorded would have broken three
published ones — `GERP++_RS`, `GERP++_NR` and `GERP++_RS_rankscore`, all in
`hg38/scores/dbNSFP4.9a`, which is an `allele_score` and therefore precisely
a resource `allele_filter` is pointed at. `allele_filter: GERP++_RS > 2`
worked before the change and would have failed the pipeline build after it.
The undercount came from a survey that only matched score ids at one
indentation, and it was believed because it agreed with what we wanted.

This is recorded rather than quietly fixed because the decision it corrupted
was a maintainer's, taken on the strength of the number. A blast-radius
measurement offered in support of a narrowing is load-bearing: it should be
reproducible from the ADR, which is why the query above is described in terms
of what it counted.

**Expressions change how they fail, and none changes what it selects.** An
expression whose identifier used one of the three dropped characters now
fails to parse where it used to fail as an unknown name. Both are
configuration-time errors naming the same text; the new one points at the
character.

**`not`, `and`, `or` and `in` remain matchable as names.** A resource
defining a score called `not` is ambiguous with the keyword, exactly as one
called `and` already was. The cascade did not create this and does not fix
it; refusing such names at compile time would be a new restriction on what a
resource may define, which is a bigger decision than this one.

**The cascade left one ambiguity, and a guarded keyword removed it.** A name
that BEGINS with `not` — `notch`, `nothing` — had two derivations: the whole
word as a name, and `not` applied to the rest. The cascade does not reach
that, because it is lexical rather than structural. `not` is therefore
matched only when the character after it cannot continue a name, which leaves
`notch` with one derivation.

This was very nearly left as a tie-break pinned by a test, on the belief that
guarding the keyword would reserve the word and stop a score named exactly
`not` from parsing. It does not: the guard asks only what FOLLOWS, and after
a bare `not` comes a space, so both the keyword and the name remain available
to the parser and only the name completes a comparison. Measured, not
assumed — `not > 2` selects records, and there is a test that says so.

The general form of both mistakes in this ADR is the same: an assumption
about blast radius, believed because it was convenient, and cheap to check.

**Earley is still the parser.** An LALR one would be faster to build, and the
build is already cached behind first use, so there is nothing to gain worth
re-testing the keyword-versus-name behaviour for.

## Alternatives rejected

**Bolt `not` and parentheses onto the ambiguous grammar.** Smaller diff, and
the tests would have passed. It also leaves the next person extending this
language with no way to know what binds tighter except to run the parser, and
leaves each new rule able to silently re-resolve the existing ambiguity.

**Narrow the shared terminal instead of splitting it.** One-line change; it
would have silently withdrawn `!@#$%^&*()+` from quoted literals, which the
annotator documentation explicitly promises. The failure would have surfaced
as somebody's ClinVar filter matching nothing.

**Widen literals while we were in there**, so that `Pathogenic/Likely_pathogenic`
— which the documentation currently tells authors to work around with a
substring match — could be written whole. Correct-looking and genuinely
wanted, but it is a language change nobody asked for in this issue, it needs
its own tests and documentation, and it interacts with the whitespace rule.
Left as follow-up.

**Make `!=` answer True on a missing operand**, so that `!=` and
`not (… == …)` agree. It reads better in isolation and breaks the rule that
makes the language predictable: a comparison would then assert something
about a value the record does not carry, and `x != v` would select records
that `x == v` and `x > v` and every other comparison declines to select.
