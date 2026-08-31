# pylint: disable=C0114,C0116,W0212
import pathlib
from collections.abc import Callable

import pytest
from gain.genomic_resources.implementations.genomic_scores_impl import (
    build_score_implementation_from_resource,
    scan,
)
from gain.genomic_resources.repository import GenomicResource
from gain.genomic_resources.testing.builders import (
    a_fragment_score,
    a_position_score,
    an_allele_score,
)

from tests.small.genomic_resources.info_page_html import table_after

# Every fixture below carries these three contigs.  They reach the
# stored statistics sorted as plain strings -- chr1, chr10, chr2 -- so a
# page rendering them in this order is rendering the natural key.
_NATURAL_ORDER = ["chr1", "chr2", "chr10", "all chromosomes"]


def _built_page(resource: GenomicResource) -> str:
    """The resource's info page, statistics built."""
    scan.do_noregion_histograms(resource)
    return build_score_implementation_from_resource(resource).get_info()


def _position_score(tmp_path: pathlib.Path) -> GenomicResource:
    return (
        a_position_score()
        .with_score("score", "float")
        .with_data(
            """
            chrom  pos_begin  pos_end  score
            chr1   1          5        0.1
            chr2   1          5        0.2
            chr10  1          5        0.3
            """)
        .with_tabix()
        .build_resource(tmp_path)
    )


def _allele_score(tmp_path: pathlib.Path) -> GenomicResource:
    return (
        an_allele_score()
        .with_score("score", "float")
        .with_data(
            """
            chrom  pos_begin  reference  alternative  score
            chr1   10         A          G            0.1
            chr2   10         A          C            0.2
            chr10  10         A          T            0.3
            """)
        .with_tabix()
        .build_resource(tmp_path)
    )


def _fragment_score(tmp_path: pathlib.Path) -> GenomicResource:
    return (
        a_fragment_score()
        .with_score("s", "float")
        .with_data(
            """
            chrom  pos_begin  pos_end  s
            chr1   1          5        0.1
            chr2   1          5        0.2
            chr10  1          5        0.3
            """)
        .with_tabix()
        .build_resource(tmp_path)
    )


@pytest.mark.parametrize(("build_resource", "heading"), [
    (_position_score, "Coverage"),
    (_allele_score, "Alleles"),
    (_fragment_score, "Fragments"),
])
def test_per_chromosome_rows_render_in_natural_order(
    tmp_path: pathlib.Path,
    build_resource: Callable[[pathlib.Path], GenomicResource],
    heading: str,
) -> None:
    page = _built_page(build_resource(tmp_path))

    table = table_after(page, f"<h2>{heading}</h2>")

    # The tfoot total is read back in deliberately.  It is not a data row,
    # but the order this test is about is the order a reader sees, and that
    # ends with "all chromosomes" -- stopping at the data rows would no
    # longer notice a total floated into the middle of them.
    assert [
        row[0].text for row in table.rows + table.foot] == _NATURAL_ORDER
