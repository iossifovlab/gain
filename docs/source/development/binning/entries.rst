Entries
=======

An entry is one item of the ``binners`` list of a run definition. A
``fragment_score_binner`` entry reduces, in each bin, the values of the
fragments of the ``fragment_score`` resources that its query matches. GAIn
provides the spec classes of the package ``gain.binning`` to build such an
entry in Python. :class:`~gain.binning.FragmentEntry` is the entry itself,
and the other spec classes are its blocks. ``binning_tool`` reads the same
classes from the YAML of a run definition, so a spec class and a YAML key
have the same meaning.

Building an entry
-----------------

A :class:`~gain.binning.FragmentEntry` needs only a resource query. Each
other field has a default:

.. code-block:: python

    from gain.binning import FragmentEntry

    entry = FragmentEntry("frags/*")

The entry above pools every resource that matches ``frags/*`` into one set
of tracks. Each fragment adds 1 to the bin that holds its start, so each
track counts fragments. ``pool=False`` makes one job for each resource, and
``search_term`` narrows the query with the full-text index of the
repository. ``name`` gives the base of the names of the tracks, in place of
the query.

Two blocks decide what a bin holds:

- ``value`` is what one fragment adds. A
  :class:`~gain.binning.ConstantContribution` adds a constant number, and
  its default, 1, counts fragments. A
  :class:`~gain.binning.ScoreContribution` adds the value of a numeric
  fragment score.
- ``aggregate`` is an :class:`~gain.binning.Aggregate`. Its ``mode`` picks
  the fragments that reach a bin: ``fragment_start``, ``fragment_length``
  or ``coverage_profile``. Its ``aggregator`` is the fold over the values
  of a bin, and ``uncovered_value`` is what a base without fragments adds.

The entry below sums the ``count`` score of the fragments as a coverage
profile, and reduces each bin by its mean:

.. code-block:: python

    from gain.binning import Aggregate, FragmentEntry, ScoreContribution

    entry = FragmentEntry(
        "frags/*",
        name="coverage",
        value=ScoreContribution("count"),
        aggregate=Aggregate(mode="coverage_profile", aggregator="mean"),
    )

``Aggregate`` keeps the ``uncovered_value`` that the caller gave, so
:func:`dataclasses.replace` derives the default of a new mode.
:attr:`~gain.binning.Aggregate.effective_uncovered_value` gives the value
that a bin uses. The user page :doc:`/binning_infrastructure` describes the
modes, the aggregators of each mode and the empty bins.

Grouping the cells of an entry
------------------------------

The ``group`` of an entry is the rule that puts each fragment into a group,
and each group makes one track. GAIn provides three spec classes for the
rule:

- :class:`~gain.binning.ConstantGroup` puts each fragment into one group
  with a constant name.
- :class:`~gain.binning.CellGrouping` finds the group of the cell barcode of
  a fragment in a cell metadata table.
- :class:`~gain.binning.ValueGrouping` uses the value of a ``str`` fragment
  score as the group.

When ``group`` is ``None``, the labels of the resources decide. A resource
with the ``cell_meta_resource_id`` label is grouped as by
``CellGrouping()``, and any other resource is the one group ``all``.

The ``meta`` of an entry is a :class:`~gain.binning.MetaSource`, and it
names the cell metadata table of a cell grouping. The table comes from a
``data_frame`` resource (``resource_id``), from the ``data_frame`` resource
that a label of each fragment resource names (``resource_label``) or from a
local file (``file_name``). A ``MetaSource`` keeps the ``file_format`` that
the caller gave, and
:attr:`~gain.binning.MetaSource.effective_file_format` gives the format
that GAIn reads.

The ``filters`` of a ``MetaSource`` select the rows of the table for each
fragment resource. Each :class:`~gain.binning.MetaFilter` is one conjunct:
the ``column`` of the table equals a literal ``value``, or the value of the
``label`` of the fragment resource. A filter on a label selects the own rows
of each sample from one shared table, and
:meth:`~gain.binning.MetaFilter.operand` gives the text that a column must
equal for one resource.

The example below builds a pooled entry over the fragment resources
``frags/*``. GAIn groups each cell by the ``class`` column of the cell
metadata table ``meta/cells``, and the filter selects the rows of the
sample of each resource:

.. code-block:: python

    from gain.binning import CellGrouping, FragmentEntry, MetaFilter, MetaSource

    entry = FragmentEntry(
        "frags/*",
        group=CellGrouping(),
        meta=MetaSource(
            resource_id="meta/cells",
            filters=(MetaFilter("sample_id", label="sample_id"),),
        ),
    )

A ``meta`` goes only with a cell grouping, or with no ``group`` on a
resource that the convention groups by its cells. The user page
:doc:`/binning_infrastructure` describes the single-cell convention and the
local metadata files.

Refusals
--------

Each spec class checks its fields when it is built. A malformed field raises
:class:`~gain.binning.RunDefinitionError`, and so does a ``meta`` that goes
with a constant group or a value grouping. A check that needs the
repository, for example the type of a score, runs later, when GAIn
resolves the entry. The functions of :doc:`groups_and_tracks` and
``binning_tool`` raise the same error for such a refusal.

API
---

The names below are exported by the package ``gain.binning``.

.. currentmodule:: gain.binning

.. autosummary::

   FragmentEntry
   ScoreContribution
   ConstantContribution
   Aggregate
   Aggregate.effective_uncovered_value
   ConstantGroup
   CellGrouping
   ValueGrouping
   MetaSource
   MetaSource.effective_file_format
   MetaFilter
   MetaFilter.operand
   RunDefinitionError

.. autoclass:: FragmentEntry
   :members:

.. autoclass:: ScoreContribution
   :members:

.. autoclass:: ConstantContribution
   :members:

.. autoclass:: Aggregate
   :members:

.. autoclass:: ConstantGroup
   :members:

.. autoclass:: CellGrouping
   :members:

.. autoclass:: ValueGrouping
   :members:

.. autoclass:: MetaSource
   :members:

.. autoclass:: MetaFilter
   :members:

.. autoclass:: RunDefinitionError
   :members:
