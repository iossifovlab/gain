"""The genomic-score resource implementations, and the scan behind them.

Two jobs used to share one 1500-line module, and they answer to different
readers:

* :mod:`.scan` -- the statistics machinery.  Module-level functions that
  read a region and reduce it: the per-record and vectorized scans, the
  eligibility gates that choose between them, the task bodies, and the
  merge-and-save step.  None of it needs an implementation object; it
  reads a resource and returns a result, which is what a task body should
  be.  This is the half carrying the numeric and task history (gain#794,
  gain#857), which is why it kept the original file's blame.
* :mod:`.impl` -- :class:`~.impl.GenomicScoreImplementation` and
  :class:`~.impl.FragmentScoreImplementation`.  The info page's render
  accessors, the task-graph wiring that schedules :mod:`.scan`'s
  functions, the resource file set, and the hashes.

The dependency runs one way: ``impl`` imports ``scan``, never the reverse.

This module is the facade.  Four ``core/pyproject.toml`` entry points name
this package path -- ``position_score`` and ``allele_score`` to
:class:`~.impl.GenomicScoreImplementation`, ``fragment_score`` and the
deprecated ``cnv_collection`` (ADR 0011) to
:class:`~.impl.FragmentScoreImplementation` -- so the three names below are
a published surface, not an internal convenience.  A stale entry point
fails when a repository first opens a resource of that type, not at import,
so ``test_genomic_scores_impl_facade.py`` pins all four.
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
