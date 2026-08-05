# 12. A group repository skips a child that cannot answer a search, and fails only when none can

**Status:** proposed
**Date:** 2026-08-05
**Issues:** iossifovlab/gain#680; the label-vocabulary half settled by ADR 0007

## Context

A search carries two filters that can name a label, and only one of them is
independent of any single repository's index.

`resource_query` (`-q`) spells a label as a `LabelClause` evaluated against
`meta.labels`. ADR 0007 and its gain#634 amendment made that **independent of
any column vocabulary**: an absent label reads as `""`, and a clause the index
has no column for is deferred and re-asked of the resources the statement
yields.

`search_term` (`-s`) is handed to FTS5 as a `MATCH` expression, and FTS5's
expression grammar includes a **column filter** — `assay_term_name: "ATAC-seq"`
restricts the match to one column. `_create_contents_db` gives the index one
column per label key, so a label key doubles as a column-filter name. That is
what makes `-s` a label search at all, and nothing has ever made *that*
vocabulary portable. A column filter is a valid expression only against an
index that has the column.

The two facts collide in a group. Measured on the group of `grr_encode_local`
and `grr_local`: encode's index publishes 14 label columns (`accession`,
`assay_term_name`, `target`, `doi`, …), the local GRR's publishes 3
(`reference_genome`, `source_genome`, `target_genome`), and they overlap in
exactly one.

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

Two more failures have the same shape — a child that cannot answer a filter the
group as a whole can. A child with no `.CONTENTS.sqlite3.gz` raises as soon as
`-s` or `-t` is supplied, and `cli_list._search` calls that "the normal shape of
a checked-out GRR", so it is the common case rather than an exotic one. A child
whose index has no `contents` table — built with no resource in it, or one whose
every resource the build had to skip (gain#464) — instead warns and yields
nothing.

**This is about a filter, not only about a term.** `-t` alone routes through the
index too: `_search_resources` short-circuits to `get_all_resources()` only when
`search_term` and `resource_type` are *both* unset. So `grr_browse -t
position_score` against a group with one index-less child is the same bug.

## Decision

**A child that cannot answer a filter is skipped with a warning; the search
fails only when no child could answer.** One rule, covering the missing column,
the missing index, and the contents-less index.

**A contents-less child is a skip, not a zero-row answer.** Today it warns and
yields nothing, which is indistinguishable from "searched, matched nothing". A
repository in that state has not answered the filter, and saying so is what
lets the group classify the outcome at all — otherwise a group whose every
child is contents-less returns *zero rows and success* for a term that is
plainly malformed.

**Absorb a child's failure only before it has yielded its first row.** A
failure arriving mid-scan propagates, so a child's results can never be
silently truncated into a warning. This costs nothing, because each absorbed
failure is raised before its child's first row: the index is opened, the
contents table checked, and the term probed, all ahead of `cursor.execute`.
That is a property of **the failures this decision absorbs**, not of
repositories in general — plenty of other errors can and do arrive mid-scan,
and they must keep propagating.

**Absorb only the two typed failures, never a bare `ValueError`.** The cache
layer raises `ValueError` of its own after a child's first row, and catching
broadly would swallow it.

**When every child was skipped, repository health wins the classification.**
If any child was skipped because it could not be read — no index, or a
contents-less one — the failure is the index-unavailable error, because
repairing that repository could change the answer and the caller has been told
nothing wrong. Only when *every* child could read its index and every one of
them rejected the filter is the failure a `SearchTermError`. The message names
the group and lists each child with its own reason either way.

**The rule lands once, as a per-child generator on the group.**
`search_resources_by_child` yields `(holder, resource)` pairs, and
`search_resources` is its projection, so the tolerance has one definition and
the two cannot drift. Both stay non-generator functions, so a malformed
`resource_query` is still parsed when the call is made rather than on first
iteration.

Every repository answers `search_resources_by_child`; the default pairs each
hit with itself, and only the group overrides it, projecting its children's
pairs upward. A nested group therefore names the leaf that holds the resource
rather than itself, and `GenomicResourceCachedRepo` needs no override at all:
it names itself, which is both what a caller must hold to ask for a
cached-file count and the honest answer, since the resource it serves is the
cache twin rather than the remote. It still gets the tolerance, because its
`search_resources` delegates into the group it wraps.

## Why this scope

*Why the probe is the oracle, and no new machinery is.*
`_reject_unparsable_search_term` already mirrors each index's own column names
into a throwaway FTS5 table and asks FTS5 itself whether the term parses. That
makes the classification fall out for a child with a readable FTS5 `contents`
table: a genuinely malformed term (`"unclosed`) fails such a child's probe, and
a column filter naming a label fails only the children lacking that column. No
union of the children's columns has to be computed, and no `apsw` error string
has to be parsed. The message a user finally sees is the child's own `no such
column: assay_term_name`, which was already the right words.

*What the probe does not cover.* It is the oracle only for a child whose index
can be read and described. A child with no index never reaches it; a
contents-less child never reaches it (both are skips under this decision); and
a child whose `contents` is present but is **not an FTS5 table** — a foreign or
corrupted index — passes the probe and then fails at `cursor.execute` with a
raw `apsw.SQLError`. That last one is deliberately **not** absorbed. Blaming a
corrupt repository on the caller's search is the exact failure
`_reject_unparsable_search_term` was written to avoid, and a repository that
cannot be read should stay loud rather than become a warning under a
successful-looking search.

*Why health wins the classification.* The earlier draft of this decision said a
child that read its index and still rejected the term *proves* the term at
fault. It does not: a probe rejection proves only that the term is invalid
against **that child's column vocabulary**, which is the very thing this ADR
argues is not a property of the term. A group of one unindexed child and one
child whose index lacks the column would then answer 400 "your search is
malformed" for a valid term whose only real problem was an unbuilt index next
door.

*Why this is a generalization rather than a new posture.* The contents-less
child already warns and carries on instead of raising (gain#464), so a group
already tolerates one shape of "cannot answer". This decision keeps that
tolerance and makes it legible — the child is now *reported as skipped* rather
than silently indistinguishable from a child that matched nothing.

*Why the rule is not in `cli_list`, where the reported command lives.*
`run_list_command` splits a **bare** top-level `GenomicResourceGroupRepo` into
`proto.children` and searches each separately, because it needs the child
object per row — for the repo-id column and for the cached-file count on a
`GenomicResourceCachedRepo`. The reporter's own definition is that shape. But
the split is an `isinstance` check, so a group with a `cache_dir` builds
`GenomicResourceCachedRepo(GenomicResourceGroupRepo(...))`, which is *not* a
`GenomicResourceGroupRepo`: that one is not split, and goes through the group's
own search. Both shapes therefore have to work, which is why the rule lives in
the group rather than in the CLI. With the pairs available the split goes away
entirely — `run_list_command` asks the repository it was handed, whatever shape
it is, and gets the holder alongside every row.

*Why not simply steer `-s` users to `-q`.* Rejecting the `-s` spelling with a
message naming the `-q` one was a real option and costs no code. It was
rejected because the `-s` column filter is not a mistake — it is the terser
spelling, it works correctly against any single GRR, and a user cannot be
expected to know that a filter's portability depends on which columns each
child's index happens to publish. Note the two are **not** equivalent
spellings: `-s` is a phrase match over a tokenized column, `-q` is
equality-or-`fnmatch` over the whole rendered value, so a label
`ATAC-seq (paired)` matches the first and not the second. They happened to
return the same 369 resources on the measured group.

*Why not make absence silent.* Matching ADR 0007's "an absent label reads as
empty" exactly would mean skipping a child with no warning. That reads well
until a label key is mistyped, at which point the search returns zero rows and
says nothing. The asymmetry is deliberate: 0007 could be silent because a
deferred clause is still evaluated against each resource the statement yields;
here the child is not consulted at all.

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
- **The web API reports a truncated total as though it were complete.**
  `SearchResources` drains the search into a list and computes `pages` and
  `total_resources` from it, so a skipped child shortens the page while the
  payload still presents an authoritative count, and the warning goes only to
  the server log. This decision accepts that for now rather than changing the
  response schema the web UI consumes; carrying the skips in the payload is
  gain#686. The CLI has no such gap — its warning lands in the terminal beside
  the rows.
- **A stale index loses resources here in a way `-q` does not.** gain#634 could
  re-ask a deferred label clause of each resource the statement yields, because
  a `LabelClause` is `fnmatch` over a rendered value, which Python can
  reproduce exactly. A `search_term` has no such fallback — FTS5 tokenization
  is not reproducible in Python — so a child whose published index predates a
  label a curator added is skipped rather than deferred. Rebuilding the index
  with `grr_manage repo-repair` is the only fix, which is what the warning says.
- **The no-index failure needs a typed exception.** It is currently a bare
  `ValueError` that `cli_list` recognises by string-matching `"SQLite metadata
  DB" not in str(err)`. Shared code cannot absorb it on that basis, and the
  cache layer raises bare `ValueError`s of its own that must not be caught. The
  type is what separates them; `cli_list`'s arm goes away, because the group
  now produces the message that arm was formatting.
- **`views.py` gets a *handled* 500, not a new status code.** The bare no-index
  `ValueError` is not in that endpoint's `except` today, so it already escapes
  as an unhandled exception. What changes is that the caller gets a message
  naming the repositories and the repair instead of a traceback.
- **Only five index columns are portable, and even those are conditional.**
  `GR_INDEX_NON_LABEL_COLUMNS` is `full_id`, `id`, `type`, `description`,
  `summary`, `score_ids` and `score_descriptions`, but an index's columns are
  the union of what its resources contributed: the score fields exist only in a
  repository holding a score resource, and a repository whose every resource
  failed to index has no `contents` table at all. So `-s 'score_ids: foo'` is
  exactly as unportable as a label filter, and `-s 'summary: foo'` is portable
  only across children that indexed at least one resource.
- **The absorption boundary is load-bearing and must be tested, not
  commented.** Restricting absorption to "before the first row" is safe only
  while the absorbed failures really do precede the first row. A regression
  test that drives a child which raises *after* its first row, and pins that
  the group propagates, is what keeps a later change from quietly widening it.
