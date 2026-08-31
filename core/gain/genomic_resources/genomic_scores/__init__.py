"""Genomic score resources: the base class, its three kinds, and the algebra.

This package was one 3128-line module until gain#902 split it along the seams
the code already had:

- :mod:`.records` -- the batch array types and the region/record algebra over
  them; imports no score class, so the scan and the statistics layer can use
  it without pulling :class:`.GenomicScore` in behind it
- :mod:`.base` -- :class:`GenomicScore`, everything the kinds share
- :mod:`.position`, :mod:`.allele`, :mod:`.fragment` -- one module per kind
- :mod:`.builders` -- the eight factories, and the dispatch between kinds

**This module is a permanent facade, not a deprecation shim.**  It re-exports
the entire pre-split public surface, so every
``from gain.genomic_resources.genomic_scores import <name>`` written before
the split keeps working, verbatim and indefinitely.  Deep imports from the
submodules are allowed and equivalent, but no caller is expected to migrate
to them: gpf imports through here, and so do out-of-tree callers this
repository cannot see or fix (``grr_bench``, demo repositories).  The
promise is pinned by ``test_genomic_scores_facade.py``, which spells the
surface out rather than deriving it from ``__all__``.

What did NOT move here is the resource *implementation* --
``genomic_scores_impl`` -- whose own split is gain#1007, nor the
decomposition of the :class:`GenomicScore` class itself, which is gain#1027
and is deliberately sequenced after this split.
"""
from .base import (
    DEFAULT_VALUE_ARRAYS_BATCH_SIZE,
    AlleleScore,
    FragmentScore,
    GenomicScore,
    PositionScore,
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
from .records import (
    AlleleRecordArrays,
    RecordArrays,
    clip_span,
    clip_to_region,
    owned_records_mask,
    owns_record,
)

__all__ = [
    "DEFAULT_VALUE_ARRAYS_BATCH_SIZE",
    "AlleleRecordArrays",
    "AlleleScore",
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
    "owned_records_mask",
    "owns_record",
]
