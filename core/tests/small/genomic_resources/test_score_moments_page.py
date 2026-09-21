# pylint: disable=C0114,C0116,W0212
"""The ``n / mean / sd`` cell a score's info page renders beside its range,
read off the histogram's accumulators (gain#1589)."""
import json
import pathlib
from typing import Any

import pytest
from gain.genomic_resources.cli import cli_manage
from gain.genomic_resources.repository import GenomicResource
from gain.genomic_resources.repository_factory import (
    build_resource_implementation,
)
from gain.genomic_resources.testing.builders import (
    a_fragment_score,
    a_gene_score,
    a_position_score,
    an_allele_score,
)

from tests.small.genomic_resources.info_page_html import (
    section_after,
    table_after,
)

SCORES_HEADING = "<h2>Scores (1)</h2>"

#: Each kind with the values its histogram folds and what it weighs them
#: by: the position score's first row spans two base pairs, so its 1.0
#: counts twice -- n is 3, the mean 2 and the population sd sqrt(2).
#: The other kinds count a row once.
_KINDS: list[tuple[str, Any, str, str, str]] = [
    (
        "position",
        a_position_score().with_score("s", "float").with_data("""
            chrom  pos_begin  pos_end  s
            chr1   1          2        1.0
            chr1   3          3        4.0
        """).with_tabix(),
        "[1, 4]", "3 / 2 / 1.41",
        "base pairs",
    ),
    (
        "allele",
        an_allele_score().with_score("s", "float").with_data("""
            chrom  pos_begin  reference  alternative  s
            chr1   10         A          G            0.1
            chr1   10         A          C            0.3
        """).with_tabix(),
        "[0.1, 0.3]", "2 / 0.2 / 0.1",
        "alleles",
    ),
    (
        "fragment",
        a_fragment_score().with_score("s", "float").with_data("""
            chrom  pos_begin  pos_end  s
            chr1   10         100      0.5
            chr1   200        300      1.5
        """).with_tabix(),
        "[0.5, 1.5]", "2 / 1 / 0.5",
        "fragments",
    ),
    (
        "gene",
        a_gene_score().with_score("s", "float").with_data("""
            gene  s
            G1    1.0
            G2    3.0
        """),
        "[1, 3]", "2 / 2 / 1",
        "genes",
    ),
]


def _built(tmp_path: pathlib.Path, builder: Any) -> GenomicResource:
    resource = builder.build_resource(tmp_path)
    cli_manage(["repo-stats", "-R", str(tmp_path), "-j", "1"])
    return resource


def _page(resource: GenomicResource) -> str:
    return build_resource_implementation(resource).get_info()


@pytest.mark.parametrize(
    ("kind", "builder", "domain", "cell", "unit"),
    _KINDS, ids=[kind for kind, *_ in _KINDS])
def test_the_scores_table_shows_n_mean_sd_beside_the_range(
    tmp_path: pathlib.Path,
    kind: str, builder: Any, domain: str, cell: str, unit: str,
) -> None:
    page = _page(_built(tmp_path, builder))

    table = table_after(page, SCORES_HEADING)
    # Whole rows: the cell must sit in the last column, after Range,
    # and nothing else about the row may move to make room for it.
    range_index = table.head[0].index(next(
        head for head in table.head[0] if head.text == "Range"))
    assert [head.text for head in table.head[0][range_index:]] == [
        "Range", "n / mean / sd"]
    (row,) = table.rows
    assert [c.text for c in row[range_index:]] == [domain, cell], kind
    # The footnote says what n counts, per kind.
    assert f"n counts {unit}" in section_after(page, SCORES_HEADING)


def test_the_cell_is_empty_when_the_histogram_predates_the_accumulators(
    tmp_path: pathlib.Path,
) -> None:
    resource = _built(tmp_path, _KINDS[0][1])
    filename = "statistics/histogram_s.json"
    stored = json.loads(resource.get_file_content(filename))
    for key in ("count", "sum", "sum_of_squares"):
        del stored[key]
    with resource.proto.open_raw_file(resource, filename, mode="wt") as out:
        out.write(json.dumps(stored))

    page = _page(resource)

    (row,) = table_after(page, SCORES_HEADING).rows
    assert [cell.text for cell in row[-2:]] == ["[1, 4]", ""]
    # Nothing in the column, so nothing to footnote.
    assert "n counts" not in section_after(page, SCORES_HEADING)


def test_a_categorical_score_renders_an_empty_cell(
    tmp_path: pathlib.Path,
) -> None:
    resource = _built(
        tmp_path,
        an_allele_score().with_score("s", "str").with_data("""
            chrom  pos_begin  reference  alternative  s
            chr1   10         A          G            x
            chr1   10         A          C            y
        """).with_tabix())

    page = _page(resource)

    (row,) = table_after(page, SCORES_HEADING).rows
    assert row[-1].text == ""
    assert "n counts" not in section_after(page, SCORES_HEADING)
