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

from gain.genomic_resources.genomic_scores.chrom_lengths import ChromLength
from gain.genomic_resources.statistics.coverage import (
    COVERAGE_SEGMENT_LENGTHS_IMAGE_FILE,
    COVERAGE_STATISTICS,
    COVERAGE_STATISTICS_FILE,
    CoverageDisplay,
    CoverageStatistics,
    build_coverage_display,
    resolve_chrom_lengths,
)
from gain.genomic_resources.statistics.schema import StatisticsFile

from .base import GenomicScoreImplementation


class PositionScoreImplementation(GenomicScoreImplementation):
    """Assists in the management of a position score resource.

    Its page is the genomic-score page plus a Coverage section, which
    ``position_score.jinja`` fills as the other kinds' templates fill
    theirs.
    """

    template_name: ClassVar[str] = "position_score.jinja"

    def statistics_files(self) -> list[StatisticsFile]:
        return [COVERAGE_STATISTICS]

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
            self.resource,
            self._resolve_labelled_genome(self._render_repo),
            self._score_chrom_lengths,
            coverage.covered_by_chromosome())
        return build_coverage_display(
            self.resource.resource_id, coverage, lengths)

    def _score_chrom_lengths(self) -> dict[str, ChromLength]:
        """The second rung's records: what the score's own file can say.

        Asked only once the genome rung has resolved nothing, so the
        label is not consulted again -- the ladder runs without a
        genome, as it does for an unlabelled score.

        Not asked of a backend whose lengths are never exact.  Nothing
        such a backend says can serve as a denominator, and finding that
        out would open its table: on a tabix score, the index probe per
        contig, at every render -- and ``repo-repair`` renders every
        page (gain#1448).  The probe stays a repair-time cost.  A bigWig
        header is exact, and is the one reason a render opens a table.
        """
        if not self.score.chrom_length_source.is_exact:
            return {}
        return self.get_chrom_lengths(None)
