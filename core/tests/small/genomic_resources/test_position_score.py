# pylint: disable=W0621,C0114,C0116,W0212,W0613

import pathlib

from gain.genomic_resources import GenomicResource
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

    assert list(score.fetch_region_segments(
            "1", 13, 18, ["phastCons100way"])) == [
        (10, 15, [0.02]),
        (17, 19, [0.03]),
    ]

    assert list(score.fetch_region_segments(
            "1", 13, 18, ["phastCons5way"])) == [
        (10, 15, [None]),
        (17, 19, [0]),
    ]

    scores = ["phastCons5way", "phastCons100way"]
    assert list(score.fetch_region_segments("2", 13, 18, scores)) == [
        (5, 80, [3, 0.01]),
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
    # ``fetch_position_scores`` DRAINS the region generator it opens, so the
    # buffered walk reaches the ``buffer.prune()`` that ends it and the
    # annotation path -- which reads position after position through here --
    # does not grow a ``LineBuffer`` across a run.
    #
    # Since gain#1120 the prune runs in a ``finally``, so an ABANDONED walk
    # is pruned too; this pins the drained half.  The abandoned half is
    # test_abandoned_queries_keep_the_buffer_bounded, over in
    # genomic_position_table/test_overlapping_intervals.py.
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

    # ``buffered_record_count`` rather than ``isinstance`` plus
    # ``len(table.buffer)``: the count is what a table owes a caller asking
    # what it retains, and asking it needs no knowledge of which backend has
    # a buffer (gain#1120).
    #
    # The claim of the bound is that the buffer does NOT grow with the
    # walk -- without the drain it reaches the walk's length (200).  On
    # this walk of point records ``LineBuffer.prune``'s cheap leading pop
    # keeps the buffer at a record or two, so 64 is not a description of
    # today's eviction; it is headroom, so that a policy leaning on
    # amortized compaction -- bounded by ``LineBuffer.COMPACT_FLOOR``, 32
    # today, see the rationale in
    # test_prune_evicts_the_dead_records_a_wide_one_spans -- still passes,
    # while a buffer that scales with the reads cannot.
    for pos in range(1, 201):
        assert score.fetch_position_scores("chr1", pos) == [0.1]
        assert table.buffered_record_count() <= 64
