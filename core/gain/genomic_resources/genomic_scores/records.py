"""Batch array types and the region/record algebra over them.

The half of the score layer that knows nothing about score resources: the
shape of a read batch (:data:`RecordArrays`, :class:`AlleleRecordArrays`)
and the five functions that decide which part of a record a region gets,
or whether it gets it at all.

Three of those partition different things and are deliberately
neighbours -- :func:`clip_span` partitions POSITIONS, :func:`owns_record`
partitions RECORDS by where they BEGIN, and :func:`overlap_fractions_admit`
selects RECORDS by how much of the region, or of themselves, the two share --
so that the halves of the algebra have one home and a caller picks the one it
means.

Nothing here imports a score class. That is a property of this module, not
yet a saving for its callers: the scan and the statistics layer still reach
these names through the package facade, which imports every submodule, so
:class:`~.base.GenomicScore` is loaded either way. What it buys is that
those callers CAN be pointed at ``genomic_scores.records`` directly, one at
a time, without anything else moving -- migrating them was out of scope for
the gain#902 split that created this module.
"""

from __future__ import annotations

from collections.abc import Generator, Iterator
from typing import (
    NamedTuple,
)

import numpy as np

#: One batch as :meth:`GenomicScore.fetch_region_value_arrays()
#: <.base.GenomicScore.fetch_region_value_arrays>` produces it:
#: the RAW one-based begin and end columns, plus one parsed value array per
#: requested score id.  Named because the vectorized scan validators are
#: transducers over a stream of these.
RecordArrays = tuple[np.ndarray, np.ndarray, dict[str, np.ndarray]]


class AlleleRecordArrays(NamedTuple):
    """One batch as :meth:`AlleleScore.fetch_region_allele_arrays()
    <.allele.AlleleScore.fetch_region_allele_arrays>` makes it.

    :data:`RecordArrays` widened by the two key columns an allele row has and
    a position row does not.  The first three fields are that tuple exactly,
    in the same order, so ``batch[:3]`` **is** a ``RecordArrays``.

    That slice is required, not decorative: every consumer of the shared read
    unpacks three names (``validate_record_arrays`` and the scan's coverage
    accumulator among them), and handing one of them a batch of five raises
    ``too many values to unpack``.  A caller feeding this read into machinery
    written for the shared one passes ``batch[:3]``, and mypy says so too --
    this type is not a ``RecordArrays``.

    ``reference`` and ``alternative`` are the cells **as stored** -- see the
    fetch method for why they are the one part of a batch that is not parsed.
    """

    pos_begin: np.ndarray
    pos_end: np.ndarray
    values: dict[str, np.ndarray]
    reference: np.ndarray
    alternative: np.ndarray


def _key_column_array(
    cells: dict[int, np.ndarray], key: int | None, length: int,
) -> np.ndarray:
    """One key column of a batch, or a column of ``None`` if it has none.

    An undeclared ``reference``/``alternative`` yields an array of ``None``
    rather than nothing at all, because that is what the record read yields
    for it -- :func:`build_tabular_parser` puts ``None`` in the record when
    the key is ``None``.  Handing back no array instead would make the two
    reads disagree in shape for a resource they agree about row by row, and
    would put the check for it in every consumer.
    """
    if key is None:
        return np.full(length, None, dtype=object)
    return cells[key]


def clip_span(
    rec_begin: int, rec_end: int,
    pos_begin: int | None, pos_end: int | None,
) -> tuple[int, int] | None:
    """Clip a record's span to a queried window: skip, clip, or refuse.

    Returns the part of ``[rec_begin, rec_end]`` inside
    ``[pos_begin, pos_end]``, where a ``None`` bound means unbounded on
    that side, or ``None`` for a record with no part inside the region:
    one ending before it (the skip) or one starting past it (which
    naive clipping would turn into an inverted span, whose width as a
    weight is negative).
    """
    if pos_begin is not None and rec_end < pos_begin:
        return None
    left = max(pos_begin, rec_begin) if pos_begin is not None else rec_begin
    right = min(pos_end, rec_end) if pos_end is not None else rec_end
    if left > right:
        return None
    return (left, right)


def owns_record(begin: int, start: int | None, end: int | None) -> bool:
    """Whether a region owns a record, by where that record BEGINS.

    The scan's partition of RECORDS, and the one statement of it
    (gain#816).  Ownership is total, unique and reachable: the regions
    tile a contig contiguously from position 1, so every record's begin
    falls in exactly one of them, and the owning region's query always
    returns it -- ``begin`` inside ``[start, end]`` is by itself an
    overlap.  An unbounded side owns everything on that side, which is
    the same rule with one region.

    Contrast :func:`clip_span`, which partitions POSITIONS.  A statistic
    that sums over records wants this; one that unions positions wants
    that.  Both live here so the two halves of the algebra have one home.
    """
    return (start is None or begin >= start) \
        and (end is None or begin <= end)


def owned_records_mask(
    pos_begin: np.ndarray,
    start: int | None,
    end: int | None,
) -> np.ndarray:
    """:func:`owns_record` over a whole batch's begin column."""
    keep = np.ones(pos_begin.shape[0], dtype=bool)
    if start is not None:
        keep &= pos_begin >= start
    if end is not None:
        keep &= pos_begin <= end
    return keep


def clip_to_region[T](
    segments: Iterator[tuple[int, int, T]],
    pos_begin: int | None,
    pos_end: int | None,
) -> Generator[tuple[int, int, T], None, None]:
    """Clip a segment stream to a region, dropping what falls outside."""
    for begin, end, payload in segments:
        span = clip_span(begin, end, pos_begin, pos_end)
        if span is not None:
            yield (span[0], span[1], payload)


def overlap_fractions_admit(
    rec_begin: int, rec_end: int,
    start: int, end: int,
    min_region_fraction: float | None,
    min_fragment_fraction: float | None,
) -> bool:
    """Whether a record overlaps a region by enough of either side.

    With *overlap* the length of the intersection, ``min_region_fraction``
    is ``overlap / region_length`` -- "the record covers at least this much
    of MY region" -- and ``min_fragment_fraction`` is
    ``overlap / record_length`` -- "at least this much of the RECORD falls
    in my region".  The two answer different questions: a 10 bp record
    inside a 1 Mb region scores ~0.00001 on the first and 1.0 on the second.

    Every threshold supplied must hold, and each is compared with ``>=``, so
    ``1.0`` means full containment of the side it is about.  Both ``None``
    admits everything -- including a record with no overlap at all, which is
    a backend answering a region query with a record outside it rather than
    a question this predicate was asked to decide.

    A SELECTION predicate, not a reshaping one: it says whether the record
    is answered, never what span is reported for it (ADR 0008).
    """
    if min_region_fraction is None and min_fragment_fraction is None:
        return True
    span = clip_span(rec_begin, rec_end, start, end)
    overlap = 0 if span is None else span[1] - span[0] + 1
    return (
        (min_region_fraction is None
         or overlap / (end - start + 1) >= min_region_fraction)
        and
        (min_fragment_fraction is None
         or overlap / (rec_end - rec_begin + 1) >= min_fragment_fraction)
    )
