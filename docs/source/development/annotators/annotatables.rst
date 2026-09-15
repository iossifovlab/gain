What an annotator receives
==========================

An *annotatable* is the thing a pipeline annotates: one position, one region
or one allele, on one chromosome. It is the first argument of every
``annotate`` call, and the only thing an annotator knows about the input row
apart from the context the earlier annotators built.

Annotators are typed on the base class,
:class:`~gain.annotation.annotatable.Annotatable`, rather than on a variant,
because a pipeline does not only annotate variants. ``annotate_tabular``
builds a :class:`~gain.annotation.annotatable.Position` from a chromosome
and a position column, a :class:`~gain.annotation.annotatable.Region` from
an added end column, a :class:`~gain.annotation.annotatable.VCFAllele` when
there are reference and alternative columns, and a
:class:`~gain.annotation.annotatable.CNVAllele` for a large deletion or
duplication. All four reach an annotator through the same interface, and an
annotator that only makes sense for some of them checks the annotatable's
``type`` — an :class:`~gain.annotation.annotatable.Annotatable.Type` — and
answers the empty result for the rest.

The interval
------------

Every annotatable spans a closed, 1-based interval ``[pos, pos_end]``, both
ends inclusive: a position has ``pos_end == pos``, and ``len(annotatable)``
is ``pos_end - pos + 1``. An annotator that queries a score or gene models
queries exactly this span. The canonical spellings are ``chrom``, ``pos`` and
``pos_end`` — the constructor's arguments and the keys ``to_dict`` writes —
with ``chromosome``, ``position`` and ``end_position`` as aliases.

A :class:`~gain.annotation.annotatable.VCFAllele` derives both its
:class:`~gain.annotation.annotatable.Annotatable.Type` and its interval from
the two alleles, and the docstring below states the rule. The one thing to
carry away is that for a deletion or a complex allele ``pos_end`` reaches one
base *past* the last reference base.

Serialised form
---------------

An annotatable round-trips through a string of the form ``TYPE(args...)``:
``repr`` writes it and
:meth:`Annotatable.from_string <gain.annotation.annotatable.Annotatable.from_string>`
reads it back, dispatching on the type token to the right subclass. That is
the form an annotator writes when it hands its input to an external tool
one line at a time, and the form such a tool parses on the other side;
``demo_annotator`` does exactly this in both directions.

A ``None`` annotatable
----------------------

An input row may have no annotatable at all — an unparsable variant, a
liftover that found nothing. The pipeline passes ``None`` through, and the
answer for it is every attribute set to ``None``, never an exception.
:class:`~gain.annotation.annotator_base.AnnotatorBase` handles the ``None``
before it reaches the subclass, so an annotator built on it never sees one
in ``_do_annotate``; a batch-only annotator that overrides
``_do_batch_annotate`` is responsible for its own ``None`` entries.

API
---

.. currentmodule:: gain.annotation.annotatable

.. autoclass:: Annotatable
   :members:

.. autoclass:: Position
   :members:

.. autoclass:: Region
   :members:

.. autoclass:: VCFAllele
   :members:

.. autoclass:: CNVAllele
   :members:
