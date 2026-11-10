Binning
=======

This chapter is the Python side of ``binning_tool``. The package
``gain.binning`` builds the entries of a run definition as Python objects,
shows the groups and the tracks of an entry without a run, and reads the
HDF5 file of a run back.

.. rubric:: Where the line falls

The run definition, the command line of ``binning_tool`` and the layout of
the file are the user side. The user page :doc:`/binning_infrastructure`
describes them, not this chapter. This chapter describes only the names
that ``gain.binning`` exports.

.. toctree::
   :maxdepth: 2

   binning/api
