Pipelines
=========

An :class:`~gain.annotation.annotation_pipeline.AnnotationPipeline` is an
ordered list of annotators over one repository. It is what
``annotate_tabular`` and ``annotate_vcf`` build from the YAML they are
given, and what a Python caller builds from the same YAML with
:func:`~gain.annotation.annotation_factory.load_pipeline_from_yaml`.

Loading
-------

Three loaders share one signature and differ only in where the YAML comes
from: :func:`~gain.annotation.annotation_factory.load_pipeline_from_yaml`
takes the text, :func:`~gain.annotation.annotation_factory.load_pipeline_from_file`
a path, and :func:`~gain.annotation.annotation_factory.load_pipeline_from_file_or_resource`
either a path or the id of a GRR resource of type ``annotation_pipeline`` —
the form the command-line tools accept. Each takes the repository the
annotators resolve their resources through, and two keyword arguments:

* ``work_dir`` — the directory under which every annotator gets a
  subdirectory of its own, ``A<index>_<type>``, injected into its parameters
  as ``work_dir``. Left unset, a temporary directory is minted with a
  warning; pass one.
* ``allow_repeated_attributes`` — what to do when two annotators emit the
  same attribute name. Off, the pipeline refuses to build and names the
  overlap; on, the later attributes are renamed by suffixing their
  annotator's id.

Loading does everything but open. For each entry of the YAML, in order, the
loader resolves the entry's type to a factory (:doc:`plugins`), calls it
with the pipeline and the entry's :class:`~gain.annotation.annotation_config.AnnotatorInfo`,
wraps the result for ``input_annotatable`` and ``value_transform`` if the
entry uses them, and refuses any parameter the annotator never read. A
configuration fault at any of those steps is raised as an
``AnnotationConfigurationError`` naming the annotator.

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
loops. The optional ``batch_work_dir`` is a scratch directory the caller may
offer to the batched annotators, relative to their own ``work_dir``.

Both open the pipeline on first use if nothing has opened it; both leave it
open. The pipeline is a context manager whose exit closes it — and, as with
the resource objects, entering it does not open it, so the spelling that
does both is ``with pipeline.open() as pipeline:``. ``close`` closes every
annotator, logging rather than propagating what any of them raises.

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

A ``ReannotationPipeline`` is built from a new pipeline and the previous one
and runs only what changed: the annotators new to the pipeline; the
annotators whose :attr:`~gain.annotation.annotation_pipeline.Annotator.used_context_attributes`
name an attribute a new annotator now produces; and the annotators whose
*internal* attributes a new annotator reads, since an internal attribute is
not in the previous output and has to be computed again. Everything else is
copied from the previous output. That is the reason the tuple matters to an
implementer: an annotator that reads the context without declaring what it
reads is not rerun when its inputs change.

API
---

.. currentmodule:: gain.annotation.annotation_factory

.. autofunction:: load_pipeline_from_yaml

.. autofunction:: load_pipeline_from_file

.. autofunction:: load_pipeline_from_file_or_resource

.. currentmodule:: gain.annotation.annotation_pipeline

.. autoclass:: AnnotationPipeline
   :members:

.. currentmodule:: gain.annotation.annotation_config

.. autoclass:: AnnotatorInfo
   :members:
