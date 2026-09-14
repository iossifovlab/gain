Writing an annotator
====================

Two classes define what an annotator is.
:class:`~gain.annotation.annotation_pipeline.Annotator` is the interface the
pipeline drives: open, annotate, close, and the attributes it produces.
:class:`~gain.annotation.annotator_base.AnnotatorBase` is the class an
annotator actually extends: it implements the interface once, on top of two
narrower hooks that a subclass fills in. Every annotator GAIn ships, and
every in-tree plugin, is built on ``AnnotatorBase``; the interface class is
documented so you can read the contract the base keeps, not so you can
implement it yourself.

What the base does for you
--------------------------

The base constructor takes the pipeline and an
:class:`~gain.annotation.annotation_config.AnnotatorInfo` — the annotator's
type, id, configured attributes, parameters and resources, as parsed from the
YAML. It checks each configured attribute against
:meth:`~gain.annotation.annotation_pipeline.Annotator.get_attribute_specs`,
resolves its name, aggregator and parameters (consulting
:meth:`~gain.annotation.annotator_base.AnnotatorBase.get_attribute_defaults`),
and requires a ``work_dir`` parameter — a directory of the annotator's own,
which the pipeline injects into every annotator's parameters before the
factory runs and which
:meth:`~gain.annotation.annotator_base.AnnotatorBase.open` creates.

Because ``get_attribute_specs`` is called from the base constructor, a
subclass that computes its specs from something — a resource's score list,
say — sets that something *before* it calls ``super().__init__``.

Two YAML parameters are handled by the pipeline rather than by the
annotator, and a subclass never sees them: ``input_annotatable``, which
substitutes an annotatable produced earlier in the pipeline for the input
row's own, and an attribute's ``value_transform``, which rewrites the answer
on its way into the context. Both are applied by wrapping the built
annotator, so the contract below is stated for the annotator alone.

The contract
------------

A subclass implements two members, and may override a handful more.

**Always:**

* :meth:`~gain.annotation.annotation_pipeline.Annotator.get_attribute_specs`
  — every attribute the annotator can produce, keyed by source
  (:doc:`attributes`).
* :meth:`~gain.annotation.annotator_base.AnnotatorBase._do_annotate` —
  produce the attributes for one annotatable. It receives the annotatable
  (never ``None``; the base answers the empty result for that before
  delegating) and the context, and answers an
  :class:`~gain.annotation.annotator_base.AnnotatedValues`: a mapping keyed
  by attribute *name*, every value finished. The base hands it back to the
  pipeline as it is; nothing is reduced or renamed on the annotator's
  behalf.

**When there is a reason:**

* :meth:`~gain.annotation.annotator_base.AnnotatorBase._do_batch_annotate`
  — the same contract for a sequence of annotatables: one
  ``AnnotatedValues`` per input, in input order, the empty result for each
  ``None``. The default loops ``_do_annotate`` and is correct for every
  annotator; override it only when the backend has a genuinely batched
  path — an external tool run once over a file, a model that predicts
  a batch at a time. A batch-*only* annotator, one with no per-item path,
  overrides ``annotate`` to raise ``NotImplementedError`` as well; the
  ``demo_annotator`` adapters and the VEP annotators are the shipped
  examples.
* :meth:`~gain.annotation.annotator_base.AnnotatorBase.open` and
  :meth:`~gain.annotation.annotation_pipeline.Annotator.close` — when the
  annotator holds a resource. Overrides open what they query, call the base
  and return ``self``; ``close`` must be safe on an annotator never opened
  and safe twice.
* :meth:`~gain.annotation.annotator_base.AnnotatorBase.get_attribute_defaults`
  — when per-attribute defaults live somewhere other than the spec.
* :attr:`~gain.annotation.annotation_pipeline.Annotator.used_context_attributes`
  — when the annotator reads attributes an earlier annotator produced. Name
  them here: the pipeline builds its dependency graph from this tuple, and
  a reannotation reruns the annotator when a named attribute's producer
  changes. Reading the context without declaring it works for a plain
  annotation run, and silently goes stale under reannotation.
* :attr:`~gain.annotation.annotator_base.AnnotatorBase.ACCEPTED_RESOURCE_TYPES`
  and :meth:`~gain.annotation.annotator_base.AnnotatorBase.resolve_resource`
  — when the annotator takes a ``resource_id`` naming a typed genomic
  resource. Declare the types it accepts, resolve through the classmethod,
  and the refusal for a resource of the wrong type is phrased the same way
  as every other annotator's.

**Never:** :meth:`~gain.annotation.annotator_base.AnnotatorBase.annotate`
and :meth:`~gain.annotation.annotator_base.AnnotatorBase.batch_annotate`.
They are the pipeline's side of the contract, and the base already routes
them to the two hooks. The one exception is the batch-only ``annotate``
above.

Shaping the answer
------------------

Every ``AnnotatedValues`` is keyed by attribute *name*, and names must be
read when the annotator answers, never cached beside the queries it built —
:doc:`attributes` says why. The base provides four helpers that read
``self.attributes`` at answer time so a subclass does not have to:

* :meth:`~gain.annotation.annotator_base.AnnotatorBase._every` — one value
  under every attribute's name. For an annotator with one thing to say
  however many attributes expose it: a lifted-over annotatable, a renamed
  chromosome, a yes/no decision.
* :meth:`~gain.annotation.annotator_base.AnnotatorBase._from_sources` — a
  source-keyed mapping answered by attribute name, nothing folded. For
  values that are final as they stand — a point read's one value per score,
  a tool's output row. An absent source answers ``None``.
* :func:`~gain.annotation.annotator_base.fold_own_values` — the folding
  twin of ``_from_sources``, for an annotator whose values are its own
  rather than a score's record stream: each attribute's source value is
  reduced by the aggregator the attribute names, if it names one and the
  value is a list, and answered under the attribute's name.
* :meth:`~gain.annotation.annotator_base.AnnotatorBase._pair_aggregated`
  and :meth:`~gain.annotation.annotator_base.AnnotatorBase._pair_all` — for
  a score annotator whose score did the folding during the read, and
  answered one value per query in attribute order. They pair those values
  back onto the attributes by position, and refuse a read that answered the
  wrong number of them.

:meth:`~gain.annotation.annotator_base.AnnotatorBase._empty_result` is
``_every(None)``: the answer for a ``None`` annotatable, and what an
annotator returns when a guard fires — a chromosome the resource does not
have, a region past a length cutoff.

A minimal annotator
-------------------

The smallest complete annotator declares one attribute, reads the context,
and answers through ``_every``. This is the shape of the plugin
:doc:`/python_interface` builds in its fifth section, whose full adapter is
downloadable there:

.. code-block:: python

    from typing import Any

    from gain.annotation.annotatable import Annotatable
    from gain.annotation.annotation_pipeline import (
        AnnotationPipeline,
        Annotator,
        AnnotatorInfo,
        AttributeSpec,
    )
    from gain.annotation.annotator_base import AnnotatedValues, AnnotatorBase


    class FollowupAnnotator(AnnotatorBase):
        """Flag a variant for follow-up from attributes already computed."""

        def get_attribute_specs(self) -> dict[str, AttributeSpec]:
            return {
                "followup": AttributeSpec(
                    source="followup",
                    value_type="str",
                    description="Whether the variant is selected for follow-up",
                ),
            }

        @property
        def used_context_attributes(self) -> tuple[str, ...]:
            return ("phyloP7way", "clinical_significance")

        def _do_annotate(
            self, annotatable: Annotatable, context: dict[str, Any],
        ) -> AnnotatedValues:
            conserved = (context.get("phyloP7way") or 0) > 0
            pathogenic = context.get("clinical_significance") == "Pathogenic"
            return self._every("yes" if conserved and pathogenic else "no")


    def build_followup_annotator(
        pipeline: AnnotationPipeline, info: AnnotatorInfo,
    ) -> Annotator:
        return FollowupAnnotator(pipeline, info)

The module-level function at the end is the *factory*: the callable a
pipeline resolves the annotator's type to, taking the pipeline and the
``AnnotatorInfo`` and returning a built annotator. :doc:`plugins` says how
it is registered under a name.

Annotators that wrap a tool
---------------------------

An annotator that runs an external program — in a subprocess, or in a
container — is a batch annotator by nature: it writes its inputs to a file
or a stream, runs the tool once, and reads the answers back. The three
in-tree plugin packages are the worked examples, and are cited here rather
than paraphrased:

* ``demo_annotator`` — four annotators, purpose-built as an example. Its
  ``adapter`` module shows the adapter pattern twice over the same toy tool:
  once through temporary files in the annotator's ``work_dir``, once over
  the tool's stdin and stdout. Its two ``demo_annotate_*_adapter`` modules
  show an adapter that hands a GRR resource — gene models, a reference
  genome — to a tool that reads files, through a caching repository rooted
  in the ``work_dir``.
* ``vep_annotator`` — two real annotators over Ensembl VEP in a container,
  on a shared base that builds VEP's input and parses its output.
* ``spliceai_annotator`` — a real annotator over a model, with a per-item
  ``_do_annotate`` *and* a batched ``_do_batch_annotate`` that predicts many
  requests at once; the one in-tree annotator with both paths.

:class:`~gain.annotation.docker_annotator.DockerAnnotator` is the base the
containerised ones share: it holds a Docker client, prepares the images the
annotator needs in ``open``, and leaves the ``run`` of one batch abstract.

API
---

.. currentmodule:: gain.annotation.annotation_pipeline

.. autoclass:: Annotator
   :members:

.. currentmodule:: gain.annotation.annotator_base

.. autoclass:: AnnotatorBase
   :members:
   :private-members: _do_annotate, _do_batch_annotate, _every, _from_sources, _empty_result, _pair_aggregated, _pair_all

.. autoclass:: AnnotatedValues

.. autofunction:: fold_own_values

.. currentmodule:: gain.annotation.docker_annotator

.. autoclass:: DockerAnnotator
   :members:
