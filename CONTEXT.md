# GAIn

The annotation engine and genomic resource framework behind GPF. This file
records the language the project uses for its own domain — the terms a
maintainer must get right to read the code or write an issue about it.

It is grown a term at a time, as ambiguities are actually hit and resolved.
It is not a glossary of everything in `gain`, and it deliberately excludes
general programming concepts. Reasoning behind decisions lives in
[`docs/adr/`](docs/adr/README.md), not here.

## Language

### Repositories

**Genomic Resource Repository (GRR)**:
A store of versioned genomic resources, addressed by id and served over a
storage protocol (directory, HTTP, S3, in-memory).
_Avoid_: repo, database, GRR instance

**Group repository**:
A GRR whose contents are the union of its **children**, each itself a GRR.
_Avoid_: composite repo, aggregate repo, multi-GRR

**Child**:
One GRR directly under a **group repository**. A child may itself be a group,
so the structure is a tree.
_Avoid_: sub-repo, member, source

**Resource**:
One versioned, self-describing unit of a GRR — a score, a genome, a gene model
— configured by its `genomic_resource.yaml`.
_Avoid_: dataset, asset, entry

### Describing a resource

**Label**:
One free-form key/value pair under a resource's `meta.labels`, authored by
whoever curates the resource. Values are whatever YAML made of them, so a label
is compared in its rendered form.
_Avoid_: tag, attribute, annotation (which means variant annotation here), metadata

**Search index**:
The `.CONTENTS.sqlite3.gz` FTS5 artefact a GRR publishes alongside its
resources, built by `grr_manage`. It is a **separate artefact from the
resources** and nothing forces it to be current.
_Avoid_: contents db, FTS, catalogue

**Column vocabulary**:
The set of column names one GRR's **search index** publishes. It is
**per-repository**: one column per **label** key found at build time, plus the
fixed **non-label columns**. Two children of a group routinely have almost
disjoint vocabularies.
_Avoid_: schema, index fields

**Non-label column**:
An index column that names a field of the resource rather than a label —
`full_id`, `id`, `type`, `description`, `summary`, and an implementation's
contributions such as `score_ids`. Enumerated as
`GR_INDEX_NON_LABEL_COLUMNS`, and the only part of a
**column vocabulary** guaranteed present in every GRR.
_Avoid_: builtin column, system column

### Searching

**Search term**:
The `-s` filter, handed to FTS5 as a `MATCH` expression. Its grammar is FTS5's,
not gain's.
_Avoid_: search string, query (reserved for **resource query**), keyword

**Column filter**:
The FTS5 syntax `name: value` inside a **search term**, restricting the match to
one index column. Valid only against an index whose **column vocabulary** has
that column.
_Avoid_: field search, scoped term

**Resource query**:
The `-q` filter: gain's own wildcard language, an id glob plus optional **label
clauses**, parsed by `ResourceQuery`. Unlike a **search term**, it is
independent of any **column vocabulary**.
_Avoid_: wildcard query, selector, filter expression

**Label clause**:
One condition on one label inside a **resource query** — `key = value` or
`value in key`. Held as data rather than a closure, so whichever engine
evaluates it, `matches` is the only definition of the comparison.
_Avoid_: predicate, label filter

## Relationships

- A **group repository** has one or more **children**; each child is a **GRR**,
  possibly another group.
- A **GRR** holds many **resources**; a **resource** carries zero or more
  **labels**.
- A **GRR** publishes at most one **search index**, whose **column vocabulary**
  is one column per **label** key plus the **non-label columns**.
- A **search term** may contain **column filters**, which bind it to one
  **column vocabulary**. A **resource query** contains **label clauses**, which
  bind it to nothing.
- A **search term** and a **resource query** conjoin when both are supplied.

## Example dialogue

> **Dev:** "`-s 'assay_term_name: \"ATAC-seq\"'` works against encode but blows
> up on the group. Is the label missing from the other GRR?"
>
> **Maintainer:** "Careful — you're using a **column filter**, not a **label
> clause**. It reads the other child's **column vocabulary**, and that
> vocabulary has no `assay_term_name` column, so FTS5 rejects the whole
> expression as malformed. Nothing has been asked about labels yet."
>
> **Dev:** "So no resource over there carries the label?"
>
> **Maintainer:** "That doesn't follow either. A **search index** is a separate
> artefact from the resources — if it was published before a curator added the
> label, the resources serve it and the index has no column for it. 'No column'
> and 'no resource carries the key' are different claims."
>
> **Dev:** "And `-q '*[assay_term_name=\"ATAC-seq\"]'` returns 369 across the
> whole group."
>
> **Maintainer:** "Right, because a **label clause** is evaluated against
> `meta.labels`, and ADR 0007 made that independent of any **column
> vocabulary**. Same question, two spellings, and only one of them survives a
> group."

## Flagged ambiguities

- **"Searching by label" means two different mechanisms** — resolved: a **label
  clause** in a **resource query** (evaluated against `meta.labels`,
  vocabulary-independent) versus a **column filter** in a **search term**
  (evaluated against an index column that merely happens to be named after a
  label key, vocabulary-dependent). Only the first is portable across a
  **group repository**. See ADR 0007 and ADR 0012.

- **"The label is not present in this GRR" means three different things** —
  resolved, and they are not interchangeable:
  1. no resource in it carries the key;
  2. its **search index** has no column of that name;
  3. its index *predates* the label, so the resources serve it and the index
     does not.

  (2) does not imply (1) — that conflation was the gain#634 bug — and only (2)
  is what makes a **column filter** fail.

- **"The search failed"** — resolved: distinguish a filter *this GRR* cannot
  answer (a missing column, a missing index) from one *nobody* can answer (a
  malformed **search term**). Under ADR 0012 the first is a skipped **child**
  and the second is an error.
