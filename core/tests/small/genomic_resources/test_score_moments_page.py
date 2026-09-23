# pylint: disable=C0114,C0116,W0212
"""The ``Summary`` cell a score's info page renders beside its range: ``n``,
``mean`` and ``sd`` read off the histogram's accumulators (gain#1589), one
labelled line each (gain#1617)."""
import json
import pathlib
from typing import Any

import pytest
from gain.gene_scores.gene_scores import GeneScore
from gain.genomic_resources.genomic_scores import (
    AlleleScore,
    FragmentScore,
    PositionScore,
)
from gain.genomic_resources.repository import GenomicResource
from gain.genomic_resources.repository_factory import (
    build_resource_implementation,
)
from gain.genomic_resources.score_resource import ScoreResource
from gain.genomic_resources.statistics.moments import (
    MOMENT_KEYS,
    MomentsSummary,
)
from gain.genomic_resources.testing.builders import (
    a_fragment_score,
    a_gene_score,
    a_position_score,
    an_allele_score,
)
from gain.genomic_resources.testing.statistics import publish_statistics

from tests.small.genomic_resources.info_page_html import (
    section_after,
    table_after,
)

SCORES_HEADING = "<h2>Scores (1)</h2>"

#: Each family with the values its histogram folds and what it weighs them
#: by: the position score's first row spans two base pairs, so its 1.0
#: counts twice -- n is 3, the mean 2 and the population sd sqrt(2).
#: The other kinds count a row once.
_KINDS: list[tuple[type[ScoreResource], Any, str, MomentsSummary, str]] = [
    (
        PositionScore,
        a_position_score().with_score("s", "float").with_data("""
            chrom  pos_begin  pos_end  s
            chr1   1          2        1.0
            chr1   3          3        4.0
        """).with_tabix(),
        "[1, 4]", (("n", "3"), ("mean", "2"), ("sd", "1.41")),
        "base pairs",
    ),
    (
        AlleleScore,
        an_allele_score().with_score("s", "float").with_data("""
            chrom  pos_begin  reference  alternative  s
            chr1   10         A          G            0.1
            chr1   10         A          C            0.3
        """).with_tabix(),
        "[0.1, 0.3]", (("n", "2"), ("mean", "0.2"), ("sd", "0.1")),
        "alleles",
    ),
    (
        FragmentScore,
        a_fragment_score().with_score("s", "float").with_data("""
            chrom  pos_begin  pos_end  s
            chr1   10         100      0.5
            chr1   200        300      1.5
        """).with_tabix(),
        "[0.5, 1.5]", (("n", "2"), ("mean", "1"), ("sd", "0.5")),
        "fragments",
    ),
    (
        GeneScore,
        a_gene_score().with_score("s", "float").with_data("""
            gene  s
            G1    1.0
            G2    3.0
        """),
        "[1, 3]", (("n", "2"), ("mean", "2"), ("sd", "1")),
        "genes",
    ),
]
_IDS = [kind.__name__ for kind, *_ in _KINDS]


def _built(tmp_path: pathlib.Path, builder: Any) -> GenomicResource:
    resource = builder.build_resource(tmp_path)
    publish_statistics(resource)
    return resource


def _page(resource: GenomicResource) -> str:
    return build_resource_implementation(resource).get_info()


@pytest.mark.parametrize(
    ("kind", "builder", "domain", "lines", "unit"), _KINDS, ids=_IDS)
def test_the_scores_table_shows_a_labelled_summary_beside_the_range(
    tmp_path: pathlib.Path,
    kind: type[ScoreResource], builder: Any, domain: str,
    lines: MomentsSummary, unit: str,
) -> None:
    page = _page(_built(tmp_path, builder))

    table = table_after(page, SCORES_HEADING)
    header, row = table.text
    # The last two columns, so the cell sits after Range and nothing
    # else was appended beyond it.
    assert header[-2:] == ["Range", "Summary"], kind
    assert row[-2] == domain, kind
    # The Summary cell's own labelled lines, in order.
    assert table.rows[0][-1].terms == lines, kind
    # The class the stylesheet lays the lines out by.
    assert '<dl class="moments">' in section_after(page, SCORES_HEADING)
    # The footnote says what n counts, in the family's own noun.
    assert f"n counts {unit}" in section_after(page, SCORES_HEADING)


@pytest.mark.parametrize(("kind", "unit"), [
    (kind, unit) for kind, _, _, _, unit in _KINDS], ids=_IDS)
def test_each_family_names_the_unit_its_histogram_counts_in(
    kind: type[ScoreResource], unit: str,
) -> None:
    # Read off the family's OWN namespace, so a family inheriting a
    # sibling's word passes nothing.
    assert vars(kind)["HISTOGRAM_COUNT_UNIT"] == unit


def test_the_cell_is_empty_when_the_histogram_predates_the_accumulators(
    tmp_path: pathlib.Path,
) -> None:
    resource = _built(tmp_path, _KINDS[0][1])
    filename = "statistics/histogram_s.json"
    stored = json.loads(resource.get_file_content(filename))
    for key in MOMENT_KEYS:
        del stored[key]
    with resource.open_raw_file(filename, mode="wt") as out:
        out.write(json.dumps(stored))

    _, row = table_after(_page(resource), SCORES_HEADING).text

    assert row[-2:] == ["[1, 4]", ""]


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

    _, row = table_after(_page(resource), SCORES_HEADING).text

    assert row[-1] == ""
