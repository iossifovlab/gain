""":class:`FragmentScore` -- one value per genomic interval.

The kind whose records span a region rather than a point, and the one that
still answers to a legacy resource-type spelling; recognising that spelling
announces it through
:func:`~gain.genomic_resources.resource_types.warn_deprecated_spelling`.
"""

from __future__ import annotations

import copy
from collections.abc import Generator, Iterator
from typing import (
    Any,
    ClassVar,
)

import numpy as np

from gain import logging
from gain.genomic_resources.genomic_position_table.record import (
    Record,
)
from gain.genomic_resources.repository import (
    GenomicResource,
)
from gain.genomic_resources.resource_errors import (
    backwards_records_error,
)
from gain.genomic_resources.resource_types import (
    FRAGMENT_SCORE_TYPES,
    LEGACY_FRAGMENT_SCORE_TYPE,
    PREFERRED_FRAGMENT_SCORE_TYPE,
    warn_deprecated_spelling,
)
from gain.genomic_resources.score_def import (
    ScoreValue,
)
from gain.genomic_resources.score_filter import (
    ScoreFilter,
)

from ..aggregators import (
    AGGREGATOR_SCHEMA,
)
from .base import GenomicScore
from .records import RecordArrays

logger = logging.getLogger(__name__)


class FragmentScore(GenomicScore):
    """A genomic score over fragments -- intervals carrying attributes.

    Nothing here is copy-number specific; a CNV collection is one
    application of it.  Accepts either resource type in
    :data:`~gain.genomic_resources.resource_types.FRAGMENT_SCORE_TYPES`,
    warning once per resource on the deprecated one.
    """

    # As AlleleScore, except that strings join rather than list -- a fragment
    # score's string attributes are rendered into one cell.  Owned by the
    # score class, so no score-definition subclass is needed to carry them.
    DEFAULT_AGGREGATORS: ClassVar[dict[str, str | None]] = {
        "float": "max",
        "int": "max",
        "str": "join(,)",
        "bool": None,
    }

    def __init__(self, resource: GenomicResource):
        resource_type = resource.get_type()
        if resource_type not in FRAGMENT_SCORE_TYPES:
            accepted = " or ".join(
                f"'{score_type}'" for score_type in FRAGMENT_SCORE_TYPES)
            raise ValueError(
                "The resource provided to FragmentScore should be of "
                f"{accepted} type, not a '{resource_type}'")
        if resource_type == LEGACY_FRAGMENT_SCORE_TYPE:
            # Warned here, not from the `in FRAGMENT_SCORE_TYPES` membership
            # tests: those also run inside the repository layer's SQL
            # predicate, which would fire the warning on every query rather
            # than on every open.
            #
            # Announced through `warn_deprecated_spelling` rather than
            # logged outright because construction is NOT once per resource:
            # the statistics scan rebuilds the score inside every min/max
            # and histogram task, so a repo-repair over an hg38-scale
            # resource passes here once per region.  Named by full id: a
            # repository may hold several versions of one resource id, each
            # its own directory with its own config to migrate, and the
            # announce-once-per-message rule would otherwise print one line
            # for all of them and name none of them precisely.
            warn_deprecated_spelling(
                logger, "resource type",
                LEGACY_FRAGMENT_SCORE_TYPE, PREFERRED_FRAGMENT_SCORE_TYPE,
                found_in=f"Resource '{resource.get_full_id()}'")
        super().__init__(resource)

    @classmethod
    def record_weight(
        cls,
        left: int,  # ruff: ignore[unused-class-method-argument]
        right: int,  # ruff: ignore[unused-class-method-argument]
    ) -> int:
        """A fragment counts once however long it is.

        The kind's whole reason for weighing by record rather than by span:
        a fragment is a measured thing, not a run of per-base values, so
        its length says nothing about how many times its value counts.

        A constant, which is elementwise: the base's
        :meth:`~.base.GenomicScore.record_weights` fills it out to a
        batch's shape.
        """
        return 1

    @staticmethod
    def get_schema() -> dict[str, Any]:
        schema = copy.deepcopy(GenomicScore.get_schema())
        scores_schema = schema["scores"]["schema"]["schema"]
        scores_schema["aggregator"] = AGGREGATOR_SCHEMA
        return schema

    def validate_records(
        self, records: Iterator[Record],
    ) -> Generator[Record, None, None]:
        """Refuse a fragment that begins before the one before it.

        Fragments overlap freely and several may share a start, so only the
        BEGINS are compared, and only against each other.  A fragment's own end
        takes no part: an interval reaching back over its predecessor is the
        normal case, not a data error.

        The comparison is against RAW spans, the layer at which this rule and
        the vectorized one can ever be stated once (ADR 0008), and it starts
        afresh at every contig: "begins after the one before it" is a claim
        about one contig, and a second contig starting lower than the first
        ended is most resources.
        """
        prev_chrom: str | None = None
        prev_begin: int | None = None
        for record in records:
            chrom, begin, _end = self._record_to_begin_end(record)
            if chrom != prev_chrom:
                prev_begin = None
            if prev_begin is not None and begin < prev_begin:
                raise backwards_records_error(
                    self.resource_id, chrom, begin, prev_begin,
                    "a fragment score's")
            prev_chrom, prev_begin = chrom, begin
            yield record

    def validate_record_arrays(
        self, batches: Iterator[RecordArrays], chrom: str,
    ) -> Generator[RecordArrays, None, None]:
        """Refuse a fragment beginning before the one before it, vectorized.

        The same rule as :meth:`validate_records`, over a batch's columns:
        only the RAW begins are compared, and only against each other.  A
        fragment's own end takes no part -- an interval reaching back over
        its predecessor is the normal case for this kind, not a data error.
        A violation straddling a batch boundary is caught on the carried
        begin.
        """
        prev_begin: int | None = None
        for batch in batches:
            pos_begin, _pos_end, _cells = batch
            if pos_begin.size:
                if prev_begin is not None and int(pos_begin[0]) < prev_begin:
                    raise backwards_records_error(
                        self.resource_id, chrom, int(pos_begin[0]),
                        prev_begin, "a fragment score's")
                backwards = pos_begin[1:] < pos_begin[:-1]
                if bool(backwards.any()):
                    first = int(np.argmax(backwards))
                    raise backwards_records_error(
                        self.resource_id, chrom, int(pos_begin[first + 1]),
                        int(pos_begin[first]), "a fragment score's")
                prev_begin = int(pos_begin[-1])
            yield batch

    def fetch_fragment_scores(
        self, chrom: str,
        start: int, stop: int,
        scores: list[str] | None = None,
        *,
        score_filter: ScoreFilter | None = None,
    ) -> Generator[tuple[int, int, tuple[ScoreValue, ...]], None, None]:
        """Stream ``(begin, end, values)`` for the fragments over a region.

        **Private to the fragment plane.**  This is the primitive the plane's
        public reads are to be built on, not a read to reach for directly; it
        keeps its name because it had one, not because the name is an
        invitation.  It diverges from the internals beside it
        (``_score_segments``, ``_region_read_defs``) in spelling only.

        One entry per overlapping fragment, in table order, each reporting
        the fragment's OWN extent -- unclipped, even where it runs past the
        region asked for.  What a partial overlap means depends on what the
        caller is computing, so ADR 0008 leaves it to them; a caller that
        wants the window intersected composes
        :func:`~.records.clip_span`.

        ``values`` is positional, parallel to ``scores`` as requested (to
        :meth:`~.base.GenomicScore.get_all_scores` when that is ``None``),
        rather than a mapping: the caller already knows what it asked for and
        in what order.  A value may be ``None`` where the record carries no
        value for that score -- unlike the per-position reads, that is the
        only ``None`` here, because a fragment score has no notion of an
        uncovered position.

        ``score_filter`` -- from :meth:`GenomicScore.compile_filter()
        <.base.GenomicScore.compile_filter>` -- drops the fragments it
        rejects, which are then simply not yielded.  It reads the RECORD, so
        it may name any score the resource defines, including one outside
        ``scores``, and a rejected fragment costs no extraction.

        The REQUEST is checked when this is called; the READING is lazy.  A
        closed score, a contig this resource does not have and an unknown
        score id are refused before the first ``next()`` rather than on it,
        for the reason :meth:`~.base.GenomicScore._region_read_defs` gives.
        A malformed RECORD is a different matter and is refused when the
        record is reached: a fragment whose end precedes its begin ends the
        iteration then, mid-stream.

        **One live read at a time.**  A score serves a single region read at
        once -- the table's line iterator and line buffer are the table's, not
        the generator's -- so starting a second read invalidates one that is
        still being consumed, and on a tabix-backed table the two then answer
        each other's records with no error raised.  Materialising is what
        makes a held answer safe to keep:

        .. code-block:: python

            kept = list(score.fetch_fragment_scores(chrom, beg, end))

        Abandoning a read mid-stream is safe and costs only a
        :class:`~gain.genomic_resources.genomic_position_table.table_tabix.TabixGenomicPositionTable`
        buffer prune, which gain#1120 moved into a ``finally`` -- though that
        runs when the generator is released, so a caller holding a reference
        to a ``close()``-ed generator still holds the read open.
        """
        records = self.fetch_records(
            chrom, start, stop, score_filter=score_filter)
        return (
            (beg, end, tuple(values))
            for beg, end, values in self.region_values_from_records(
                records, chrom, start, stop, scores)
        )
