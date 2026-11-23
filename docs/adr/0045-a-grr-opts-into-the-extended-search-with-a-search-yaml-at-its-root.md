# 45. A GRR opts into the extended search with a search.yaml at its root

- **Status:** accepted
- **Date:** 2026-11-23
- **Issues:** [gain#2501](https://github.com/iossifovlab/gain/issues/2501) (the
  `search.yaml` config and the dialog), with
  [gain#2502](https://github.com/iossifovlab/gain/issues/2502) to
  [gain#2505](https://github.com/iossifovlab/gain/issues/2505)

## Context

The root `index.html` of a GRR gets an **extended search**. It is a dialog in
which a reader picks values of **facets**, as in the ENCODE experiment search.
A facet is a **label** key. Its values and counts come from the
`resource_labels` table of the search index (ADR 0043, PR #2487).

Not every label key makes a usable facet. The NYGC GRR has 2,866 resources and
34 label keys. About 15 keys have few values, for example `assay` (7),
`species` (8) and `brain_region` (4). Other keys have nearly one value per
resource: `sample_id` has 623 values on 757 resources, `library` 518 on 518 and
`run_id` 40 on 40. A facet on such a key is a list of hundreds of entries.

Before this decision, a GRR carried no configuration of its own. The only input
at the repository root that `grr_manage` read was `about.md`.

## Decision

A GRR names its facets in a file `search.yaml` at its root:

```yaml
facets:
  - label: assay
    title: Assay
  - label: species
  - label: brain_region
    title: Brain region
```

- The list is flat. Facets appear in file order, and `title` is an optional
  display name.
- `grr_manage` reads the file when it renders the root page, as it reads
  `about.md`, and writes the facet list into the page. The page does not fetch
  the file.
- A key that no resource carries is reported as a warning and skipped. A file
  that is not valid YAML stops `grr_manage` with a message that names the file.
- **A GRR without `search.yaml` has no extended search.** Its root page shows no
  control for it and stays as it was.

## Why this shape

- **No file means no extended search, not every label.** The first draft of the
  design made every label key a facet when the file was missing. On NYGC that
  gives 34 facets, several of them with hundreds of values. The curator would
  have to write the file anyway to get a usable dialog. Making the extended
  search opt-in keeps an unconfigured GRR exactly as it was.
- **A separate file, not front matter in `about.md`.** Front matter needs no new
  file, but it mixes search settings into the text of the About page. A curator
  who edits the About text can then break the search.
- **A flat list, not groups.** ENCODE groups its facets under headings such as
  "Assay" and "Biosample". No GRR has asked for groups, and a `group` key can be
  added later without breaking a file written to the flat shape.
- **Read at render time, not stored in the search index.** The index build
  keeps an index whose `.CONTENTS` has not changed (ADR 0043). The root page is
  rendered on every run, so a change to `search.yaml` reaches the page at the
  next run.

## Consequences

- `search.yaml` is the first configuration file of a GRR. Its format is now a
  contract with curators: a new key is cheap, but a renamed or moved key breaks
  their GRRs.
- A curator must write the file to give a GRR an extended search. The NYGC GRR
  gets one in a separate step, because someone must choose its facets.
- `grr_browse` does not read `search.yaml`. A command-line filter with the same
  rules is a separate follow-up.
