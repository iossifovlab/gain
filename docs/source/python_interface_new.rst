GAIn Python interface new
=========================

.. note::

   Draft for review: this page is a revision of :doc:`python_interface`. Both are
   published while the revision is discussed; one replaces the other.

This section describes GAIn as a Python library. Use it to work with GRR
resources directly from your own scripts, to embed GAIn in your own code, to
add a resource implementation, or to write an annotator plugin.

If you write pipeline YAML or a command line rather than Python, read the
user pages instead: :doc:`grr` for resources and repositories, and
:doc:`annotation_infrastructure` for pipelines and annotators. Everything in
this section is Python.

The chapters build bottom-up, substrate before the things that consume it:

1. :doc:`development/overview` — the place to start: it connects to a
   repository and walks through five end-to-end examples, from reading
   chromosome lengths to writing, registering and running a small annotator
   plugin.
2. :doc:`development/resources_in_python` — the resource part of the
   declared API: the builders, repositories and resources, reference genomes,
   gene models, the three kinds of score, histograms, and the seam for adding
   a resource type.
3. :doc:`development/annotators` — the annotation part of the declared API:
   what an annotator receives and declares, the two classes an annotator is
   built on, the pipeline that drives it, and the entry-point group through
   which a package of your own becomes an annotator a pipeline can name.
4. :doc:`development/task_graph` — the task graph: how a script builds a
   graph of tasks, runs it with a task executor, and lets a task cache skip
   the tasks that are already done.
5. :doc:`development/module_index` — the complete generated API tree, for when
   you already know the name you are looking for.

.. toctree::
   :maxdepth: 2

   development/overview_new
   development/resources_in_python_new
   development/annotators
   development/task_graph
   development/module_index
