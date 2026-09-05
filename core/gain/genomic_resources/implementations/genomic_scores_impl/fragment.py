""":class:`FragmentScoreImplementation` -- the fragment score's page.

The genomic-score page plus a Fragments section: the accessors that
section calls, and nothing else.  A fragment's weight-1 rule is declared
on ``FragmentScore`` (``record_weight``) and read by both scan paths from
there; the resource protocol every kind answers alike is on
:class:`~.base.GenomicScoreImplementation`.
"""
from __future__ import annotations

from typing import ClassVar

from gain.genomic_resources.statistics.fragments import (
    FRAGMENT_LENGTHS_IMAGE_FILE,
    FRAGMENT_STATISTICS_FILE,
    FragmentDisplay,
    FragmentStatistics,
    build_fragment_display,
)

from .base import GenomicScoreImplementation


class FragmentScoreImplementation(GenomicScoreImplementation):
    """Assists in the management of a fragment score resource.

    Its page is the genomic-score page plus one section, Fragments, which
    only this kind publishes -- and no Coverage, which since gain#1127
    only a position score has.  The section lives in a template that
    FILLS a block the shared template leaves empty, so a kind with no
    fragments renders no section at all -- rather than a heading
    permanently reading "not computed", which is what gating one shared
    template on a boolean produced for Coverage on allele scores.
    """

    template_name: ClassVar[str] = "fragment_score.jinja"

    @staticmethod
    def get_fragment_lengths_image_filename() -> str:
        """The info page's one statement of the fragment image's path."""
        return FRAGMENT_LENGTHS_IMAGE_FILE

    def get_fragment_statistics(self) -> FragmentStatistics | None:
        """The resource's fragment statistics, or ``None`` if not built.

        Absence is an expected state, not an error: statistics roll out
        lazily as resources are rebuilt (``calc_statistics_hash`` does
        not know about this file), so a resource built before the
        statistic existed simply has nothing to show yet.
        """
        try:
            content = self.resource.get_file_content(
                FRAGMENT_STATISTICS_FILE)
        except FileNotFoundError:
            return None
        return FragmentStatistics.deserialize(content)

    def get_fragment_display(self) -> FragmentDisplay | None:
        """The Fragments section's payload, or ``None`` if not computed.

        ``None`` means the statistic is not built -- the file is simply
        absent, which renders the section's "not computed" fallback.
        Since gain#1127 that is the ONE way it can be missing: the tally
        has its own file, where as a group inside the coverage one it
        could also be absent from a file that existed.
        """
        statistics = self.get_fragment_statistics()
        if statistics is None:
            return None
        return build_fragment_display(statistics)
