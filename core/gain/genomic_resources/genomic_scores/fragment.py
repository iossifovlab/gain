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

    # A fragment weighs 1 however long it is.
    RECORD_WEIGHT_IS_SPAN: ClassVar[bool] = False

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
    ) -> list[dict[str, ScoreValue]]:
        """Fetch score values for every fragment overlapping a region.

        One dict per overlapping fragment, keyed by score id, as
        :meth:`AlleleScore.fetch_allele_scores()
        <.allele.AlleleScore.fetch_allele_scores>` keys one allele's values --
        the list is per fragment, not per score.  A region no fragment
        overlaps gives ``[]``; unlike the two per-position reads there is no
        ``None``, because several fragments overlapping is the normal case
        and "none of them" is a count of zero rather than absent data.

        ``score_filter`` -- from :meth:`GenomicScore.compile_filter()
        <.base.GenomicScore.compile_filter>` -- drops
        the fragments it rejects, which are then simply not among the dicts.
        It reads the RECORD, so it may name any score the resource defines,
        including one outside ``scores``, and a rejected fragment costs no
        extraction.

        A contig this resource does not have is a different answer: that is
        the caller asking about something that does not exist, and it is
        refused, as the per-position reads refuse it.  Answering ``[]`` would
        make "no fragments here" and "no such contig" indistinguishable.

        A fragment's own span is not reported.  Callers want the values it
        carries; a caller that needs the intervals themselves reads records
        through :meth:`~.base.GenomicScore.fetch_records`.
        """
        if not self.is_open():
            raise ValueError(f"The resource <{self.resource_id}> is not open")
        if chrom not in self.get_all_chromosomes():
            raise ValueError(
                f"{chrom} is not among the available chromosomes.")

        requested_scores = scores or self.get_all_scores()
        score_defs = self._resolve_score_defs(requested_scores)

        records = list(self.fetch_records(
            chrom, start, stop, score_filter=score_filter))
        if not records:
            return []

        return [
            dict(zip(
                requested_scores,
                self.get_score_values_from_record(record, score_defs),
                strict=True))
            for record in records
        ]
