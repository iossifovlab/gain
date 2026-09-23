"""A genomic score's page shows no Chromosome lengths section (gain#1616).

The per-contig table #1579 added was ~200 rows of agreement on a real
score, the disagreements it meant to surface drowned in them, and those
disagreements are a repair-time concern (gain#1575 warns on partial
contig overlap).  The stored ``statistics/chrom_lengths.json`` stays --
coverage and region splitting read it -- so every page here is built
after a repair that wrote it: the section is absent although the data
it used to render is on disk.
"""

from __future__ import annotations

import pathlib
from collections.abc import Callable

import pytest
from gain.genomic_resources.genomic_scores.chrom_lengths import (
    CHROM_LENGTHS_FILE,
)
from gain.genomic_resources.repository import GenomicResourceRepo

from .test_genomic_scores_impl_derived_files import repaired
from .test_info_page_sortable_tables import REPO_BUILDERS


@pytest.mark.parametrize(
    ("resource_id", "build"), REPO_BUILDERS.items(), ids=REPO_BUILDERS)
def test_a_repaired_score_page_has_no_chromosome_lengths_section(
    tmp_path: pathlib.Path,
    resource_id: str,
    build: Callable[[pathlib.Path], GenomicResourceRepo],
) -> None:
    """Every kind's repair stores the lengths; no kind's page lists
    them."""
    build(tmp_path)
    impl, repo = repaired(tmp_path, resource_id)

    page = impl.get_info(repo=repo)

    assert impl.resource.file_exists(CHROM_LENGTHS_FILE)
    assert "<h2>Scores" in page
    assert "<h2>Chromosome lengths</h2>" not in page
