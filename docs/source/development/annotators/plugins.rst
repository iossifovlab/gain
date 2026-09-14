Annotator plugins
=================

The annotator types a pipeline can name are not a fixed list. Each one is a
*factory* — a callable that takes the pipeline and an annotator's
configuration and returns a built annotator — and factories are discovered
through a Python entry-point group. A package installed alongside GAIn adds
a type without any change to GAIn itself, and GAIn's own annotators are
registered the same way.

This is the extension seam for a new *annotator*; adding a new kind of
resource is a different seam, with its own entry-point group, described in
:doc:`../resources/adding_a_resource_type`.

Declaring
---------

The entry-point group is ``gain.annotation.annotators``. Its keys are
annotator *type* names — the key of a list entry in pipeline YAML — and its
values point at the factory for that type. A factory is any callable with
the signature

.. code-block:: python

    def build(pipeline: AnnotationPipeline, info: AnnotatorInfo) -> Annotator: ...

so a one-line function that constructs the class is the usual shape.

GAIn's own annotators are declared this way in the ``core`` package's
``pyproject.toml``; ``effect_annotator`` is the smallest complete example:

.. code-block:: toml

    [project.entry-points."gain.annotation.annotators"]
    effect_annotator = "gain.annotation.effect_annotator:build_effect_annotator"

A third-party package declares its own type the same way, in its own
``pyproject.toml``:

.. code-block:: toml

    [project.entry-points."gain.annotation.annotators"]
    followup = "my_package.followup:build_followup_annotator"

with ``build_followup_annotator`` the factory from
:doc:`writing_an_annotator`. The package has to be *installed* — an entry
point is metadata, written when the package is built, and a module on the
path without a distribution behind it declares nothing. An editable install
is enough.

Two keys may point at the same factory. GAIn does this for spellings it
keeps accepting — ``position_score`` and ``position_score_annotator`` are
both registered, and ``cnv_collection_annotator`` is a deprecated alias of
``fragment_score_annotator`` that warns when a pipeline names it. Nothing
prevents a third-party package from registering a type name GAIn already
uses; the group is flat, so prefer a name qualified by your project.

Discovering
-----------

Factories are loaded on the first lookup in a process, and the registry is
kept for the life of the process: every entry in the group is loaded, keyed
by its name. A name registered by two distributions logs a warning and keeps
the one met last — an order you do not control, which is the other reason to
qualify a name.

:func:`~gain.annotation.annotation_factory.get_annotator_factory` is the
lookup the pipeline loader uses. A type nothing has registered raises
``ValueError``; a type GAIn used to ship and has since retired raises the
same, with a message saying what replaced it.
:func:`~gain.annotation.annotation_factory.get_available_annotator_types`
lists every registered name, which is the quickest way to check that an
installed plugin was picked up:

.. code-block:: python

    from gain.annotation.annotation_factory import get_available_annotator_types

    assert "followup" in get_available_annotator_types()

:func:`~gain.annotation.annotation_factory.register_annotator_factory`
adds a factory to the in-process registry directly, without an entry point.
That is the right tool for a test, or for an annotator defined in the same
script that runs it; it is not a substitute for the entry point in a package
meant to be installed, because it only takes effect once the registering
module has been imported — and a pipeline built in a fresh worker process,
as ``annotate_tabular`` does with more than one worker, starts from an
empty registry and may never import it.

Naming in a pipeline
--------------------

Once registered, a type is named in pipeline YAML like any built-in one: the
entry-point key is the key of the list entry, and the entry's body is the
annotator's parameters and, optionally, its ``attributes``:

.. code-block:: yaml

    - position_score_annotator:
        resource_id: hg38/scores/phyloP7way

    - allele_score_annotator:
        resource_id: hg38/scores/ClinVar_20251019
        attributes:
        - name: clinical_significance
          source: CLNSIG

    - followup: {}

The loader parses each entry into an
:class:`~gain.annotation.annotation_config.AnnotatorInfo` — the type, the
configured attributes, the parameters with ``work_dir`` injected, the
resources the parser resolved — looks the type up, and calls the factory
with the pipeline and that info. Everything after that is the annotator's
constructor, described in :doc:`writing_an_annotator`. The empty mapping
after ``followup`` is what an annotator with no parameters and default
attributes needs; ``annotate_tabular`` then emits its ``followup`` column
after ``phyloP7way`` and ``clinical_significance``, in pipeline order.

The shipped plugins
-------------------

Three packages in the GAIn repository are annotator plugins in exactly this
sense: each is its own distribution, with its own ``pyproject.toml`` and its
own entry-point table, and GAIn's core knows nothing about them. They are
the worked examples for this chapter, cited here rather than paraphrased:

``demo_annotator``
    Four annotators, purpose-built as an example: ``external_demo_annotator``
    and ``external_demo_stream_annotator`` over one toy tool (through files
    and through a stream), ``external_demo_gene_annotator`` and
    ``external_demo_genome_annotator`` over tools that take a GRR resource.
    Its ``pyproject.toml`` also declares the tools themselves as console
    scripts, which is how the adapters find them.

``spliceai_annotator``
    One annotator, ``spliceai_annotator``, over the SpliceAI model — the
    in-tree annotator with both a per-item and a batched path.

``vep_annotator``
    Two annotators, ``vep_full_annotator`` and ``vep_effect_annotator``,
    over Ensembl VEP in a container.

Each declares its group in the same three-line form as the examples above,
so its ``pyproject.toml`` is the thing to open when you set up your own.

API
---

.. currentmodule:: gain.annotation.annotation_factory

.. autofunction:: get_annotator_factory

.. autofunction:: get_available_annotator_types

.. autofunction:: register_annotator_factory
