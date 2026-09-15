What an annotator declares
==========================

An annotator's output is a set of *attributes*. Three objects describe an
attribute, one per stage of its life, and the distinction between them is
what makes the rest of the chapter readable:

* An :class:`~gain.annotation.annotation_pipeline.AttributeSpec` is the
  **static declaration**: what the annotator *can* produce. It is written by
  the annotator's author, keyed by *source*, and does not depend on any
  pipeline.
* An ``AttributeConfig`` is the attribute **as written in pipeline YAML**:
  a source, the name to emit it under, whether it is internal, which
  aggregator to reduce it with, and any parameters. It is produced by the
  configuration parser and an extender never constructs one.
* An :class:`~gain.annotation.annotation_config.Attribute` is the
  **runtime attribute** a configured annotator exposes through its
  :attr:`~gain.annotation.annotation_pipeline.Annotator.attributes`: the
  config resolved against the spec, carrying both.

The specification
-----------------

:meth:`Annotator.get_attribute_specs <gain.annotation.annotation_pipeline.Annotator.get_attribute_specs>`
returns every attribute the annotator can produce, as a mapping from source
to :class:`~gain.annotation.annotation_pipeline.AttributeSpec`. It is the
catalogue the configuration is checked against — a configured attribute
whose source is not a key here is refused — and it is independent of the
configuration and of ``open``, so it may use only what the subclass set
before it delegated to the base constructor.

A spec carries the ``source``, a ``value_type``, a ``description`` shown in
the generated pipeline documentation, and four flags:

* ``is_default`` — whether the attribute is emitted when the YAML names no
  attributes at all. With no ``attributes:`` block, every default spec
  stands in, under its source name.
* ``internal_default`` — whether the attribute is *internal* unless the YAML
  says otherwise: computed and available to later annotators through the
  context, but not written to the output.
* ``supports_aggregation`` — whether an aggregator may be configured for it.
* ``attribute_type`` — ``"attribute"`` for a value, or ``"annotatable"`` for
  an attribute that is itself an annotatable and can be named as another
  annotator's ``input_annotatable``. An annotatable attribute never
  supports aggregation.

The ``value_type`` is a string naming the type of a single value: ``int``,
``float``, ``str``, ``object`` (a value that is not one of the scalar
types), ``list`` and ``annotatable`` are the spellings in use. It is what an
aggregator is validated against — a numeric aggregator over a ``str``
attribute is refused when the pipeline is built — and what the web
annotation interface reads to present a value; the tabular and VCF writers
do not consult it.

Defaults per attribute
----------------------

Some annotators have defaults that do not belong in the spec: a score
resource declares, in its own configuration, the aggregator each score
should be reduced with. :meth:`AnnotatorBase.get_attribute_defaults <gain.annotation.annotator_base.AnnotatorBase.get_attribute_defaults>`
is the hook for those. The base constructor consults it once per attribute:
an ``aggregator`` key becomes the aggregator when the YAML names none, and
every other key becomes an attribute parameter that the YAML's own
parameters override. The default returns an empty mapping, which is right
for any annotator whose defaults are already in its specs.

The runtime attribute
---------------------

Once configured, an annotator's
:attr:`~gain.annotation.annotation_pipeline.Annotator.attributes` is a list
of :class:`~gain.annotation.annotation_config.Attribute`, in configuration
order. Each one holds its ``name`` (the output column), its ``source``, the
resolved ``internal`` flag and ``aggregator``, its parameters, and the
``spec`` it was resolved against. Two things about it matter to an
implementer:

**The name is read at answer time, never cached.** A pipeline that names
one attribute twice renames the later ones, and it does so *after* every
annotator has been constructed. An annotator that captured ``attr.name``
while building its queries would answer under names the pipeline has since
moved away from. Whatever a query list caches, it must not cache names —
walk ``self.attributes`` again when you answer. The helpers on
:class:`~gain.annotation.annotator_base.AnnotatorBase` all do this for you.

**Parameters are usage-monitored.** An attribute's ``parameters`` (and the
annotator's own, on its :class:`~gain.annotation.annotation_config.AnnotatorInfo`)
are a mapping that records every key read. After an annotator is built, the
pipeline refuses any key that was configured but never read, on the grounds
that a parameter nothing consulted is a typo. So an annotator reads every
parameter it accepts in its constructor, not lazily on the first annotate.

Aggregation
-----------

An aggregator reduces *many* values to one — a position score read over a
region answers one value per position, and ``max`` or ``mean`` folds them.
The attribute *names* the aggregator; :meth:`Attribute.fold <gain.annotation.annotation_config.Attribute.fold>`
is the one statement of how it reduces. Whether there is anything to reduce
is the annotator's decision, because the container differs by annotator: the
base does not aggregate on an annotator's behalf, and
:doc:`writing_an_annotator` says which helper to reach for.

API
---

.. currentmodule:: gain.annotation.annotation_pipeline

.. autoclass:: AttributeSpec
   :members:

.. currentmodule:: gain.annotation.annotation_config

.. autoclass:: Attribute
   :members:
