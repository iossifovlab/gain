""":class:`AlleleScoreImplementation` -- the allele score's page.

The genomic-score page plus an Alleles section: the accessors that
section calls, and nothing else.  Whether a statistic reads alleles is
decided on the built score class, by
:func:`~gain.genomic_resources.statistics.alleles.region_alleles_for`,
and both scan paths read it from there; the resource protocol every kind
answers alike is on :class:`~.base.GenomicScoreImplementation`.
"""
from __future__ import annotations

from typing import ClassVar

from gain.genomic_resources.statistics.alleles import (
    ALLELE_COMPLEX_GRID_IMAGE_FILE,
    ALLELE_DELETION_LENGTHS_IMAGE_FILE,
    ALLELE_INSERTION_LENGTHS_IMAGE_FILE,
    ALLELE_STATISTICS_FILE,
    AlleleSectionDisplay,
    AlleleStatistics,
    build_allele_section_display,
)

from .base import GenomicScoreImplementation


class AlleleScoreImplementation(GenomicScoreImplementation):
    """Assists in the management of an allele score resource.

    It carries its own info page, which is the genomic-score page plus an
    Alleles section.  The section lives in a template that FILLS a block
    the shared template leaves empty, as the other kinds' sections do, so
    a kind whose rows carry no ref/alt pair renders no section at all --
    rather than a heading permanently reading "not computed", which is
    what one shared template rendering every section produced.
    """

    template_name: ClassVar[str] = "allele_score.jinja"

    @staticmethod
    def get_allele_insertion_lengths_image_filename() -> str:
        """The info page's one statement of the insertion image's path."""
        return ALLELE_INSERTION_LENGTHS_IMAGE_FILE

    @staticmethod
    def get_allele_deletion_lengths_image_filename() -> str:
        """The info page's one statement of the deletion image's path."""
        return ALLELE_DELETION_LENGTHS_IMAGE_FILE

    @staticmethod
    def get_allele_complex_grid_image_filename() -> str:
        """The info page's one statement of the complex grid's path."""
        return ALLELE_COMPLEX_GRID_IMAGE_FILE

    def get_allele_statistics(self) -> AlleleStatistics | None:
        """The resource's allele statistics, or ``None`` if not built.

        Absence is an expected state, not an error: statistics roll out
        lazily as resources are rebuilt (``calc_statistics_hash`` does
        not know about this file), so a resource built before the
        statistic existed simply has nothing to show yet.
        """
        try:
            content = self.resource.get_file_content(ALLELE_STATISTICS_FILE)
        except FileNotFoundError:
            return None
        return AlleleStatistics.deserialize(content)

    def get_allele_display(self) -> AlleleSectionDisplay | None:
        """The Alleles section's payload, or ``None`` if not built."""
        statistics = self.get_allele_statistics()
        if statistics is None:
            return None
        return build_allele_section_display(statistics)
