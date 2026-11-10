Binning
=======

``binning_tool`` cuts the genome into fixed-size bins and reduces each
track to one number per bin. The result is a bins × tracks matrix in one
HDF5 file. GAIn provides the package ``gain.binning`` as the Python side of
``binning_tool``: a script or a notebook uses it to prepare the entries of a
run and to read the file that the run writes.

A binning run has three parts. An **entry** is one item of the ``binners``
list of a run definition, and it names a binner kind and the resources that
it reads. A **group** is the set of fragments that one fragment track
reduces, and its name is the selector of the track. A **track** is one
column of the output matrix, and each track belongs to one entry.

A notebook works with ``gain.binning`` in four steps:

1. Build a ``fragment_score_binner`` entry from the spec classes, as
   :doc:`binning/entries` describes.
2. Preview the groups and the tracks of the entry without a run, as
   :doc:`binning/groups_and_tracks` describes.
3. Run ``binning_tool`` on a run definition that holds the entry. The user
   page :doc:`/binning_infrastructure` describes the command.
4. Read the file of the run back, as :doc:`binning/reading_a_run`
   describes.

The package has two limits. Python does not run a binning: only
``binning_tool`` writes the file of a run. Only the ``fragment_score_binner``
entries have spec classes. A ``position_score_binner`` entry has no spec
class: you write it as a mapping, in the YAML of a run definition or as a
dict that you pass to ``resolve_binners``.

The examples of this chapter import the package ``gain.grr`` as ``grr`` and
call the repository ``repo``:

.. code-block:: python

    import gain.grr as grr

    repo = grr.build_genomic_resource_repository()

.. rubric:: Where the line falls

The run definition, the command line of ``binning_tool`` and the layout of
the file are the user side. The user page :doc:`/binning_infrastructure`
describes them, not this chapter. This chapter describes only the names
that ``gain.binning`` exports. Import each name from the package, for
example ``from gain.binning import FragmentEntry``. The modules of the
package that define the names are not the import path.

.. toctree::
   :maxdepth: 2

   binning/entries
   binning/groups_and_tracks
   binning/reading_a_run
