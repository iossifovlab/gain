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

1. :doc:`development/architecture_overview` — the map of GAIn's object model,
   a short trace of what ``annotate_tabular`` does at runtime, and the project
   vocabulary. Every later chapter is a zoom on one box of that map, so if you
   arrived here from a search result, read the map first.
2. Working with resources in Python — *planned* (:issue:`1143`).
3. Annotators — *planned* (:issue:`1144`).
4. :doc:`development/module_index` — the complete generated API tree, for when
   you already know the name you are looking for.

.. attention::

   **Draft marker D1 — the two unwritten chapters.** The numbered list above
   names chapters 2 and 3 so the reader sees the shape of the guide, but the
   ``toctree`` below deliberately leaves them out: a ``toctree`` entry for a
   page that does not exist is a build warning, and a placeholder page that
   says "coming soon" would go live on the site the moment this merges.
   Decide which of the three you want here — (a) list-but-don't-link as now,
   (b) stub pages that ship a one-paragraph placeholder, or (c) drop the two
   lines from the list until :issue:`1143` and :issue:`1144` land. Option (a)
   is the draft's choice because it publishes nothing that later has to be
   unpublished.

.. toctree::
   :maxdepth: 2

   development/architecture_overview
   development/module_index

.. Chapters 2 and 3 (gain#1143 "Working with resources in Python", gain#1144
   "Annotators") slot in between the two entries above when they land.
