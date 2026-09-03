"""Genomic score resources: the base class, its three kinds, and the algebra.

This package was one 3128-line module until gain#902 split it along the seams
the code already had:

- :mod:`.records` -- the batch array types and the region/record algebra over
  them; imports no score class, so the scan and the statistics layer can use
  it without pulling :class:`~.base.GenomicScore` in behind it
- :mod:`.base` -- :class:`~.base.GenomicScore`, everything the kinds share
- :mod:`.position`, :mod:`.allele`, :mod:`.fragment` -- one module per kind
- :mod:`.builders` -- the eight factories, and the dispatch between kinds

and decomposing the class itself (gain#1027) has since added two more:

- :mod:`.aggregation` (gain#1074) -- the machinery
  :meth:`~.base.GenomicScore.aggregate_region` orchestrates; knows no score
  class, and is handed the per-kind weight rule rather than reading it
- :mod:`.value_extraction` (gain#1114) -- the two decisions
  :meth:`~.base.GenomicScore.open` takes about how a record's cell becomes a
  value: which extractor reads the payload, and which payload column each
  score def is addressed to

**This module is a permanent facade, not a deprecation shim.**  It
re-exports every name the pre-split module DEFINED, so each
``from gain.genomic_resources.genomic_scores import <name>`` written before
the split keeps working, verbatim and indefinitely.  Deep imports from the
submodules are allowed and equivalent, but no caller is expected to migrate
to them: gpf imports through here, and so do out-of-tree callers this
repository cannot see or fix (``grr_bench``, demo repositories).  The
promise is pinned by ``test_genomic_scores_facade.py``, which spells the
surface out rather than deriving it from ``__all__``.

What it deliberately does not carry is the ~70 names the pre-split module
merely IMPORTED and so leaked as incidental re-exports -- ``np``, ``copy``,
``Record``, ``GenomicScoreDef``, ``ScoreValue`` and the like.  Reaching
through this module for one of those worked by accident, never by
intention, and an AST scan of gain, gpf and ``grr_bench`` (2026-08-31)
found no caller that did.  Import them from the module that defines them.

What did NOT move here is the resource *implementation* --
``genomic_scores_impl`` -- whose own split is gain#1007, nor the
decomposition of the :class:`~.base.GenomicScore` class itself, which is
gain#1027 and is deliberately sequenced after this split.
"""
from .allele import AlleleScore
from .base import (
    DEFAULT_VALUE_ARRAYS_BATCH_SIZE,
    GenomicScore,
)
from .builders import (
    build_allele_score_from_resource,
    build_allele_score_from_resource_id,
    build_fragment_score_from_resource,
    build_fragment_score_from_resource_id,
    build_position_score_from_resource,
    build_position_score_from_resource_id,
    build_score_from_resource,
    build_score_from_resource_id,
)
from .fragment import FragmentAggregate, FragmentScore
from .position import PositionScore
from .records import (
    AlleleRecordArrays,
    RecordArrays,
    clip_span,
    clip_to_region,
    overlap_fractions_admit,
    owned_records_mask,
    owns_record,
)

__all__ = [
    "DEFAULT_VALUE_ARRAYS_BATCH_SIZE",
    "AlleleRecordArrays",
    "AlleleScore",
    "FragmentAggregate",
    "FragmentScore",
    "GenomicScore",
    "PositionScore",
    "RecordArrays",
    "build_allele_score_from_resource",
    "build_allele_score_from_resource_id",
    "build_fragment_score_from_resource",
    "build_fragment_score_from_resource_id",
    "build_position_score_from_resource",
    "build_position_score_from_resource_id",
    "build_score_from_resource",
    "build_score_from_resource_id",
    "clip_span",
    "clip_to_region",
    "overlap_fractions_admit",
    "owned_records_mask",
    "owns_record",
]
