# pylint: disable=W0621,C0114,C0116,W0212,W0613

import pathlib

from gain.genomic_resources import GenomicResource
from gain.genomic_resources.genomic_position_table import (
    TabixGenomicPositionTable,
)
from gain.genomic_resources.genomic_scores import (
    PositionScore,
)
from gain.genomic_resources.repository import GR_CONF_FILE_NAME
from gain.genomic_resources.testing import build_inmemory_test_resource
from gain.genomic_resources.testing.builders import a_grr, a_position_score


def test_the_simplest_position_score() -> None:
    res: GenomicResource = build_inmemory_test_resource({
        GR_CONF_FILE_NAME: """
            type: position_score
            table:
                filename: data.mem
            scores:
              - id: phastCons100way
                type: float
                desc: "The phastCons computed over the tree of 100 \
                       verterbarte species"
                name: s1""",
        "data.mem": """
            chrom  pos_begin  s1
            1      10         0.02
            1      11         0.03
            1      15         0.46
            2      8          0.01
            """,
    })
    assert res.get_type() == "position_score"
    score: PositionScore = PositionScore(res)
    score.open()

    assert score.get_all_scores() == ["phastCons100way"]
    assert score.fetch_position_scores("1", 11) == [0.03]
    assert score.fetch_position_scores("1", 15) == [0.46]
    assert score.fetch_position_scores("2", 8) == [0.01]
    assert score.fetch_position_scores("1", 10) == [0.02]
    assert score.fetch_position_scores("1", 12) is None


def test_region_score() -> None:
    res: GenomicResource = build_inmemory_test_resource({
        GR_CONF_FILE_NAME: """
            type: position_score
            table:
                filename: data.mem
            scores:
              - id: phastCons100way
                type: float
                desc: "The phastCons computed over the tree of 100 \
                       verterbarte species"
                name: s1
              - id: phastCons5way
                type: int
                aggregator: max
                na_values: "-1"
                desc: "The phastCons computed over the tree of 5 \
                       verterbarte species"
                name: s2""",
        "data.mem": """
            chrom  pos_begin  pos_end  s1    s2
            1      10         15       0.02  -1
            1      17         19       0.03  0
            1      22         25       0.46  EMPTY
            2      5          80       0.01  3
            """,
    })
    assert res
    assert res.get_type() == "position_score"
    score = PositionScore(res)
    score.open()

    assert score.table is not None
    assert score.table.chrom_key == 0  # "chrom"
    assert score.table.pos_begin_key == 1  # "pos_begin"
    assert score.table.pos_end_key == 2  # "pos_end"

    assert score.fetch_position_scores("1", 12) == [0.02, None]


def test_phastcons100way() -> None:
    res: GenomicResource = build_inmemory_test_resource({
        GR_CONF_FILE_NAME: """
            type: position_score
            table:
                filename: data.mem
            scores:
              - id: phastCons100way
                type: float
                desc: "The phastCons computed over the tree of 100 \
                       verterbarte species"
                name: phastCons100way
        """,
        "data.mem": """
            chrom  pos_begin  pos_end  phastCons100way
            1      54768      54768    0.002
            1      54769      54771    0.001
            1      54772      54773    0
            1      54774      54774    0.001
            1      54775      54776    0
            1      54777      54780    0.001
            1      54781      54789    0
        """,
    })
    assert res
    assert res.get_type() == "position_score"
    score = PositionScore(res)
    score.open()

    assert score.get_all_scores() == ["phastCons100way"]

    assert score.fetch_position_scores("1", 54773) == [0]


def test_position_score_fetch_region() -> None:
    res: GenomicResource = build_inmemory_test_resource({
        GR_CONF_FILE_NAME: """
            type: position_score
            table:
                filename: data.mem
            scores:
              - id: phastCons100way
                type: float
                desc: "The phastCons computed over the tree of 100 \
                       verterbarte species"
                name: s1
              - id: phastCons5way
                type: int
                aggregator: max
                na_values: "-1"
                desc: "The phastCons computed over the tree of 5 \
                       verterbarte species"
                name: s2""",
        "data.mem": """
            chrom  pos_begin  pos_end  s1    s2
            1      10         15       0.02  -1
            1      17         19       0.03  0
            1      22         25       0.46  EMPTY
            2      5          80       0.01  3
            """,
    })
    score = PositionScore(res).open()

    assert list(score.fetch_region_segment_scores(
            "1", 13, 18, ["phastCons100way"])) == [
        (13, 15, [0.02]),
        (17, 18, [0.03]),
    ]

    assert list(score.fetch_region_segment_scores(
            "1", 13, 18, ["phastCons5way"])) == [
        (13, 15, [None]),
        (17, 18, [0]),
    ]

    scores = ["phastCons5way", "phastCons100way"]
    assert list(score.fetch_region_segment_scores("2", 13, 18, scores)) == [
        (13, 18, [3, 0.01]),
    ]


def test_position_score_chrom_prefix() -> None:
    res: GenomicResource = build_inmemory_test_resource({
        GR_CONF_FILE_NAME: """
            type: position_score
            table:
                filename: data.mem
                chrom_mapping:
                    add_prefix: chr
            scores:
              - id: phastCons100way
                type: float
                desc: "The phastCons computed over the tree of 100 \
                       verterbarte species"
                name: s1""",
        "data.mem": """
            chrom  pos_begin  s1
            1      10         0.02
            1      11         0.03
            1      15         0.46
            2      8          0.01
            """,
    })
    score: PositionScore = PositionScore(res)
    score.open()

    assert score.table is not None
    assert set(score.table.get_chromosomes()) == {"chr1", "chr2"}


def test_a_walk_of_point_reads_leaves_the_tabix_buffer_pruned(
    tmp_path: pathlib.Path,
) -> None:
    # ``fetch_position_scores`` DRAINS the region generator it opens.  An
    # abandoned one leaves ``TabixGenomicPositionTable.get_records_in_region``
    # suspended short of the ``buffer.prune()`` that ends its buffered walk,
    # and abandoning a generator does not tear down what it was iterating --
    # the eviction would wait on a garbage collection that may never come.
    # Annotation reads position after position through here, so the
    # ``LineBuffer`` would grow for the length of a run.
    data = "chrom  pos_begin  pos_end  s\n" + "\n".join(
        f"chr1  {pos}  {pos}  0.1" for pos in range(1, 201))
    resource = (
        a_grr()
        .with_resource(
            "walked",
            a_position_score()
            .with_score("s", "float")
            .with_tabix()
            .with_data(data))
        .build_repo(tmp_path)
        .get_resource("walked")
    )
    score = PositionScore(resource)
    score.open()
    table = score.table
    assert isinstance(table, TabixGenomicPositionTable)

    for pos in range(1, 201):
        assert score.fetch_position_scores("chr1", pos) == [0.1]

    assert len(table.buffer) <= 2
