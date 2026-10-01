# 33. `gain.grr` is the public import path for external users

- **Status:** accepted
- **Date:** 2026-10-01
- **Issues:** [gain#1765](https://github.com/iossifovlab/gain/issues/1765);
  [grr_nygc_single_cell_demo#4](https://github.com/iossifovlab/grr_nygc_single_cell_demo/issues/4)
  (the first external switch, after a release)

## Context

A script that reads a GRR had to know where each factory is defined: the
repository in `gain.genomic_resources.repository_factory`, gene models in
`gain.genomic_resources.gene_models`, scores in
`gain.genomic_resources.genomic_scores`, gene scores in
`gain.gene_scores.gene_scores`, and so on. `gain/__init__.py` exports
nothing. Two package facades already existed one level down
(`genomic_scores`, `gene_models`), and the `genomic_scores` one is pinned by
`test_genomic_scores_facade.py`. Notebooks, demo scripts and SFARI tooling
each spelled out half a dozen deep paths.

## Decision

1. **Audience: external users only.** `gain.grr` is for code outside `gain`
   and `gpf`. Internal code keeps importing from the defining modules. An
   architecture rule enforces this (7).
2. **Scope: the GRR constructors and the resource builders.** Excluded on
   purpose ("Tier 3"):
   - `build_*_from_file` -- these bypass the GRR;
   - `build_genomic_position_table` -- takes a raw config dict and returns an
     internal table;
   - `build_resource_implementation` and the entry-point registry -- plugin
     plumbing;
   - `load_pipeline_*` / `build_annotation_pipeline` -- annotation is a
     separate concern; leaving it out keeps the *static API surface* of the
     GRR free of annotation, matching the existing `genomic_resources` →
     `annotation` fence;
   - `GenomicContext`, implementation classes, pandas and anndata types.
3. **Pure re-export.** Every name is exactly the defining module's name and
   object. No aliases; no renaming. The `load_` / `build_` inconsistency of
   the data_frame and ann_data loaders is the source modules' business.
4. **An explicit, pinned list.** `__all__` is written by hand.
   `tests/small/grr/test_grr_facade.py` asserts it is exactly the expected
   set and that each name `is` the object in its defining module. A diff to
   that test is a public-API change and is reviewed as one.
5. **A package, with the signature types.** `gain/grr/__init__.py` also
   exports the types that appear in the exported factories' signatures
   (`GenomicResourceRepo`, `GenomicResource`, `ReferenceGenome`,
   `GeneModels`, `LiftoverChain`, `GenomicScore`, `PositionScore`,
   `AlleleScore`, `FragmentScore`, `GeneScore`, `GeneSetCollection`), so a
   caller can annotate without going back to the deep paths. For
   `gene_models` and `genomic_scores` the names come from the package
   facade, the path users already know.
6. **Eager imports.** No `__getattr__` laziness. Measured warm on
   2026-10-01: bare python 0.04 s; the reference genome module alone
   0.53 s; every facade module except anndata 1.00 s; all of them 1.75 s.
   `import gain.grr` from the finished package measured 1.38--1.47 s warm
   (2174 modules in `sys.modules`). That is below the point where lazy
   loading machinery pays for itself. Revisit if anndata gets materially
   heavier.
7. **Two architecture rules** in `core/tests/test_architecture.py`, both on
   the AST import graph through `_imported_modules`:
   - inbound: no module under `gain` imports `gain.grr`, except `gain.grr`
     itself (its tests live outside the package);
   - outbound: `gain.grr` imports only the modules it re-exports from
     (`GRR_FACADE_SOURCES`).
8. **Docs.** `python_interface.rst` and `development/resources_in_python.rst`
   teach `gain.grr`. The annotation example in `python_interface.rst` stays
   on `gain.annotation.*`. The ADRs and `development/resources/*.rst`
   document internals and stay on the defining modules.
9. **Stability.** The names in `gain.grr.__all__` are the supported public
   surface. Removing one takes a one-release deprecation window with a
   warning, the same posture as the `cnv_collection` retirement (ADR 0011).
   The deep paths are not demoted and stay supported.

## Static surface, not runtime load

Decision 2 keeps annotation out of what `gain.grr` *exports*. It does not
keep annotation out of what `import gain.grr` *loads*. `get_genomic_context`
is exported, and `genomic_context` loads every
`gain.genomic_resources.plugins` entry point at module scope, including the
annotation context provider. Importing `genomic_context` alone loads
`gain.annotation`, `annotation_config`, `annotation_factory` and the rest
(about 1380 modules, about 0.9 s warm). The finished facade has
`gain.annotation` in `sys.modules` after import.

That is why the outbound rule reads the AST rather than `sys.modules`: a
runtime check would fail on the plugin load, which this decision does not
change. Making `genomic_context` load its providers lazily is a separate
change, if it is ever wanted.

## Consequences

- External code has one import path, and every name on it is pinned by
  test. Adding a name is a reviewed change to three places: `__all__`, the
  pinning test, and -- if the name comes from a new module --
  `GRR_FACADE_SOURCES`.
- Two spellings of every exported name are supported indefinitely. Docs that
  describe internals keep naming the defining module, so a reader can meet
  both.
- `import gain.grr` costs about 1.4 s warm, mostly anndata and the plugin
  load above. A script that needs only the repository can still import
  `repository_factory` directly.
- Nothing inside `gain` can use the facade, so it cannot become the reason
  for an import cycle.
