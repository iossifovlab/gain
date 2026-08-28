# pylint: disable=C0114,C0116,W0212,W0621
import pathlib
import re

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

_ROW = re.compile(r"<tr[^>]*>\s*<td>([^<]*)</td>")

# chr10 sorts above chr2 in the stored statistics; the page must not.
_UNSORTED_CONTIGS = ["chr1", "chr10", "chr2"]


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


def test_coverage_rows_render_in_natural_chromosome_order(
    tmp_path: pathlib.Path,
) -> None:
    resource = _position_score(tmp_path)

    page = _built_page(resource)

    assert _chromosome_column(page, "Coverage") == [
        "chr1", "chr2", "chr10", "all chromosomes",
    ]


def test_fragment_rows_render_in_natural_chromosome_order(
    tmp_path: pathlib.Path,
) -> None:
    resource = (
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

    page = _built_page(resource)

    assert _chromosome_column(page, "Fragments") == [
        "chr1", "chr2", "chr10", "all chromosomes",
    ]


def test_allele_rows_render_in_natural_chromosome_order(
    tmp_path: pathlib.Path,
) -> None:
    resource = (
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

    page = _built_page(resource)

    assert _chromosome_column(page, "Alleles") == [
        "chr1", "chr2", "chr10", "all chromosomes",
    ]
