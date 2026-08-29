# pylint: disable=C0114,C0116,W0212
import pathlib
import re
from collections.abc import Callable

import pytest
from gain.genomic_resources.implementations.genomic_scores_impl import (
    GenomicScoreImplementation,
    build_score_implementation_from_resource,
)
from gain.genomic_resources.repository import GenomicResource
from gain.genomic_resources.testing.builders import (
    a_fragment_score,
    a_position_score,
    an_allele_score,
)

# The Chromosome cell carries a data-sort-value since gain#984, so the
# opening tag must be matched permissively.  Requiring a bare <td> does
# fail when that attribute arrives, but it fails as "the column is
# empty" rather than as "this pattern no longer matches the markup",
# which is a long way from the edit that caused it.
_ROW = re.compile(r"<tr[^>]*>\s*<td[^>]*>([^<]*)</td>")

# Every fixture below carries these three contigs.  They reach the
# stored statistics sorted as plain strings -- chr1, chr10, chr2 -- so a
# page rendering them in this order is rendering the natural key.
_NATURAL_ORDER = ["chr1", "chr2", "chr10", "all chromosomes"]


def _chromosome_column(page: str, heading: str) -> list[str]:
    """The Chromosome column of the table under ``heading``, in order."""
    section = page.split(f"<h2>{heading}</h2>", 1)[1]
    table = section.split("<table>", 1)[1].split("</table>", 1)[0]
    return _ROW.findall(table)


def _built_page(resource: GenomicResource) -> str:
    """The resource's info page, statistics built."""
    GenomicScoreImplementation._do_noregion_histograms(resource)
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

    assert _chromosome_column(page, heading) == _NATURAL_ORDER
