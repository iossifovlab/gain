# 12. A group repository skips a child that cannot answer a search, and fails only when none can

**Status:** proposed
**Date:** 2026-08-05
**Issues:** iossifovlab/gain#680; the label-vocabulary half settled by ADR 0007

## Context

A search carries two filters that can name a label, and only one of them is
repository-independent.

`resource_query` (`-q`) spells a label as a `LabelClause` evaluated against
`meta.labels`. ADR 0007 and its gain#634 amendment made that vocabulary
independent of any one repository: an absent label reads as `""`, and a clause
the index has no column for is deferred and re-asked of each resource the
statement yields.

`search_term` (`-s`) is handed to FTS5 as a `MATCH` expression, and FTS5's
expression grammar includes a **column filter** — `assay_term_name: "ATAC-seq"`
restricts the match to one column. `_create_contents_db` gives the index one
column per label key, so a label key doubles as a column-filter name. That is
what makes `-s` a label search at all, and nothing has ever made *that*
vocabulary repository-independent. A column filter is a valid expression only
against an index that has the column.

The two facts collide in a group. Measured on the group of `grr_encode_local`
and `grr_local`: encode's index publishes 14 label columns (`accession`,
`assay_term_name`, `target`, `doi`, …), the local GRR's publishes 3
(`reference_genome`, `source_genome`, `target_genome`), and they overlap in
exactly one. Only the non-label columns — `GR_INDEX_NON_LABEL_COLUMNS`:
`full_id`, `id`, `type`, `description`, `summary`, and the score
implementation's `score_ids` and `score_descriptions` — are guaranteed present
in every child. So `-s 'summary: foo'` is group-safe and
`-s 'assay_term_name: foo'` is not.

`GenomicResourceGroupRepo._search_resources` is a bare `yield from` over its
children, so a term valid in one child and invalid in another does not narrow
the result — it destroys it, after the valid children have already streamed
their rows:

```
$ grr_browse -s 'assay_term_name: "ATAC-seq"'
position_score  0  10  8.56 MB  grr_encode_local  ATAC-seq/ENCSR990NNX
...                                        (369 rows)
apsw.SQLError: no such column: assay_term_name
```

Child order alone decides whether the user sees 369 rows before the abort or
none. gain#632 turned the bare `apsw.SQLError` into a named `SearchTermError`,
which improved the message and changed nothing about the outcome.

A second failure has the same shape and the same cause — a child that cannot
answer a filter the group as a whole can. A child with no
`.CONTENTS.sqlite3.gz` raises as soon as `-s` or `-t` is supplied, and
`cli_list._search` calls that "the normal shape of a checked-out GRR", so it is
the common case rather than an exotic one.

## Decision

**A child that cannot answer a filter is skipped with a warning; the search
fails only when no child could answer.** One rule, covering both the missing
column and the missing index.

**Absorb a child's failure only before it has yielded its first row.** A
failure arriving mid-scan propagates, so a child's results can never be
silently truncated into a warning. This costs nothing, because both absorbed
failures fire ahead of `cursor.execute` — the index is opened and the term
probed before any row exists — and a nested group only raises when it yielded
nothing, so the property holds recursively. Stated as an invariant: **a
repository either refuses a search before its first row, or it answers it
completely.**

**When every child was skipped, classify by who was in a position to judge.**
If any child read its index and still rejected the term, the term is at fault
and that is proven, so the failure is a `SearchTermError` — a 400. If no child
could open an index at all, nobody could judge the term, so the failure is the
index-unavailable error — a 500. Either way the message names the group and
lists each child's reason.

**The rule lands once, as a per-child generator on the group** yielding
`(child, resource)` pairs. `search_resources` is a projection of it.

## Why this scope

*Why the existing probe is the oracle, and no new machinery is.*
`_reject_unparsable_search_term` already mirrors each index's own column names
into a throwaway FTS5 table and asks FTS5 itself whether the term parses. That
makes the classification fall out for free: a genuinely malformed term
(`"unclosed`) fails *every* child's probe, so every child is skipped and the
search raises; a column filter naming a label fails only the children lacking
that column, so the skip is partial and the search does not. No union of the
children's columns has to be computed, and no `apsw` error string has to be
parsed to tell "no such column" from a corrupt index. The message a user
finally sees is the child's own `no such column: assay_term_name`, which was
already the right words.

*Why the rule is not in `cli_list`, where the reported command lives.*
`grr_browse` never calls the group's search: `run_list_command` splits a group
into `proto.children` and searches each separately, because it needs the child
object per row — for the repo-id column and for the cached-file count on a
`GenomicResourceCachedRepo`. So a fix landed only in the group would not fix
the command that reported this, and one landed only in the CLI would leave
`views.py` answering 400 for a query the group can serve. A per-child generator
is what lets one definition serve both: the CLI consumes the pairs and keeps
its per-row child, and the plain resource stream is the projection.

*Why this is a generalization rather than a new posture.* One "cannot answer"
case already behaves exactly this way. A repository whose index has no
`contents` table — built with no resource in it, or one whose every resource
the build had to skip — logs a warning naming the repository and the repair,
and yields nothing (gain#464). A group already tolerates that child and moves
on. This decision extends the same treatment to the two cases that raise
instead, rather than inventing a tolerance the codebase did not have.

*Why not simply steer `-s` users to `-q`.* `-q '*[assay_term_name="ATAC-seq"]'`
returns the same 369 resources across the whole group with no error, so
rejecting the `-s` spelling with a message naming the `-q` one was a real
option and costs no code. It was rejected because the `-s` column filter is not
a mistake — it is the terser spelling, it works correctly against any single
GRR, and a user cannot be expected to know that a filter's portability depends
on which columns each child's index happens to publish.

*Why not make absence silent.* Matching ADR 0007's "an absent label reads as
empty" exactly would mean skipping a child with no warning. That reads well
until a label key is mistyped, at which point the search returns zero rows and
says nothing. The asymmetry is deliberate: 0007 could be silent because a
deferred clause is still *evaluated*, against every resource; here the child is
not evaluated at all.

*Why `[key="*"]` is left alone.* In a group with disjoint vocabularies, 0007's
tautology is sharper than 0007 anticipated — `-q '*[biosample_summary="*"]'`
returns 8194, every resource in the group, including all of `grr_local`, which
carries no such label. That is a real cost but a separate decision, and 0007
already records both the reasoning and the `[key="?*"]` escape hatch that would
buy the has-this-label test back.

## Consequences

- **A search can now return incomplete results, and a warning is the only
  signal.** This is the price of the rule and it is not small: a group whose
  every child is mid-repair answers with whatever the healthy children hold.
  The warning names the child and the repair command; nothing else marks the
  result as partial.
- **A stale index loses resources here in a way `-q` does not.** gain#634 could
  re-ask a deferred label clause of each resource because a `LabelClause` is
  `fnmatch` over a rendered value, which Python can reproduce exactly. A
  `search_term` has no such fallback — FTS5 tokenization is not reproducible in
  Python — so a child whose published index predates a label a curator added is
  skipped rather than deferred, and its matching resources are simply not
  returned. Rebuilding the index with `grr_manage repo-repair` is the only fix,
  which is what the warning says.
- **The no-index failure needs a typed exception.** It is currently a bare
  `ValueError` that `cli_list` recognises by string-matching `"SQLite metadata
  DB" not in str(err)`. Shared code cannot absorb it on that basis, so it gets
  a type — and the string match goes away with it.
- **`views.py` gains a second status code from one call.** The endpoint
  currently maps every `SearchTermError` to 400. Under this decision a group
  whose children have no index at all must answer 500 instead, because no
  caller input was wrong.
- **The invariant is load-bearing and should be tested, not commented.** The
  "absorb only before the first row" rule is safe only while both absorbed
  failures really do precede the first row. A regression test that drives a
  child which raises *after* its first row, and pins that the group propagates,
  is what keeps a later change from quietly widening the absorption.
