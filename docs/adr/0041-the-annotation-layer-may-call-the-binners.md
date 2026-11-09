# 41. The annotation layer may call the binners

- **Status:** accepted
- **Date:** 2026-11-09
- **Issues:** [gain#2208](https://github.com/iossifovlab/gain/issues/2208) (the
  plan that this decision replaces),
  [gain#2116](https://github.com/iossifovlab/gain/issues/2116) (the public API of
  `gain.binning`)

## Context

A pipeline author wants the values of a `fragment_score_binner` entry for a
list of regions. An example is the number of fragments of each cell type in
each CNV. `binning_tool` computes these values only for the bins of a global
grid.

The plan of gain#2208 added a grouping and the fragment modes to the
`fragment_score` annotator. That plan had three costs:

- a second implementation of the fragment modes, in the region read of
  `FragmentScore`
- a move of the grouping code and the mode rules from `gain.binning` to
  `gain.genomic_resources` (gain#2209)
- tests that compare the two implementations, because the two
  implementations can drift apart.

The plan also added a fence: `gain.annotation` does not import `gain.binning`.

## Decision

A new annotator, `binning_tracks_annotator`, takes a `binners:` list with the
spelling of a run definition. The annotator resolves the list with the code of
`binning_tool` and binds each job. For each annotatable, the annotator calls
`bin_region` with a bin size equal to the end of the annotatable.

Thus `gain.annotation` imports `gain.binning`. The other direction stays
forbidden: `test_binning_does_not_import_the_annotation_layer` in
`core/tests/test_architecture.py` still applies.

## Why

The fragment binner finds the bin of a position with `calc_bin_index`, and it
clips each fold to the start and the end of the region. With a bin size equal
to the end of the annotatable, the block of `bin_region` has one row. That row
covers exactly the annotatable, and it has one column for each track.

So the annotator and `binning_tool` have one engine. A value of the annotator
equals the value of `binning_tool` for the same window, without a test that
compares them.

We rejected two alternatives:

- The region read of gain#2208. It needs the second implementation and the
  comparison tests.
- A move of the entry parsers, the binders and the folds to
  `gain.genomic_resources`, so that both layers import them from below. The
  move is large, and no other reader of that code exists.

The scope is one direction of import. The `fragment_score` annotator does not
change. It keeps its rule that each overlapping fragment counts once, and it
gets no grouping and no fragment modes.

## Consequences

- A change to the entry keys or to the folds of a binner also changes the
  output of `binning_tracks_annotator`.
- An attribute of the annotator is named by the identity of its track:
  `<prefix>_<binner_index>_<track_index>`. Each name is a valid VCF key, so the
  `_XX` escape of gain#2208 is not necessary.
- The annotator takes the three fragment modes of the binner, with the default
  of the binner. The `fragment_overlap` mode of gain#2208 does not exist.
- The function that resolves a `binners:` list, `_resolve_jobs` in
  `gain/binning/run_definition.py`, is private. The annotator needs a name
  for it that the annotation layer can import.
- The amendments of ADR 0035 and ADR 0037 that gain#2208 planned are not
  necessary, because the binning code stays in `gain.binning`.
