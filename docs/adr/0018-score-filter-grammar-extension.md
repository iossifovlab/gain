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
The grammar is unambiguous, so precedence no longer depends on how the parser
resolves ambiguity, and adding an operator means choosing its level in the
cascade rather than hoping. `and` over `or` is unchanged; the pre-existing
tests that pin it by evaluation stayed green across the restructure, which is
what makes this a restructure rather than a redefinition.

**A variable name narrows; a quoted literal does not.** The one terminal
became two. A name is letters, digits and `_`, still allowing a leading run
of digits so that `1000G` — the shape the GRR publishes, and the reason 0017
widened the fragment side in the first place — keeps working. A literal keeps
the old, wider class verbatim: its quotes already delimit it, so it needs no
narrowing for `(` and `!` to be unambiguous, and narrowing it would have
quietly broken filters the annotator documentation advertises.

Splitting rather than narrowing both is the whole point. Sharing one terminal
is what made "make `(` mean something" and "keep `"path(o)genic"` working"
look like opposites.

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

**A score whose id contains punctuation can no longer be filtered on.** The
resource may still define it and every other read still returns it — only the
filter language cannot name it. This is the price of the syntax, and it is
paid in a currency we checked before spending: of 317 distinct score ids in
the local GRR mirror, none contains `(`, `)`, `!`, `@`, `#`, `$`, `%`, `^`,
`&`, `*` or `+`. The private SFARI GRR was **not** scanned, so this is
evidence and not proof. If such a name ever turns up, the fix is a quoted
form for names — not a re-widened bare name, which would take the syntax back
out.

**Two expressions change how they fail, and none changes what it selects.**
An expression whose identifier used a dropped character now fails to parse
where it used to fail as an unknown name. Both are configuration-time errors
naming the same text; the new one points at the character.

**`not`, `and`, `or` and `in` remain matchable as names.** A resource
defining a score called `not` is ambiguous with the keyword, exactly as one
called `and` already was. The cascade did not create this and does not fix
it; refusing such names at compile time would be a new restriction on what a
resource may define, which is a bigger decision than this one.

**The grammar is now unambiguous, but still parsed with Earley.** Nothing
here requires that; an LALR parser would work and would be faster to build.
It was left alone because the build is already cached behind first use and
the change is not free of risk — the keyword-versus-name overlap above is
exactly what a fixed lexer handles differently.

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
