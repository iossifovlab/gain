"""The Chromosome lengths section of a genomic score's page (gain#1579).

What the last repair stored in ``statistics/chrom_lengths.json``, laid
out per contig with one column per source, so a reader can put a
tabix score's probed bound beside its genome's true length and see
which of the score's contigs that genome does not list.  Read behind
the same gate the Coverage section reads (gain#1578): a stored file
that is not ``CURRENT`` is never shown, and the render opens no table.
"""

from __future__ import annotations

import pathlib

from gain.genomic_resources.implementations.genomic_scores_impl import (
    GenomicScoreImplementation,
)
from gain.genomic_resources.repository import GenomicResourceRepo
from gain.genomic_resources.testing.info_page_fixtures import (
    COVERAGE_RESOURCE_ID,
    a_coverage_repo,
)

from .info_page_html import table_after
from .test_cli_stats_chrom_lengths import resource_stats
from .test_genomic_scores_impl_derived_files import resynced

HEADING = "<h2>Chromosome lengths</h2>"


def _repaired(
    where: pathlib.Path, resource_id: str,
) -> tuple[GenomicScoreImplementation, GenomicResourceRepo]:
    """The resource repaired -- statistics and stored lengths -- and a
    fresh view of the repository as it is on disk now."""
    resource_stats(where, resource_id)
    return resynced(where, resource_id)


def test_a_repaired_labelled_tabix_score_lists_every_source_per_contig(
    tmp_path: pathlib.Path,
) -> None:
    """The coverage fixture: a tabix score over chr1, chr2 and chr10
    labelled with a genome that lists chr1 (100) and chr2 (50).  Its
    file holds the genome's lengths and the probe's bounds; the table
    shows both, contigs in table order, sources best first."""
    a_coverage_repo(tmp_path)
    impl, repo = _repaired(tmp_path, COVERAGE_RESOURCE_ID)

    table = table_after(impl.get_info(repo=repo), HEADING)

    assert table.text == [
        ["Chromosome", "reference_genome", "tabix_estimate"],
        ["chr1", "100", "12"],
        ["chr2", "50", "12"],
        ["chr10", "", "3"],
    ]
