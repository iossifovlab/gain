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
2. Annotators — *planned* (:issue:`1144`).
3. :doc:`development/module_index` — the complete generated API tree, for when
   you already know the name you are looking for.

.. attention::

   **Draft marker D1 — the one unwritten chapter.** The numbered list above
   names chapter 2 so the reader sees the shape of the guide, but the
   ``toctree`` below deliberately leaves it out: a ``toctree`` entry for a
   page that does not exist is a build warning, and a placeholder page that
   says "coming soon" would go live on the site the moment this merges.
   Decide which of the three you want here — (a) list-but-don't-link as now,
   (b) a stub page that ships a one-paragraph placeholder, or (c) drop the
   line from the list until :issue:`1144` lands. Option (a) is the draft's
   choice because it publishes nothing that later has to be unpublished.

.. toctree::
   :maxdepth: 2

   development/resources_in_python
   development/module_index

.. Chapter 2 (gain#1144 "Annotators") slots in between the resources chapter
   and the module index when it lands.
