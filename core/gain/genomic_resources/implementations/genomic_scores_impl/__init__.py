"""The genomic-score resource implementations, and the scan behind them.

Two jobs used to share one 1500-line module, and they answer to different
readers:

* :mod:`.scan` -- the statistics machinery.  Module-level functions that
  read a region and reduce it: the per-record and vectorized scans, the
  eligibility gates that choose between them, the task bodies, and the
  merge-and-save step.  None of it needs an implementation object; it
  reads a resource and returns a result, which is what a task body should
  be.  This is the half carrying the numeric and task history (gain#794,
  gain#857), which is why it kept the original file's blame.  Its
  ``__all__`` states the surface, in tiers.
* :mod:`.impl` -- :class:`~.impl.GenomicScoreImplementation` and
  :class:`~.impl.FragmentScoreImplementation`.  The info page's render
  accessors, the task-graph wiring that schedules :mod:`.scan`'s
  functions, the resource file set, and the hashes.  The class still
  answers to two readers -- the templates and the resource protocol --
  and separating those is gain#1037, deliberately not done here.

The dependency runs one way: ``impl`` imports ``scan``, never the reverse,
which ``tests/test_architecture.py`` pins from the AST.

This module is the facade, and the three names below are a published
surface: four ``core/pyproject.toml`` entry points name this package path
rather than :mod:`.impl` directly, so the layout stays rearrangeable and a
later split cannot break them.  ``test_genomic_scores_impl_facade.py``
pins what they promise.
"""
from .impl import (
    FragmentScoreImplementation,
    GenomicScoreImplementation,
    build_score_implementation_from_resource,
)

__all__ = [
    "FragmentScoreImplementation",
    "GenomicScoreImplementation",
    "build_score_implementation_from_resource",
]
