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

Creating an annotatable
-----------------------

Each of the four classes has a constructor that takes the chromosome and the
coordinates directly:

.. code-block:: python

    from gain.annotation.annotatable import (
        Annotatable,
        CNVAllele,
        Position,
        Region,
        VCFAllele,
    )

    position = Position("chr1", 11796321)
    region = Region("chr1", 11796321, 11796400)
    allele = VCFAllele("chr1", 11796321, "G", "A")
    deletion = CNVAllele(
        "chr1", 11796321, 11896400, Annotatable.Type.LARGE_DELETION)

A :class:`~gain.annotation.annotatable.CNVAllele` takes its type as the
fourth argument, and accepts only ``LARGE_DELETION`` and
``LARGE_DUPLICATION``. The other three classes set their type themselves.

A tabular input row becomes an annotatable through
:mod:`gain.annotation.record_to_annotatable`, the module that
``annotate_tabular`` uses.
:func:`~gain.annotation.record_to_annotatable.build_record_to_annotatable`
takes the column names a source has, and returns a converter for the first
column set it finds, in this order:

* ``chrom``, ``pos_beg``, ``pos_end`` and ``cnv_type`` — a ``CNVAllele``;
* ``chrom``, ``pos_beg`` and ``pos_end`` — a ``Region``;
* ``chrom``, ``pos``, ``ref`` and ``alt`` — a ``VCFAllele``;
* ``vcf_like`` — a ``VCFAllele`` from one ``chrom:pos:ref:alt`` column;
* ``chrom``, ``pos`` and ``variant``, or ``location`` and ``variant`` — a
  ``VCFAllele`` from a DAE or CSHL variant description, read against the
  reference genome passed as ``ref_genome`` (a CSHL copy-number description
  gives a ``CNVAllele``);
* ``chrom`` and ``pos`` — a ``Position``.

Its full signature is ``build_record_to_annotatable(renamed_columns,
available_columns, ref_genome=None)``. The first argument, a mapping, renames
the columns: the key ``col_<column>`` maps one of the names above to the
column that holds it, and the value ``"-"`` excludes that column. The second
argument is the set of column names the source has. The converter's
``build`` method takes only the row, as a mapping from column name to value,
and turns it into an annotatable. Here the ``CHROM`` column stands in for
``chrom``:

.. code-block:: python

    from gain.annotation.record_to_annotatable import (
        build_annotatable_from_dict,
        build_record_to_annotatable,
    )

    converter = build_record_to_annotatable(
        {"col_chrom": "CHROM"}, {"CHROM", "pos"})
    position = converter.build({"CHROM": "chr1", "pos": "5"})

:func:`~gain.annotation.record_to_annotatable.build_annotatable_from_dict`
does both steps for one dictionary, with no renaming:

.. code-block:: python

    allele = build_annotatable_from_dict(
        {"chrom": "chr1", "pos": "11796321", "ref": "G", "alt": "A"})

Both raise ``MalformedRecordError``, a ``ValueError``, when no column set
matches or when a row has a field that cannot be read.

An annotatable holds nothing to open or close, so this page has no lifecycle
section.

The interval
------------

Every annotatable spans a closed, 1-based interval ``[pos, pos_end]``, both
ends inclusive: a position has ``pos_end == pos``, and ``len(annotatable)``
is ``pos_end - pos + 1``. An annotator that queries a score or gene models
queries exactly this span. The canonical spellings of the attributes are
``chrom``, ``pos`` and ``pos_end``, with ``chromosome``, ``position`` and
``end_position`` as aliases.

``to_dict`` writes a different set of keys for each class, and every class
writes ``type``, the name of its
:class:`~gain.annotation.annotatable.Annotatable.Type`:

* ``Position``: ``type``, ``chrom`` and ``pos``.
* ``Region`` and ``CNVAllele``: ``type``, ``chrom``, ``pos_begin`` and
  ``pos_end``.
* ``VCFAllele``: ``type``, ``chrom``, ``pos``, ``ref`` and ``alt``. It does
  not write ``pos_end``, because the alleles determine it.

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

.. currentmodule:: gain.annotation.record_to_annotatable

.. autofunction:: build_record_to_annotatable

.. autofunction:: build_annotatable_from_dict
