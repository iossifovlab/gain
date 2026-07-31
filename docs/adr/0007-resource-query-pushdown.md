# 7. The resource query is pushed into the FTS index, and absence reads as empty

**Status:** accepted
**Date:** 2026-07-31
**Issues:** iossifovlab/gain#442, following #441; index-column vetting from #464

## Context

`search_resources(search_term, resource_type, resource_query)` takes three
filters that conjoin. Two of them — `search_term` and `resource_type` — were
matched in SQL against the FTS `contents` index. The third, the wildcard
query language `#441` lowered into the GRR, was applied afterwards, in
Python, over the rows the first two returned. One signature, two evaluation
engines, and the invariant that they agree held only because one of them
never ran on the indexed path.

Pushing the third filter into the same statement runs into a semantic
mismatch that has nothing to do with SQL syntax. Building the index fills
`""` for every label column a resource does not carry, so **the index cannot
distinguish an absent label from an empty one**. `ResourceQuery.match_labels`
could: it returned `False` for a missing key before running the predicate.

For any pattern that accepts the empty string, no `WHERE` clause reproduces
that. A raw comparison over-matches, because it accepts the resources whose
label is absent. Adding `AND col != ''` — the obvious repair — under-matches,
because it rejects the resources whose label is genuinely empty. Measured on
the encode GRR (7,922 resources, 21 index columns), both cases occur in bulk
in *different columns of the same repository*: `target` is absent from 1,776
resources and empty on none, while `submitter_comment` is empty on 7,199 and
absent from none. `simple_biosample_summary` (2,477 empty) and `doi` (4
empty) are the same shape. So the guard is right for exactly the column that
motivates it and wrong for the others, and a column carrying both cases
cannot be expressed at all.

## Decision

**Match an absent label as `""`.** The matcher gives up the distinction the
index never had, instead of the index acquiring one the matcher needs. Both
paths then agree with no guard.

**Do not restate the comparisons in SQL.** A parsed query carries its clauses
as data rather than closures, and each clause's own `matches` is registered
as a scalar function on the metadata connection. `*` and `in` are defined
once, whichever engine runs the query.

**Settle an unknown label key by asking the clause about `""`.** A key with
no label column — because no resource carries it, or because the name belongs
to a field of the resource rather than to a label — holds for every resource
if the clause accepts the empty string and for none if it does not.

## Why this scope

*Why not the guard.* It is not a near-miss; it is wrong in the direction
that silently loses resources, on a column shape that is common rather than
exotic.

*Why not reshape the index.* A side `(full_id, key, value)` table would make
absence representable as "no row" and would keep the old semantics. It is
also an index-format change requiring every published GRR to be rebuilt, to
buy back a distinction with one known use — `[key="*"]` as a has-this-label
test — that appears in no config, doc or test in this repository. If a
has-this-label test is wanted later, adding `?` to the value charset spells
it `[key="?*"]` and stays consistent, since fnmatch and SQLite `GLOB` agree
that `?` is exactly one character.

*Why not native `GLOB`.* It is roughly twice as fast as the Python
post-filter on a full scan of the encode index (12–17 ms against 21–32 ms;
1–2 ms either way once a `search_term` narrows first), where the scalar
function measures about 0.85x. But it is a *second definition* of what `*`
means, free to drift from the first — which is what `resource_query` exists
to prevent. `LIKE` for the `in` operator would have been worse than a drift
risk: it is ASCII case-insensitive, and `in` is not.

*Why the push-down is not a performance change.* The indexed path already
calls `get_all_resources_dict()` unconditionally to resolve rows, and that
memo — ≥323 ms on encode just to parse labels out of `.CONTENTS.json` —
dominates the 1–32 ms of filtering by an order of magnitude. The reason to
do this is that the three filters become one statement with one meaning, not
that it is faster. Anyone chasing the wall clock should attack the
materialisation, which the side table above would enable.

## Consequences

- **`[key="*"]` changes meaning**, from "has this label" to a tautology. This
  is the whole blast radius: the grammar requires at least one character in a
  value, so no containment test can accept `""`, and neither can a literal.
  It reaches annotation pipeline configs too — `query_resources` uses the same
  matcher — so a config in `iossifovlab/grr` or a GPF instance using that
  spelling would silently widen. The `-q` help text says so.
- **The index's non-label columns have to be enumerated.** `full_id`, `id`,
  `type`, `description`, `summary` and the score implementation's
  `score_ids` / `score_descriptions` name columns that are not labels, and a
  clause on one of them must not be answered out of the column that shares
  its name. `GR_INDEX_NON_LABEL_COLUMNS` is that list, and an implementation
  contributing a further field must extend it — the index cannot say on its
  own which of its columns came from a label.

  *Superseded by gain#542:* as first written, a non-score resource carrying a
  label literally named `score_ids` still merged into the score column, and
  that ambiguity was left unfixed here. Recording which columns came from
  labels at build time was considered and rejected — it makes the clause
  answerable for the resource carrying the label but wrong for every resource
  that contributes the field, moving the divergence rather than closing it.
  The collision is refused at index-build time instead, so a name in
  `GR_INDEX_NON_LABEL_COLUMNS` can never also be a label and the column has
  one meaning.
- **A published index is untrusted input on this path.** The read path
  deserializes whatever `.CONTENTS.sqlite3.gz` the repository serves, so the
  vetting `#464` added to the index *build* guarantees nothing here. Column
  names are re-checked against `INDEX_COLUMN_RE` before one is spliced, and
  the identifier is quoted as well. Without both, a crafted column name
  reached through a label key — the grammar admits parentheses — bypassed the
  id glob and the type filter.
- The Python path remains, and is the only one that works on a repository
  with no `.CONTENTS.sqlite3.gz`. A query-only search still never opens the
  index. The two are pinned against each other by a differential test rather
  than by inspection.
