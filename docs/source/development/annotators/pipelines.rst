Pipelines
=========

An :class:`~gain.annotation.annotation_pipeline.AnnotationPipeline` is an
ordered list of annotators over one repository. It is what
``annotate_tabular`` and ``annotate_vcf`` build from the YAML they are
given, and what a Python caller builds from the same YAML with
:func:`~gain.annotation.annotation_factory.load_pipeline_from_yaml`.

Loading a pipeline
------------------

Four loaders build a pipeline and differ only in where the configuration
comes from:

* :func:`~gain.annotation.annotation_factory.load_pipeline_from_yaml` takes
  the YAML text.
* :func:`~gain.annotation.annotation_factory.load_pipeline_from_file` takes
  the path of a YAML file.
* :func:`~gain.annotation.annotation_factory.load_pipeline_from_file_or_resource`
  takes either a path or the id of a GRR resource of type
  ``annotation_pipeline``, the form the command-line tools accept.
* :func:`~gain.annotation.annotation_factory.build_annotation_pipeline`
  takes the configuration already parsed, as the list or dictionary that
  ``yaml.safe_load`` returns. The other three loaders read the YAML and
  call it.

Each takes the repository the annotators resolve their resources through,
and two keyword arguments:

* ``work_dir`` — a :class:`pathlib.Path` under which every annotator gets a
  subdirectory of its own, ``A<index>_<type>``. The loader injects that
  subdirectory into the annotator's parameters as ``work_dir``, unless the
  YAML entry sets one. Pass a ``Path``: the loader joins the subdirectory
  name with ``/`` for each annotator, so a ``str`` raises ``TypeError`` as
  soon as the configuration has an annotator. Left unset, a
  temporary directory is minted with a warning; pass one.
* ``allow_repeated_attributes`` — what to do when two annotators emit the
  same attribute name. Off, the pipeline refuses to build and names the
  overlap. On, every attribute with that name, the first one included, is
  renamed to ``<name>_<annotator_id>``.

.. code-block:: python

    import pathlib

    import gain.grr as grr
    from gain.annotation.annotation_factory import build_annotation_pipeline

    repo = grr.build_genomic_resource_repository()
    pipeline = build_annotation_pipeline(
        [{"position_score": {"resource_id": "hg38/scores/phyloP7way"}}],
        repo,
        work_dir=pathlib.Path("annotation_work"),
    )

Loading does everything but open. For each entry of the configuration, in
order, the loader resolves the entry's type to an annotator factory
(:doc:`plugins`), calls it with the pipeline and the entry's
:class:`~gain.annotation.annotation_config.AnnotatorInfo`, wraps the result
for ``input_annotatable`` and ``value_transform`` if the entry uses them,
and refuses any parameter the annotator never read. A fault in the
configuration is reported as an
:class:`~gain.annotation.annotation_config.AnnotationConfigurationError` in
four ways:

* The configuration parser raises it for most faults in the shape of the
  configuration.
* The loader catches a ``ValueError`` or a ``FileNotFoundError`` that any of
  the steps above raises, and raises an ``AnnotationConfigurationError``
  that names the annotator and carries the original error.
* An annotator raises it directly for a parameter value it refuses: one
  read with ``get_number`` or ``get_integer`` (:doc:`writing_an_annotator`),
  or, in the allele and fragment score annotators, a filter expression that
  does not parse or a value they do not accept.
* After the last entry, the loader raises it when two annotators emit the
  same attribute name and ``allow_repeated_attributes`` is off.

The loader wraps no other exception: a ``TypeError`` or a ``KeyError`` from
an annotator factory passes through unchanged.

The lifecycle
-------------

A loader returns the pipeline closed.
:meth:`~gain.annotation.annotation_pipeline.AnnotationPipeline.open` opens
every annotator, and
:meth:`~gain.annotation.annotation_pipeline.AnnotationPipeline.close`
closes every annotator, logging rather than propagating what any of them
raises. The pipeline is a context manager whose exit closes it, and, as
with the resource objects, entering it does not open it. The spelling that
does both is ``with pipeline.open() as pipeline:``.

Running
-------

:meth:`~gain.annotation.annotation_pipeline.AnnotationPipeline.annotate`
takes one annotatable (or ``None``) and an optional context, runs every
annotator in order, merging each one's answer into the context before the
next runs, and returns the context. So the result carries every attribute of
every annotator, internal ones included — it is the writers, not the
pipeline, that drop internal attributes from the output.

:meth:`~gain.annotation.annotation_pipeline.AnnotationPipeline.batch_annotate`
does the same over a sequence, one context per input, calling each
annotator's ``batch_annotate`` once with the whole sequence. An annotator
with a batched backend runs its tool once per call; every other annotator
loops. The optional ``batch_work_dir`` is an opaque scratch path for the
batched annotators. The pipeline passes it to each annotator unchanged and
gives it no meaning of its own. GAIn's core annotators ignore it, and the
``demo_annotator`` and VEP adapters treat it as a path under their own
``work_dir``.

Both open the pipeline on first use if nothing has opened it; both leave it
open.

Reading a pipeline
------------------

Once built, a pipeline answers questions about itself:
:meth:`~gain.annotation.annotation_pipeline.AnnotationPipeline.get_attributes`
lists every attribute in pipeline order,
:meth:`~gain.annotation.annotation_pipeline.AnnotationPipeline.get_attribute_info`
finds one by name (the first match, so a later annotator that reuses a name
is shadowed), and
:meth:`~gain.annotation.annotation_pipeline.AnnotationPipeline.get_annotator_by_attribute_info`
finds the annotator that produces it.
:meth:`~gain.annotation.annotation_pipeline.AnnotationPipeline.get_resource_ids`
is the set of every resource any annotator uses — what a caching repository
would need to fetch to run the pipeline offline. The
:class:`~gain.annotation.annotation_config.AnnotatorInfo` of each annotator
is available through
:meth:`~gain.annotation.annotation_pipeline.AnnotationPipeline.get_info`,
and ``to_dict`` on it round-trips to the YAML shape it was parsed from.

Reannotation
------------

A :class:`~gain.annotation.annotation_pipeline.ReannotationPipeline` is
built from a new pipeline and the previous one, and runs only what changed:

* the annotators new to the pipeline;
* the annotators whose
  :attr:`~gain.annotation.annotation_pipeline.Annotator.used_context_attributes`
  name an attribute a new annotator now produces;
* the annotators whose *internal* attributes a new annotator reads, since an
  internal attribute is not in the previous output and has to be computed
  again.

Everything else is copied from the previous output. That is the reason the
tuple matters to an implementer: an annotator that reads the context without
declaring what it reads is not rerun when its inputs change.

The keyword argument ``full_reannotation=True`` turns the selection off: the
reannotation pipeline runs every annotator of the new pipeline, copies
nothing, and marks every attribute of the previous pipeline as deleted. The
``--full-reannotation`` option of ``annotate_tabular`` and ``annotate_vcf``
sets it.

The attribute ``plan`` holds the decision as a ``ReannotationPlan``: the
attributes copied, added, computed again and deleted, each with the
annotator that produces it.
:meth:`~gain.annotation.annotation_pipeline.ReannotationPipeline.format_plan`
renders the plan as text, and
:meth:`~gain.annotation.annotation_pipeline.ReannotationPipeline.print_plan`
prints it, to standard error by default:

.. code-block:: python

    from gain.annotation.annotation_pipeline import ReannotationPipeline

    reannotation = ReannotationPipeline(pipeline_new, pipeline_previous)
    reannotation.print_plan(reference="previous.yaml")

API
---

.. currentmodule:: gain.annotation.annotation_factory

.. autofunction:: load_pipeline_from_yaml

.. autofunction:: load_pipeline_from_file

.. autofunction:: load_pipeline_from_file_or_resource

.. autofunction:: build_annotation_pipeline

.. currentmodule:: gain.annotation.annotation_pipeline

.. autoclass:: AnnotationPipeline
   :members:

.. autoclass:: ReannotationPipeline
   :members: format_plan, print_plan

.. currentmodule:: gain.annotation.annotation_config

.. autoclass:: AnnotatorInfo
   :members:

.. autoclass:: AnnotationConfigurationError
