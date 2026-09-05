""":class:`PositionScoreImplementation` -- the position score's page.

The genomic-score page plus a Coverage section: the accessors that
section calls, and nothing else.  Covered positions are this kind's
statistic and only this kind's -- the union of the rows' spans measures
what a resource covers exactly when they are pairwise disjoint
(gain#1118, gain#1127).  The resource protocol every kind answers alike
is on :class:`~.base.GenomicScoreImplementation`.
"""
from __future__ import annotations

from typing import ClassVar

from gain import logging
from gain.genomic_resources.reference_genome import ReferenceGenome
from gain.genomic_resources.statistics.coverage import (
    COVERAGE_SEGMENT_LENGTHS_IMAGE_FILE,
    COVERAGE_STATISTICS_FILE,
    CoverageDisplay,
    CoverageStatistics,
    build_coverage_display,
    resolve_chrom_lengths,
)
from gain.genomic_resources.utils import read_resource_id_label

from .base import GenomicScoreImplementation

logger = logging.getLogger(__name__)


class PositionScoreImplementation(GenomicScoreImplementation):
    """Assists in the management of a position score resource.

    Its page is the genomic-score page plus a Coverage section, which
    ``position_score.jinja`` fills as the other kinds' templates fill
    theirs.
    """

    template_name: ClassVar[str] = "position_score.jinja"

    @staticmethod
    def get_coverage_segment_lengths_image_filename() -> str:
        """The info page's one statement of the global histogram's path."""
        return COVERAGE_SEGMENT_LENGTHS_IMAGE_FILE

    def get_coverage_statistics(self) -> CoverageStatistics | None:
        """The resource's coverage statistics, or ``None`` if not built.

        Absence is an expected state, not an error: statistics roll out
        lazily as resources are rebuilt (``calc_statistics_hash`` does
        not know about this file), so a resource built before the
        statistic existed simply has nothing to show yet.

        Read on each call, like its two siblings.  It was memoized while
        the Coverage and Fragments sections both read this file; since
        gain#1127 gave fragments a file of their own there is one
        caller, called once per render, and the memo saved nothing.
        """
        try:
            content = self.resource.get_file_content(
                COVERAGE_STATISTICS_FILE)
        except FileNotFoundError:
            return None
        return CoverageStatistics.deserialize(content)

    def get_coverage_display(self) -> CoverageDisplay | None:
        """The Coverage section's payload: raw counts plus fractions.

        ``None`` when the statistic is not built.  This frame's whole
        job is the genome rung of the denominator ladder -- it needs the
        repository handed to the enclosing :meth:`get_info` /
        :meth:`get_statistics_info` call, and the cache it goes through
        is shared with the scan's contig splitting.  Invoked outside a
        page build no repository is available and that rung resolves
        nothing, which degrades to raw counts rather than failing.
        """
        coverage = self.get_coverage_statistics()
        if coverage is None:
            return None
        lengths = resolve_chrom_lengths(
            self.resource, self.score, self._render_genome(),
            coverage.covered_by_chromosome())
        return build_coverage_display(
            self.resource.resource_id, coverage, lengths)

    def _render_genome(self) -> ReferenceGenome | None:
        """The resource's labelled reference genome, at render time.

        A label naming something that is not a genome is a reason to
        degrade to raw counts, not to fail the page build.

        Two ways it can fail to name one, and the guard below only ever
        covered the second.  A value that is not a resource id at all --
        the int, list or dict a free-form ``meta.labels`` allows -- used
        to reach resolution as itself and raise ``TypeError`` past the
        ``except ValueError``, failing the page build this comment says
        must not fail; it is now read as absent and reported by the
        narrowing (gain#1053).  A value that IS an id but names no
        genome still reaches resolution and is caught here.
        """
        genome_id = read_resource_id_label(
            self.resource, "reference_genome")
        try:
            return self._get_reference_genome_cached(
                self._render_repo, genome_id)
        except ValueError:
            logger.warning(
                "reference_genome label %r of %s does not name a genome "
                "resource; ignoring it for coverage fractions",
                genome_id, self.resource.resource_id)
            return None
