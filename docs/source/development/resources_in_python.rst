
Genomic resources
=================

GAIn provides a Python interface for accessing and working with genomic resources stored in Genomic Resource Repositories (GRRs). Through this interface, users can retrieve genomic sequences, inspect gene models, query genomic scores, and load datasets for further analysis using Python.

Each resource in a GRR is identified by a resource ID and has a definition describing its type, associated files, and other properties. GAIn provides Python functions for locating these resources and creating objects with methods appropriate to each resource type. For example, a reference genome object provides methods for retrieving DNA sequences, while a genomic score object allows users to retrieve scores for genomic positions, alleles, or intervals.

This section focuses on accessing and using existing genomic resources through Python. Defining new resources, configuring their properties in ``genomic_resource.yaml``, and managing them with ``grr_manage`` are covered separately in the :doc:`Genomic resources and repositories <../grr>` documentation.

The following chapters describe the Python interfaces for different types of genomic resources, beginning with repositories and resources themselves. Readers unfamiliar with the Python interface may first consult the :doc:`overview` page, which provides practical examples.

.. toctree::
   :maxdepth: 2

   resources/repositories
   resources/reference_genomes
   resources/gene_models
   resources/scores
   resources/position_scores
   resources/allele_scores
   resources/fragment_scores
   resources/gene_scores
   resources/histograms
   resources/liftover_chains
   resources/gene_sets
   resources/data_frames_and_anndata
   resources/adding_a_resource_type
