Architecture overview
=====================

This page is the map. It has three parts: a static picture of GAIn's object
model and the seams between its pieces, a short trace of what one command —
``annotate_tabular`` — actually does at runtime, and the vocabulary the rest
of this section uses. Every later chapter is a zoom on one box of the map, so
if you arrived here from a search result, read the map first and the chapter
you came for second.

The front page of this documentation has an "Overview of GAIn" figure. That
figure is the *user's* view — resources go in, an annotated table comes out,
these are the tools you run. The map below is the *extender's* view of the
same system: the Python objects behind that figure and where you plug into
them.

.. attention::

   **Draft marker D3 — this diagram is an opinion and needs your sign-off.**
   It claims that GAIn has two paths (resources, annotation), one substrate
   under the first (the protocol layer), one cross-cutting mechanism (the
   genomic context), and exactly three seams a plugin can use (the entry-point
   groups). Anything that is not one of those boxes — the task graph, the
   effect-annotation engine, the statistics machinery, the web API — is left
   off deliberately, because the parent issue's *calls-its-methods* rule puts
   them outside the declared API. If any of that is wrong, it is wrong here
   first and in every later chapter second. Also decide whether the front-page
   figure should point down to this map, or stay unrelated.

The static map
--------------

.. mermaid::

   flowchart TB
       subgraph resources["Resource path"]
           direction TB
           Repo["GenomicResourceRepo<br/>get_resource · find_resource · search_resources"]
           Res["GenomicResource<br/>get_config · get_type · open_raw_file · open_tabix_file · …"]
           Impl["GenomicResourceImplementation<br/>ReferenceGenome · GeneModels · PositionScore · AlleleScore · …"]
           Repo -->|"one id, one version"| Res
           Res -->|"build_resource_implementation<br/>keyed by the resource's type"| Impl
       end

       subgraph protocol["Protocol layer"]
           direction TB
           Proto["ReadOnlyRepositoryProtocol<br/>ReadWriteRepositoryProtocol"]
           Fs["fsspec filesystems<br/>file · http(s) · s3 · memory"]
           Proto --> Fs
       end
       Repo -. "GenomicResourceProtocolRepo wraps one protocol;<br/>a group repository is a tree of child repositories" .-> Proto

       subgraph annotation["Annotation path"]
           direction TB
           Pipe["AnnotationPipeline<br/>annotate · batch_annotate · open · close"]
           Ann["Annotator (ABC) → AnnotatorBase<br/>annotate(annotatable, context) → dict"]
           Atbl["Annotatable<br/>Position · Region · VCFAllele · CNVAllele"]
           Pipe -->|"ordered list; the context dict<br/>flows from one annotator to the next"| Ann
           Ann -->|"input"| Atbl
       end
       Pipe -->|"repository"| Repo
       Ann -->|"the resources it reads"| Impl

       subgraph ctx["Genomic context"]
           direction TB
           Prov["GenomicContextProvider (ABC)<br/>default GRR · CLI GRR/genome/gene models · CLI pipeline"]
           Ctx["GenomicContext<br/>reference_genome · gene_models · GRR · annotation_pipeline"]
           Prov -->|"init(**args)"| Ctx
       end
       Ctx -. "fallback when a pipeline does not<br/>name a genome or gene models" .-> Ann

       subgraph ep["Entry-point groups — what a plugin package adds"]
           direction LR
           EP1["gain.genomic_resources.implementations"]
           EP2["gain.annotation.annotators"]
           EP3["gain.genomic_resources.plugins"]
       end
       EP1 -.->|"register_implementation<br/>(lazy: on first lookup)"| Impl
       EP2 -.->|"register_annotator_factory<br/>(lazy: on first lookup)"| Ann
       EP3 -.->|"register_context_provider<br/>(eager: at import time)"| Prov

Reading the map
~~~~~~~~~~~~~~~

**The resource path** is how GAIn gets at data. A
:class:`~gain.genomic_resources.repository.GenomicResourceRepo` is a store of
versioned resources addressed by id; ``get_resource`` returns one
:class:`~gain.genomic_resources.repository.GenomicResource`, which knows its
``genomic_resource.yaml`` (``get_config``), its ``type`` and its files, and can
open any of them (``open_raw_file``, ``open_tabix_file``, ``open_bigwig_file``,
…) without the caller knowing where the bytes live. A resource does not know
what its data *means*; that is the job of its
:class:`~gain.genomic_resources.resource_implementation.GenomicResourceImplementation`
— ``ReferenceGenome``, ``GeneModels``, ``PositionScore``, ``AlleleScore`` and
the rest. ``build_resource_implementation(resource)`` picks the implementation
class from the resource's ``type:`` field through a registry, and the
``build_*_from_resource_id`` helpers you will already have met in
:doc:`../python_interface` are one-call shortcuts through that same chain.

**The protocol layer** is why a repository does not care whether it is a
directory on disk, an HTTP mirror, an S3 bucket or an in-memory fixture.
:class:`~gain.genomic_resources.repository.GenomicResourceProtocolRepo` wraps
one :class:`~gain.genomic_resources.repository.ReadOnlyRepositoryProtocol`
(or its read-write subclass), and the concrete protocols are built on
``fsspec`` filesystems. A *group repository* is a repository whose contents are
the union of its children — each itself a repository, possibly a group — so a
GRR definition is a tree, and a search walks it.

**The annotation path** is how GAIn computes things. An
:class:`~gain.annotation.annotation_pipeline.AnnotationPipeline` holds an
ordered list of :class:`~gain.annotation.annotation_pipeline.Annotator`
objects and a reference to the repository they draw resources from. Its
``annotate(annotatable)`` runs each annotator in turn; every annotator's
``annotate(annotatable, context)`` receives the *context* dictionary of
everything the annotators before it produced and returns its own attributes,
which is how one annotator can consume another's output (the gene list an
effect annotator emits, say). Annotators are found by the ``annotator_type``
named in pipeline YAML, again through a registry.
:class:`~gain.annotation.annotator_base.AnnotatorBase` is the convenience base
a plugin author subclasses.

An :class:`~gain.annotation.annotatable.Annotatable` is the thing being
annotated: a :class:`~gain.annotation.annotatable.Position`, a
:class:`~gain.annotation.annotatable.Region`, a
:class:`~gain.annotation.annotatable.VCFAllele` or a
:class:`~gain.annotation.annotatable.CNVAllele`. Every input format GAIn
reads — tabular columns, VCF records, Python objects — is turned into one of
these four before any annotator sees it.

**The genomic context** is the cross-cutting piece, and the one this page
exists to make visible. A
:class:`~gain.genomic_resources.genomic_context_base.GenomicContext` answers
four questions — which reference genome, which gene models, which repository,
which pipeline — for code that was not handed those objects explicitly. It is
assembled from
:class:`~gain.genomic_resources.genomic_context_base.GenomicContextProvider`
objects, each of which knows one way to find an answer: the default GRR
definition (``$GRR_DEFINITION_FILE`` or ``~/.grr_definition.yaml``), the
``annotate_*`` command-line flags, the pipeline given on the command line. An
annotator whose configuration does not name a genome or gene models falls back
to the context, which is what lets a pipeline YAML omit them. How and when the
context comes into existence is the subject of the runtime trace below.

The three entry-point groups
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

These are the seams. A plugin is an installed Python package that declares
one or more entry points in these groups; GAIn's own ``core/pyproject.toml``
registers the built-in implementations, annotators and context providers in
exactly the same way, so there is no privileged path.

.. list-table::
   :header-rows: 1
   :widths: 28 30 42

   * - Group
     - What you register
     - When GAIn looks
   * - ``gain.genomic_resources.implementations``
     - A builder ``GenomicResource → GenomicResourceImplementation``. The
       entry-point *name* is the resource ``type:`` it handles.
     - The group is scanned when ``gain.genomic_resources`` is imported, but
       each entry point is loaded lazily, on the first
       ``build_resource_implementation`` of that type. Programmatic
       alternative: ``register_implementation``.
   * - ``gain.annotation.annotators``
     - A factory ``(AnnotationPipeline, AnnotatorInfo) → Annotator``. The
       entry-point *name* is the ``annotator_type`` used in pipeline YAML.
     - Lazily, on the first ``get_annotator_factory`` call — that is, the
       first time any pipeline is built in the process. A second registration
       of the same name logs a warning and wins. Programmatic alternative:
       ``register_annotator_factory``.
   * - ``gain.genomic_resources.plugins``
     - A :class:`~gain.genomic_resources.genomic_context_base.GenomicContextProvider`
       instance.
     - Eagerly, when ``gain.genomic_resources.genomic_context`` is imported —
       but registering a *provider* is not the same as having a *context*;
       providers are only asked to produce one when ``context_providers_init``
       runs (see the trace). Programmatic alternative:
       ``register_context_provider``.

.. attention::

   **Draft marker D4 — provider "priority" means initialisation order, and
   the last one wins.** ``context_providers_init`` sorts providers by
   *descending* priority (default GRR 10 000, CLI GRR 900, CLI pipeline 800)
   and asks each for a context in that order; every context is then inserted
   at the *front* of the registered list, and the merged
   ``PriorityGenomicContext`` takes the first answer. So the highest-priority
   provider is initialised first and shadowed by every provider after it —
   the command-line pipeline beats the command-line GRR beats the default.
   That is the behaviour the code has; confirm it is the *intended* contract
   before this page states it as one, because an extender writing a provider
   will read "priority" the other way round.

A short runtime trace: ``annotate_tabular``
--------------------------------------------

``annotate_tabular`` is the tool most people meet first, and it exercises
every box on the map. Its ``cli()`` does the following, in order. The point of
this trace is step 2 — genomic context resolution exists, and this is when it
happens.

1. **Parse arguments.** The parser is built by
   ``add_common_annotation_arguments``, which ends by calling
   ``context_providers_add_argparser_arguments``: every registered context
   provider contributes its own flags. The default-GRR provider adds none; the
   CLI GRR provider adds the repository, reference-genome and gene-models
   options; the CLI pipeline provider adds the pipeline argument. The tool's
   command-line surface is therefore partly assembled by plugins.

2. **Resolve the genomic context.** ``build_cli_genomic_context(args)`` is two
   calls: ``context_providers_init(**args)`` asks every registered provider
   for a :class:`~gain.genomic_resources.genomic_context_base.GenomicContext`
   (a provider that cannot build one returns ``None`` and is skipped), then
   ``get_genomic_context()`` returns the merged view. This is **process-global
   and one-shot**: ``context_providers_init`` is a no-op on every call after
   the first, until ``clear_registered_contexts()``.

3. **Take the pipeline and the repository out of the context.**
   ``get_pipeline_from_context`` and ``get_grr_from_context``. The pipeline
   was already constructed inside the CLI pipeline provider: the raw YAML
   (from a file or from a resource in the GRR) goes through
   ``build_annotation_pipeline(config, grr)``, which parses it, and for each
   entry looks up ``get_annotator_factory(annotator_type)`` — the first such
   lookup is what loads the ``gain.annotation.annotators`` group — calls the
   factory, wraps the result in the input-transform and value-transform
   decorators, and appends it to the pipeline. Any annotator that needs a
   genome or gene models it was not given asks the context *now*, at
   construction, and fails here if the context has none.

4. **Make the resources local.** ``check_resource_locality`` refuses to run a
   pipeline over remote resources against a large input unless told to, and
   ``cache_pipeline_resources(grr, pipeline)`` copies every resource the
   pipeline needs into the local cache — through the protocol layer — *before*
   any worker starts, so workers never race each other for the same download.

5. **Split the work into a task graph.** A tabix-indexed input is split into
   per-region tasks; anything else runs as a single sequential task. Each task
   is handed ``pipeline.raw`` and ``grr.definition`` — the *serialisable*
   forms, not the objects. Inside the worker, ``_annotate_csv`` calls
   ``build_cli_genomic_context(args)`` again — this time purely for its side
   effect, because a fresh process has an empty context registry — then
   rebuilds the repository from its definition and the pipeline from its raw
   config, and only then reads rows. Each row becomes an
   :class:`~gain.annotation.annotatable.Annotatable` according to the column
   configuration, goes through ``pipeline.annotate`` (or ``batch_annotate``),
   and is written to a part file.

6. **Write the output.** Part files are concatenated in region order; if
   requested, the result is bgzip-compressed and tabix-indexed.

What this means if you embed GAIn
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Nothing in the library initialises the genomic context for you.
``get_genomic_context()`` returns whatever has been registered so far — in a
plain Python script that has not called ``context_providers_init()``, that is
an empty context. Two consequences:

- A pipeline whose annotators name their genome and gene models explicitly
  (``gene_models: hg38/gene_models/MANE/1.5``) works without any context, and
  that is the pattern the :doc:`../python_interface` examples use — they even
  pass ``None`` as the repository, which works because the
  ``build_*_from_resource_id`` helpers fall back to the default GRR definition
  when given no repository. A pipeline
  that omits them — which is normal in YAML written for the command-line tools
  — fails at ``build_annotation_pipeline`` with *"gene models resource is
  missing in config and context"*. The fix is one line before building the
  pipeline: ``context_providers_init()``, which lets the default-GRR provider
  run, or ``register_context(SimpleGenomicContext({...}))`` to hand it
  exactly the objects you want it to find.
- Because the registry is process-global and one-shot, anything that forks or
  spawns workers must re-establish it in each worker, which is precisely what
  step 5 above does. Tests that need a clean slate call
  ``clear_registered_contexts()``.

.. attention::

   **Draft marker D5 — "genomic context" already means something else on this
   site.** The getting-started CLI page uses the phrase in its biological
   sense — whether a position is intergenic, genic or coding, as reported by
   ``simple_effect_annotator``. This page uses it for the machinery above.
   ``CONTEXT.md`` already records the same class of collision for
   "annotation"; this one is not recorded. Options: add a flagged-ambiguity
   entry and a one-sentence disambiguation here; rename one of the two uses
   in the docs; or accept it. The draft has not disambiguated in prose,
   because the right sentence depends on which option you take.

The vocabulary
--------------

The terms below are GAIn's own domain vocabulary. They are maintained in the
repository's ``CONTEXT.md`` — the file the project's maintainers and its
agents use to agree on what a word means — and rendered here from that single
source. The file is sliced at two explicit ``published-on-docs-site`` markers,
not by heading text, so moving the section around inside the file cannot
change what is published here.

.. attention::

   **Draft marker D8 — a missing marker does not fail the build.** Verified
   on this draft: moving the whole ``## Language`` block to the end of
   ``CONTEXT.md`` leaves this page byte-identical (good), but *deleting* a
   marker makes the ``include`` directive log
   ``CRITICAL: Problem with "end-before" option`` — and ``sphinx-build``
   still exits 0, and the page ships with the vocabulary silently absent.
   Sphinx does not turn docutils errors into a non-zero exit, and ``-W`` is
   not an option while the build carries ~150 pre-existing warnings. The
   deterministic fix is a two-line pre-flight in ``docs/build_docs.sh`` —
   ``grep -q`` for each marker in ``CONTEXT.md``, ``exit 1`` if absent — but
   that file is being edited by :issue:`1140` right now, so this draft does
   not touch it. Add the guard when this branch rebases onto #1140.

.. attention::

   **Draft marker D9 — five ``myst.header`` warnings.** The included fragment
   starts at ``###``, so myst-parser warns *Document headings start at H3,
   not H1* once per subsection. They are harmless (the headings nest
   correctly under this section) and the one-line fix is
   ``suppress_warnings = ["myst.header"]`` in ``conf.py`` — again #1140's
   file, so it is left for the rebase. If you would rather not suppress, the
   alternative is to start the slice at ``## Language`` itself and drop this
   page's own "The vocabulary" heading, which trades five warnings for one.

.. attention::

   **Draft marker D6 — the vocabulary does not cover the map.** The slice
   below is ``CONTEXT.md``'s ``## Language`` section as it stands: 26 terms,
   all of them about repositories, resource search, tabular score statistics,
   allele classes and ann_data resources. It defines *GRR*, *group repository*,
   *child*, *resource* and *label* — and not one term from the annotation
   path. *Annotator*, *annotatable*, *pipeline*, *context*, *entry point* are
   all absent, so a reader who came for the map gets a glossary of a different
   part of the system. Either accept that for now, or add the annotation-path
   terms to ``CONTEXT.md`` first (which improves the internal document too).
   Related: the section is called "Language" in ``CONTEXT.md`` and "The
   vocabulary" here; pick one.

.. attention::

   **Draft marker D7 — everything between the markers ships verbatim, and
   some of it is internal.** The slice cites ADR 0020 five times and ADR 0014
   once — documents the parent issue says stay unpublished — and refers to
   gain#926, #1118, #848 and #779 by bare number. It also carries 26
   ``_Avoid_:`` lines, which are instructions to *writers* about synonyms not
   to use, not information for readers. A marker slice is a contiguous range,
   so none of this can be filtered out at include time; the choice is between
   editing ``CONTEXT.md`` so that what is between the markers is fit to
   publish, moving the markers to a narrower range, or publishing as is. The
   draft publishes as is so you can see exactly what that looks like.

.. include:: ../../../CONTEXT.md
   :parser: myst_parser.sphinx_
   :start-after: <!-- published-on-docs-site: start -->
   :end-before: <!-- published-on-docs-site: end -->
