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

*Amended by gain#634:* the enumeration above is not exhaustive. A key can also
have no column because the published index **predates the label** — a curator
added it and no `grr_manage` run has rebuilt the index since. The resource
serves the label from its `meta.labels` regardless, so "no column" does not
imply "no resource carries the key", and settling the clause for every
resource at once was wrong in the direction that silently loses them: since
supplying a `search_term` or a `resource_type` is what routes the search
through the index, adding a filter that should only narrow the result set
emptied it instead. Such a clause is now handed back to the caller and
re-asked of each resource the statement yields, which the indexed path already
materialises in full.

*Amended by gain#646:* label clauses are no longer pushed down at all. `#634`
reached only the keys with no column; a key that **does** have a column was
still settled out of the value the index recorded when it was built, which a
curator's edit to the value makes wrong in both directions — a false negative
on the live value, and, unlike `#634`, a false **positive** on the recorded
one, returning a resource that does not satisfy the query. No post-filter
repairs the first: it is a row the statement never yields. So the index now
narrows by `contents MATCH`, by `type` and by the id glob, and **every**
`LabelClause` is handed back to the caller and asked of the resource's own
`meta.labels`. The two routes then agree on every resource the index knows
about, whatever it recorded for a label.

The rest of the decision stands. Absence still reads as `""` — that is what
`matches_in` does for a key the resource lacks, exactly as the index's `""`
did. Not restating the comparisons in SQL stands too, and is now moot for
labels and load-bearing only for the id glob, which keeps its scalar function.

The half of the decision that accepts the empty string stands, and needs no
column: a clause that matches `""` is a tautology under this grammar — a value
must be at least one character, so `in` can never accept `""`, and the only
`=` values `fnmatch` accepts `""` for are globs of `*` alone, which accept
every string. Dropping it is sound however stale the index is.

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

  *Amended by gain#646:* the enumeration now matters only at index-build
  time. "A clause on one of them must not be answered out of the column that
  shares its name" was a statement about the read path, which no longer
  answers any label clause out of any column — a clause naming a field is
  asked of the resource's `meta.labels` like every other, and holds for no
  resource, since the build refuses such a label. The refusal above stays
  necessary: it is what keeps the *column* unambiguous for the filters that
  are still answered out of the index.
- **A published index is untrusted input on this path.** The read path
  deserializes whatever `.CONTENTS.sqlite3.gz` the repository serves, so the
  vetting `#464` added to the index *build* guarantees nothing here. Column
  names were re-checked against `INDEX_COLUMN_RE` before one was spliced, and
  the identifier quoted as well. Without both, a crafted column name reached
  through a label key — the grammar admits parentheses — bypassed the id glob
  and the type filter.

  *Amended by gain#646:* no label key is spliced into the statement any more,
  so there is nothing on this path for a crafted column name to reach and the
  read-path re-check is gone with it. The index-*build* vetting stays — it
  answers `#464` and `#542`, which this does not make redundant — and the test
  that publishes a hostile column stays too, now pinning that such a key
  simply names a label no resource carries.
- The Python path remains, and is the only one that works on a repository
  with no `.CONTENTS.sqlite3.gz`. A query-only search still never opens the
  index. The two are pinned against each other by a differential test rather
  than by inspection.

  *Extended by gain#634:* that differential built its index from the very
  resources it then compared against, so index and resources agreed by
  construction and it could not see a divergence that only a stale index
  produces. It now also runs against a repository whose index was published
  before one of its labels.

  *Extended by gain#646:* an index predating a label *key* is only half of
  stale. The differential also runs against one published before a label
  *value* was edited, over clauses naming both the live value and the
  recorded one — the second direction being the one that catches a resource
  returned that does not satisfy the query.
- **The routes agree for every resource the index knows about.** A published
  index is a separate artefact from the resources, and nothing forces it to be
  current, so the qualification is not idle: a resource added since the index
  was built is not returned by the indexed route at all. That much is
  inherent, since only the index can answer a `search_term`, and rebuilding
  with `grr_manage` is what closes it. What gain#646 settles is narrower than
  staleness as a whole: a *label* clause is read from the resource on both
  routes. `search_term` and `resource_type` both route through the index and
  are still answered out of the values it recorded, so a `meta.description`
  or a `type` edited since the build reads as it was then — the same
  false-negative/false-positive pair, in the two filters that only the index
  can serve.
