
Accessing genomic resources
===========================

.. note::

   Draft for review: this page is a revision of :doc:`resources_in_python`. Both are
   published while the revision is discussed; one replaces the other.

GAIn provides a Python interface for accessing and working with genomic resources stored in Genomic Resource Repositories (GRRs). Through this interface, users can retrieve genomic sequences, inspect gene models, query genomic scores, and load datasets for further analysis using Python.

Each resource in a GRR is identified by a resource ID and has a definition describing its type, associated files, and other properties. GAIn provides Python functions for locating these resources and creating objects with methods appropriate to each resource type. For example, a reference genome object provides methods for retrieving DNA sequences, while a genomic score object allows users to retrieve scores for genomic positions, alleles, or intervals.

This section focuses on accessing and using existing genomic resources through Python. Defining new resources, configuring their properties in ``genomic_resource.yaml``, and managing them with ``grr_manage`` are covered separately in the :doc:`Genomic resources and repositories <../grr>` documentation.

The following chapters describe the Python interfaces for different types of genomic resources, beginning with repositories and resources themselves. Readers unfamiliar with the Python interface may first consult the :doc:`Overview of the GAIn Python interface <overview>`, which provides practical examples.

.. toctree::
   :maxdepth: 2

   resources/repositories_new
   resources/reference_genomes_new
   resources/gene_models_new
   resources/scores_new
   resources/position_scores_new
   resources/allele_scores_new
   resources/fragment_scores_new
   resources/gene_scores_new
   resources/histograms_new
   resources/liftover_chains_new
   resources/gene_sets_new
   resources/data_frames_and_anndata_new
   resources/adding_a_resource_type_new
