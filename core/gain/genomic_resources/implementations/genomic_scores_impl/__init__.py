"""The genomic-score resource implementations, and the scan behind them.

Laid out the way :mod:`gain.genomic_resources.genomic_scores` is: a
kind-neutral base, one module per kind, and a factory -- with the
statistics machinery beside them in a module of its own.

* :mod:`.base` -- :class:`~.base.GenomicScoreImplementation`, what every
  kind answers alike: the task-graph wiring that schedules :mod:`.scan`'s
  functions, the resource file set, the hashes, and the page protocol.
* :mod:`.allele` and :mod:`.fragment` -- the kinds.  Each names the
  template that fills its section of the page and defines the accessors
  that section calls, and nothing else.
* :mod:`.builders` -- the factory that picks a kind from a resource's
  type, as the entry points do from a type name.
* :mod:`.scan` -- the statistics machinery.  Module-level functions that
  read a region and reduce it: the per-record and vectorized scans, the
  eligibility gates that choose between them, the task bodies, and the
  merge-and-save step.  None of it needs an implementation object; it
  reads a resource and returns a result, which is what a task body should
  be.  This is the half carrying the numeric and task history (gain#794,
  gain#857), which is why it kept the original file's blame (gain#1007);
  the base kept the class's for the same reason (gain#1210).  Its
  ``__all__`` states the surface, in tiers.

The dependency runs one way: the classes import ``scan``, never the
reverse, which ``tests/test_architecture.py`` pins from the AST.

This module is the facade, and the names below are a published surface:
four ``core/pyproject.toml`` entry points name this package path rather
than a module directly, so the layout stays rearrangeable and a later
split cannot break them.  ``test_genomic_scores_impl_facade.py`` pins
what they promise.
"""
from .allele import AlleleScoreImplementation
from .base import GenomicScoreImplementation
from .builders import build_score_implementation_from_resource
from .fragment import FragmentScoreImplementation

__all__ = [
    "AlleleScoreImplementation",
    "FragmentScoreImplementation",
    "GenomicScoreImplementation",
    "build_score_implementation_from_resource",
]
