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
contributions such as `score_ids`. Enumerated as `GR_INDEX_NON_LABEL_COLUMNS`.
These are the *closest* thing to a portable **column vocabulary**, but none is
unconditional: an index's columns are the union of what its resources
contributed, so the score fields exist only where the GRR holds a score
resource, and a GRR whose every resource failed to index has no `contents`
table at all.
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

### Tabular score statistics

**Segment**:
A maximal run of touching-or-overlapping table rows of a tabular score
carrying **equal values** — the row's *scanned* score-column tuple compares
equal, NA equal to NA, floats exactly as stored (ADR 0020). Scanned means
the columns the statistics scan fetches: every declared score except those
with a null histogram. Value-aware and per-resource: a touching row that
differs in any scanned score column starts a new segment, and a run of NA
rows is a segment. Not a table row as stored — an unmerged row is a
*fragment*, deliberately the opposite view.
Defined for **position scores only**: a kind whose rows can overlap has no
segments, by decision rather than by deferral (ADR 0020, amended by
gain#926), and builds no runs at all.
_Avoid_: interval, region (reserved for query regions), run, block, same-value
region

**Covered position**:
A position spanned by at least one table row — **value-blind**, union
semantics, computed independently of **segments**. Σ segment lengths can
exceed the covered count wherever different-valued rows overlap, so neither
statistic may be derived from the other (ADR 0020).
What a row *spans* is the kind's own answer, not the columns': a **position
score** and a **fragment score** span the row's interval and their covered
count is a union of those, whereas an **allele score** row is the point it
sits at, so its covered count is the number of **distinct positions** its
rows sit on and an optional `pos_end` reaches over nothing. Both are the
same term — a position with data — counted the way the kind means a row.
_Avoid_: covered base, coverage depth (nothing here counts how many rows
span a position), breadth

**Fragment**:
A table row of a tabular score **as stored** — unmerged and value-blind, the
deliberate opposite of a **segment**. Overlapping, nested and duplicate rows
are each their own fragment, and a fragment's length is its own span,
`pos_end - pos_begin + 1`. Only a fragment score publishes fragment
statistics — a count and a length histogram, on the same fixed log2 bins as
segment lengths. Nothing publishes fragment *segments*, and nothing will:
they would need an exact run algebra fragments do not have, and no consumer
question survives that fragment coverage and the fragment count/length
histogram cannot already answer between them (ADR 0020, amended by gain#848
and closed by gain#926).
_Avoid_: row (ambiguous — every kind has table rows), interval, CNV, call,
event

**Allele class**:
What an allele-score row's ref/alt pair is, classified VCF-anchored as one of
exactly five values — **substitution**, **insertion**, **deletion**,
**complex**, **other** (ADR 0020). Every pair classifies, so the five counts
sum to the row count. Alleles are upper-cased first; anything then outside
`ACGT` is **other**.
_Avoid_: variant type, allele type (the annotation layer's `Annotatable.Type`
is a different vocabulary, with `SMALL_INSERTION`/`SMALL_DELETION` spellings
and no **other**), effect

**Substitution**:
An allele class: strictly one reference base to one alternative base, the
identity pair included. Never a multi-base event — that is what keeps the 4×4
ref→alt matrix exact. Not to be confused with an allele score's
`substitutions` **mode** (`allele_score_mode`), which selects how a whole
resource is *read* — a property of the score, not a classification of any
one of its rows. It is opt-in: an `allele_score` reads in `alleles` mode
unless it says otherwise.
_Avoid_: SNV, SNP (both read as "one variant" rather than one class of row),
MNV (an MNV is **complex**)

**Insertion** / **Deletion**:
The two anchored allele classes: a single reference base the alternative
starts with, or its mirror image. Anchoring is strict — `AT→ATG` lengthens
but is **complex**, because its reference is not a single base. The length
is how many bases the alternative adds over the reference, *signed*:
positive for an insertion, negative for a deletion.
_Avoid_: indel (names the pair, not either class), small insertion, small
deletion (nothing here is classified by size)

**Complex**:
The allele class for everything the anchored rules do not match — MNVs and
unanchored indels alike — carrying both alleles' lengths rather than one
length. Those two lengths are published as a **complex grid**: a sparse
`(len_ref, len_alt) → count` map whose cells are *exact* lengths, each clamped
at 64, so a cell on the diagonal is an MNV of exactly that many bases (ADR
0020 as amended by gain#779). Deliberately not the fixed log2 bins the
**segment**, **fragment** and ins/del length histograms share — those would
merge a 2→3 complex into the same cell as a 3bp MNV.
_Avoid_: other (a different class), MNV, substitution of length n, complex
histogram (it is a two-dimensional map, not a binned histogram)

**Other**:
The allele class for a ref/alt pair that does not parse as alleles: `N`, a
symbolic allele, `*`, an empty string, or a row whose table configures no
ref or alt column at all. Counted rather than dropped.
_Avoid_: unknown, invalid, unclassified, error (a row here is well-formed
data the classification declines to interpret, not a fault)

### ann_data resources

**Data matrix**:
The `X` of an **ann_data resource** — the values, and effectively all of its
bytes. A **matrix-free read** never materialises it.
_Avoid_: the matrix, expression matrix (it is not always expression), counts

**Axis table**:
One of an ann_data resource's two per-axis tables — `obs`, one row per
observation (cell), and `var`, one row per variable (feature). These are what
the `describe_obs` / `describe_var` statistics summarise.
_Avoid_: annotation (reserved for variant annotation), metadata, cell table

**Feature type**:
The value of an ann_data resource's `var["feature_types"]` — `Gene Expression`,
`Peaks`, `Antibody Capture`, … A resource may carry several. Decides what a
gene-expression-only read keeps.
_Avoid_: modality, assay (which is a **label** key here)

**Matrix-free read**:
A read that materialises a resource's **axis tables** and its shape, but never
its **data matrix**. What the statistics build uses; the reason its cost is the
sidecars rather than the matrix.
_Avoid_: lazy read (nothing is deferred — the matrix is never read), backed read
(that is h5ad's on-disk `X`, a different thing)

**Sidecar** (of a 10x resource):
One of the two non-matrix members of the matrix-market **triple** — the
barcodes and the feature table. The config names only the matrix, so the
sidecars are *resolved*, and they are statistics inputs: editing the barcodes
has to invalidate the build the way editing the matrix does.

**Triple layout**:
Which names a 10x **triple**'s three members carry. **Two independent axes,
and conflating them is what made an uncompressed CellRanger v3 resource
unreadable:**
- *generation* — v2 calls the feature table `genes.tsv` and it has no
  **feature type** column; v3 calls it `features.tsv` and it does. Decided by
  probing for `genes.tsv`, which is what scanpy itself does.
- *compression* — whether the members are gzipped. CellRanger v3 gzips them;
  STARsolo writes the same v3 layout in plain text.

A resource states its own layout, so it is resolved from the manifest and is
never a config key.
_Avoid_: "the v3 layout" for the compressed one specifically — that is a v3
layout that happens to be gzipped

**Feature-barcode matrix** / **probe-barcode matrix**:
The two kinds of 10x HDF5 (`10x_h5`) file, told apart by whether the features
carry a `gene_id` dataset. A feature-barcode file's **features** are measured
directly; a probe-barcode file's are probes, each *targeting* a gene, so the
gene id and the probe id are two different things and a read reports both.
**Every `10x_h5` resource we have is feature-barcode**, and gain's own reader
refuses the other kind rather than reading it (ADR 0014).
_Avoid_: calling either "the 10x h5 format" — the distinction is the whole
reason a read of one is not a read of the other

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
- A **segment** merges touching-or-overlapping *equal-valued* rows of one
  **resource**'s table; a **covered position** is spanned by *any* row, values
  ignored. Segments are value-aware, coverage is not, and the two coincide
  only where no different-valued rows overlap. An **allele score** has
  **covered positions** but no **segments**: its rows are points, so there is
  nothing to merge and nothing to union — the count is of distinct positions.
  A **fragment score** has **covered positions** and **fragments** but no
  **segments** either: its rows overlap, and merging them is not wanted.
- The **regions** a contig is scanned in own the rows whose `pos_begin` falls
  inside them, and measure those rows whole. So the regions partition the
  contig's **fragments**, and every statistic that sums over rows is
  independent of how the contig was split. **Covered positions** are the one
  exception: a union is additive only across disjoint extents, so coverage
  partitions *positions* — by clipping — wherever rows can overlap.
- Every allele-score row has exactly one **allele class**, so the counts of
  **substitution**, **insertion**, **deletion**, **complex** and **other** sum
  to the row count. **Segments** and **covered positions** describe *where* a
  score holds data; allele classes describe *what* its rows are.
- An **ann_data resource** is a **resource** whose content is one **data matrix**
  and the two **axis tables** describing its axes; every row of `var` carries a
  **feature type**, and one resource may hold several.
- A **matrix-free read** yields the **axis tables** and the shape and nothing
  else. It is the only read the statistics build needs.

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

- **"Annotation" means two unrelated things once an ann_data resource is
  involved** — resolved: variant annotation (the pipeline, the annotators)
  versus an ann_data **axis table**. AnnData's own vocabulary calls `obs` and
  `var` annotations, which is where the collision comes from; in this repo only
  the first is *annotation*, and the second is an **axis table**. The `ann_data`
  implementation predates this entry and still says "annotation table" in
  places.
