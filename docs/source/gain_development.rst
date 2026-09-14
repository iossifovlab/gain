GAIn Development
================

This section is the in-depth description of GAIn as a Python library: the
objects you touch when you embed GAIn in your own code, add a resource
implementation, or write an annotator plugin. It is written for the
*extender* — someone building on GAIn — rather than for GAIn's own
contributors.

If you have not used the Python interface before, start with
:doc:`python_interface`; that page is the getting-started guide, and the
split between it and this section is depth, not audience. And if what you are
writing is pipeline YAML or a command line rather than Python, you want the
user pages instead — :doc:`grr` for resources and repositories,
:doc:`annotation_infrastructure` for pipelines and annotators. Everything on
this side of that line is Python.

The chapters build bottom-up, substrate before the things that consume it:

1. :doc:`development/resources_in_python` — the resource half of the declared
   API: repositories and resources, reference genomes, gene models, the three
   kinds of score, histograms, and the seam for adding a resource type.
2. :doc:`development/annotators` — the annotation half: what an annotator
   receives and declares, the two classes an annotator is built on, the
   pipeline that drives it, and the entry-point group through which a
   package of your own becomes an annotator a pipeline can name.
3. :doc:`development/module_index` — the complete generated API tree, for when
   you already know the name you are looking for.

.. toctree::
   :maxdepth: 2

   development/resources_in_python
   development/annotators
   development/module_index
