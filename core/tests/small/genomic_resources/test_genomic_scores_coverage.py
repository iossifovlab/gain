# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""Tests to improve coverage of genomic_scores module."""
import logging

import pytest
from gain.genomic_resources import GenomicResource
from gain.genomic_resources.genomic_position_table.record import (
    ALT,
    CHROM,
    POS_BEGIN,
    POS_END,
    REF,
)
from gain.genomic_resources.genomic_scores import (
    AlleleScore,
    FragmentScore,
    PositionScore,
    build_position_score_from_resource,
    build_score_from_resource,
)
from gain.genomic_resources.repository import GR_CONF_FILE_NAME
from gain.genomic_resources.score_def import (
    GenomicScoreDef,
    extract_column_value,
)
from gain.genomic_resources.testing import build_inmemory_test_resource


def test_score_line_get_score_value_parser_exception(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test the value read when the configured value parser raises.

    The behaviour under test is ``extract_column_value``'s: a value parser
    that raises is logged and yields ``None`` rather than propagating.  Read
    straight off a record -- the score lines that used to wrap one are gone,
    and a score is a cell of the record's payload.
    """

    def bad_parser(value: str) -> float:
        raise ValueError("Parse error")

    raw_row = ("chr1", "1", "10", "invalid")
    score_defs = {
        "test_score": GenomicScoreDef(
            score_id="test_score",
            desc="",
            value_type="float",
            aggregator=None,
            small_values_desc=None,
            large_values_desc=None,
            col_name="score",
            col_index=None,
            hist_conf=None,
            value_parser=bad_parser,
            na_values=None,
        ),
    }
    # score_index is init=False -- GenomicScore.open resolves it from
    # col_name/col_index, and this def is built by hand without one.
    score_defs["test_score"].score_index = 3
    record = ("chr1", 1, 10, None, None, raw_row)

    result = extract_column_value(record, score_defs["test_score"])
    assert result is None
    assert any(
        "unable to parse value" in rec.message
        for rec in caplog.records)


def test_a_retired_aggregator_key_is_refused(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """``allele_aggregator`` no longer names anything.

    It, ``position_aggregator`` and ``nucleotide_aggregator`` collapsed into
    a single ``aggregator``.  The old spellings are not accepted and not
    silently ignored: the schema is strict, so a resource still using one
    fails validation naming the field, which is what tells an author what to
    rename.  (``nucleotide_aggregator`` had been deprecated-with-a-warning in
    favour of ``allele_aggregator``; that ladder is gone with the keys.)
    """
    res: GenomicResource = build_inmemory_test_resource({
        GR_CONF_FILE_NAME: """
            type: allele_score
            table:
                filename: data.mem
                reference:
                  name: reference
                alternative:
                  name: alternative
            scores:
                - id: freq
                  type: float
                  desc: ""
                  name: freq
                  allele_aggregator: max
        """,
        "data.mem": """
            chrom  pos_begin  reference  alternative  freq
            1      10         A          G            0.02
        """,
    })
    with pytest.raises(ValueError, match="Invalid configuration"):
        AlleleScore(res)


def test_default_annotation_attribute() -> None:
    """Test get_default_annotation_attribute with custom names."""
    res: GenomicResource = build_inmemory_test_resource({
        GR_CONF_FILE_NAME: """
            type: position_score
            table:
                filename: data.mem
            scores:
                - id: score1
                  type: float
                  name: score1
                - id: score2
                  type: float
                  name: score2
            default_annotation:
                - source: score1
                  name: custom_name
                - source: score2
        """,
        "data.mem": """
            chrom  pos_begin  score1  score2
            1      10         0.1     0.2
        """,
    })

    score = build_score_from_resource(res)
    score.open()

    attr = score.get_default_annotation_attribute("score1")
    assert attr == "custom_name"

    attr2 = score.get_default_annotation_attribute("score2")
    assert attr2 == "score2"


def test_genomic_score_is_open() -> None:
    """Test is_open() method."""
    res: GenomicResource = build_inmemory_test_resource({
        GR_CONF_FILE_NAME: """
            type: position_score
            table:
                filename: data.mem
            scores:
                - id: score
                  type: float
                  name: score
        """,
        "data.mem": """
            chrom  pos_begin  score
            1      10         0.1
        """,
    })

    score = build_score_from_resource(res)
    assert not score.is_open()
    score.open()
    assert score.is_open()
    score.close()
    assert not score.is_open()


def test_genomic_score_open_already_opened(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test opening an already opened genomic score."""
    caplog.set_level(logging.INFO)

    res: GenomicResource = build_inmemory_test_resource({
        GR_CONF_FILE_NAME: """
            type: position_score
            table:
                filename: data.mem
            scores:
                - id: score
                  type: float
                  name: score
        """,
        "data.mem": """
            chrom  pos_begin  score
            1      10         0.1
        """,
    })

    score = build_score_from_resource(res)
    score.open()
    score.open()  # Open again
    assert any(
        "opening already opened" in rec.message for rec in caplog.records
    )


def test_genomic_score_context_manager_with_exception(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test context manager exit with exception."""
    res: GenomicResource = build_inmemory_test_resource({
        GR_CONF_FILE_NAME: """
            type: position_score
            table:
                filename: data.mem
            scores:
                - id: score
                  type: float
                  name: score
        """,
        "data.mem": """
            chrom  pos_begin  score
            1      10         0.1
        """,
    })

    score = build_score_from_resource(res)
    try:
        with score.open():
            raise RuntimeError("Test exception")  # noqa
    except RuntimeError:
        pass

    assert any(
        "exception while working" in rec.message
        for rec in caplog.records)


def test_position_score_multiple_values_for_position() -> None:
    """Overlapping records read back; the scan is what refuses them."""
    res: GenomicResource = build_inmemory_test_resource({
        GR_CONF_FILE_NAME: """
            type: position_score
            table:
                filename: data.mem
            scores:
                - id: score
                  type: float
                  name: score
        """,
        "data.mem": """
            chrom  pos_begin  pos_end  score
            1      10         15       0.1
            1      12         18       0.2
        """,
    })

    score = PositionScore(res)
    score.open()

    assert list(score.fetch_region_segment_scores("1", 10, 20, ["score"])) == [
        (10, 15, [0.1]),
        (12, 18, [0.2]),
    ]
    with pytest.raises(ValueError, match="multiple values"):
        list(score.validate_records(score.fetch_records("1", 10, 20)))


def test_position_score_fetch_scores_multiple_lines() -> None:
    """fetch_position_scores answers from the first of several lines."""
    res: GenomicResource = build_inmemory_test_resource({
        GR_CONF_FILE_NAME: """
            type: position_score
            table:
                filename: data.mem
            scores:
                - id: score
                  type: float
                  name: score
        """,
        "data.mem": """
            chrom  pos_begin  pos_end  score
            1      10         10       0.1
            1      10         10       0.2
        """,
    })

    score = PositionScore(res)
    score.open()

    assert score.fetch_position_scores("1", 10) == [0.1]
    with pytest.raises(ValueError, match="multiple values"):
        list(score.validate_records(score.fetch_records("1", 10, 10)))


def test_allele_score_invalid_resource_type() -> None:
    """Test AlleleScore with invalid resource type."""
    res: GenomicResource = build_inmemory_test_resource({
        GR_CONF_FILE_NAME: """
            type: position_score
            table:
                filename: data.mem
            scores:
                - id: score
                  type: float
                  name: score
        """,
        "data.mem": """
            chrom  pos_begin  score
            1      10         0.1
        """,
    })

    with pytest.raises(ValueError, match="should be of"):
        AlleleScore(res)


def test_allele_score_mode_from_name_invalid() -> None:
    """Test AlleleScore.Mode.from_name with invalid name."""
    with pytest.raises(ValueError, match="unknown allele mode"):
        AlleleScore.Mode.from_name("invalid_mode")


def test_allele_score_fetch_region_spanning_record() -> None:
    """Test the region read with spanning records (pos_begin != pos_end)."""
    res: GenomicResource = build_inmemory_test_resource({
        GR_CONF_FILE_NAME: """
            type: allele_score
            table:
                filename: data.mem
                reference:
                  name: reference
                alternative:
                  name: alternative
            scores:
                - id: freq
                  type: float
                  desc: ""
                  name: freq
        """,
        "data.mem": """
            chrom  pos_begin  pos_end  reference  alternative  freq
            1      10         15       A          G            0.02
        """,
    })
    score = AlleleScore(res)
    score.open()

    assert list(score.fetch_region_segment_scores("1", 10, 20, ["freq"])) \
        == [(10, 10, [0.02])]
    # The nucleotides come off the record, not the values stream.
    assert [(r[POS_BEGIN], r[REF], r[ALT])
            for r in score.fetch_records("1", 10, 20)] == [(10, "A", "G")]


def test_allele_score_fetch_region_overlapping_positions() -> None:
    """Test the region read with two records at one position."""
    res: GenomicResource = build_inmemory_test_resource({
        GR_CONF_FILE_NAME: """
            type: allele_score
            table:
                filename: data.mem
                reference:
                  name: reference
                alternative:
                  name: alternative
            scores:
                - id: freq
                  type: float
                  desc: ""
                  name: freq
        """,
        "data.mem": """
            chrom  pos_begin  reference  alternative  freq
            1      10         A          G            0.02
            1      10         A          G            0.03
        """,
    })
    score = AlleleScore(res)
    score.open()

    # Two records share position 10 -- the same ref/alt at that -- and both
    # are yielded rather than one being collapsed away.
    result = list(score.fetch_region_segment_scores("1", 10, 11, ["freq"]))
    assert len(result) == 2


def test_fragment_score_invalid_resource_type() -> None:
    """Test FragmentScore with invalid resource type."""
    res: GenomicResource = build_inmemory_test_resource({
        GR_CONF_FILE_NAME: """
            type: position_score
            table:
                filename: data.mem
            scores:
                - id: score
                  type: float
                  name: score
        """,
        "data.mem": """
            chrom  pos_begin  score
            1      10         0.1
        """,
    })

    with pytest.raises(ValueError, match="should be of"):
        FragmentScore(res)


def test_fragment_score_fetch_fragments() -> None:
    """Test FragmentScore.fetch_fragment_scores method."""
    res: GenomicResource = build_inmemory_test_resource({
        GR_CONF_FILE_NAME: """
            type: fragment_score
            table:
                filename: data.mem
            scores:
                - id: cnv_type
                  type: str
                  name: cnv_type
                - id: frequency
                  type: float
                  name: frequency
        """,
        "data.mem": """
            chrom  pos_begin  pos_end  cnv_type  frequency
            1      100        200      DEL       0.01
            1      300        400      DUP       0.02
            2      500        600      DEL       0.03
        """,
    })

    fragment_score = FragmentScore(res)
    fragment_score.open()

    fragments = fragment_score.fetch_fragment_scores("1", 150, 350)
    assert len(fragments) == 2
    assert fragments[0]["cnv_type"] == "DEL"
    assert fragments[0]["frequency"] == 0.01
    # A fragment's own span is read through the records, not the score fetch.
    records = list(fragment_score.fetch_records("1", 150, 350))
    assert (records[0][CHROM], records[0][POS_BEGIN], records[0][POS_END]) \
        == ("1", 100, 200)

    # A region no fragment overlaps is empty ...
    fragments = fragment_score.fetch_fragment_scores("1", 1000, 2000)
    assert len(fragments) == 0

    # ... but a contig the resource does not have is refused, so the two
    # cannot be confused for each other.
    with pytest.raises(ValueError, match="not among the available"):
        fragment_score.fetch_fragment_scores("chr99", 1, 100)


def test_fragment_score_not_open() -> None:
    """Test FragmentScore.fetch_fragment_scores when not opened."""
    res: GenomicResource = build_inmemory_test_resource({
        GR_CONF_FILE_NAME: """
            type: fragment_score
            table:
                filename: data.mem
            scores:
                - id: cnv_type
                  type: str
                  name: cnv_type
        """,
        "data.mem": """
            chrom  pos_begin  pos_end  cnv_type
            1      100        200      DEL
        """,
    })

    fragment_score = FragmentScore(res)

    with pytest.raises(ValueError, match="is not open"):
        fragment_score.fetch_fragment_scores("1", 100, 200)


def test_build_score_from_resource_invalid_type() -> None:
    """Test build_score_from_resource with unsupported resource type."""
    res: GenomicResource = build_inmemory_test_resource({
        GR_CONF_FILE_NAME: """
            type: gene_models
        """,
    })

    with pytest.raises(ValueError, match="is not of score type"):
        build_score_from_resource(res)


def test_validate_scoredefs_column_name_not_in_header() -> None:
    """Test scoredef validation when column_name is not in header."""
    res: GenomicResource = build_inmemory_test_resource({
        GR_CONF_FILE_NAME: """
            type: position_score
            table:
                filename: data.mem
                header:
                    - chrom
                    - pos_begin
                    - score1
            scores:
                - id: score
                  type: float
                  column_name: nonexistent_column
        """,
        "data.mem": """
            1  10  0.1
        """,
    })

    score = PositionScore(res)
    with pytest.raises(AssertionError):
        score.open()


def test_validate_scoredefs_column_index_out_of_bounds() -> None:
    """Test scoredef validation when column_index is out of bounds."""
    res: GenomicResource = build_inmemory_test_resource({
        GR_CONF_FILE_NAME: """
            type: position_score
            table:
                filename: data.mem
                header:
                    - chrom
                    - pos_begin
                    - score1
            scores:
                - id: score
                  type: float
                  column_index: 10
        """,
        "data.mem": """
            1  10  0.1
        """,
    })

    score = PositionScore(res)
    with pytest.raises(AssertionError):
        score.open()


def test_validate_scoredefs_no_column_name_or_index() -> None:
    """Test scoredef validation when neither column_name nor column_index."""
    res: GenomicResource = build_inmemory_test_resource({
        GR_CONF_FILE_NAME: """
            type: position_score
            table:
                filename: data.mem
            scores:
                - id: score
                  type: float
        """,
        "data.mem": """
            chrom  pos_begin  score
            1      10         0.1
        """,
    })

    score = PositionScore(res)
    with pytest.raises(AssertionError, match="Either an index or name"):
        score.open()


def test_deprecated_name_and_index_config(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test deprecated 'name' and 'index' configuration options."""
    caplog.set_level(logging.DEBUG)

    res: GenomicResource = build_inmemory_test_resource({
        GR_CONF_FILE_NAME: """
            type: position_score
            table:
                filename: data.mem
            scores:
                - id: score1
                  type: float
                  name: score1
                - id: score2
                  type: float
                  index: 3
        """,
        "data.mem": """
            chrom  pos_begin  score1  score2
            1      10         0.1     0.2
        """,
    })

    score = PositionScore(res)
    score.open()

    assert any("outdated" in rec.message for rec in caplog.records)


def test_allele_score_invalid_mode_config() -> None:
    """Test AlleleScore with invalid mode configuration."""
    res: GenomicResource = build_inmemory_test_resource({
        GR_CONF_FILE_NAME: """
            type: allele_score
            allele_score_mode: unknown_mode
            table:
                filename: data.mem
                reference:
                  name: reference
                alternative:
                  name: alternative
            scores:
                - id: freq
                  type: float
                  desc: ""
                  name: freq
        """,
        "data.mem": """
            chrom  pos_begin  reference  alternative  freq
            1      10         A          G            0.02
        """,
    })

    with pytest.raises(ValueError, match="Invalid configuration"):
        AlleleScore(res)


def test_build_score_from_resource_fragment_score() -> None:
    """Test build_score_from_resource with fragment_score type."""
    res: GenomicResource = build_inmemory_test_resource({
        GR_CONF_FILE_NAME: """
            type: fragment_score
            table:
                filename: data.mem
            scores:
                - id: cnv_type
                  type: str
                  name: cnv_type
        """,
        "data.mem": """
            chrom  pos_begin  pos_end  cnv_type
            1      100        200      DEL
        """,
    })

    score = build_score_from_resource(res)
    assert isinstance(score, FragmentScore)


def test_fragment_score_get_schema() -> None:
    """Test FragmentScore.get_schema() method."""
    schema = FragmentScore.get_schema()
    assert "scores" in schema
    assert "aggregator" in schema["scores"]["schema"]["schema"]


def test_position_score_fetch_region_all() -> None:
    res: GenomicResource = build_inmemory_test_resource({
        GR_CONF_FILE_NAME: """
            type: position_score
            table:
                filename: data.mem
            scores:
                - id: score1
                  type: float
                  name: score1
                - id: score2
                  type: float
                  name: score2
            default_annotation:
                - source: score1
                  name: custom_name
                - source: score2
        """,
        "data.mem": """
            chrom  pos_begin pos_end score1  score2
            1      11        20      0.1     0.2
            1      21        30      0.1     0.2
            2      11        20      0.1     0.2
            2      21        30      0.1     0.2
        """,
    })

    score = build_position_score_from_resource(res)
    score.open()
    # A contig is required, so "every record" is a loop over the contigs --
    # the same idiom ``_do_noregion_histograms`` uses for --region-size 0.
    result = [
        rec
        for chrom in score.get_all_chromosomes()
        for rec in score.fetch_region_segment_scores(chrom, None, None)
    ]
    assert len(result) == 4


def test_allele_score_fetch_region_all() -> None:
    res: GenomicResource = build_inmemory_test_resource({
        GR_CONF_FILE_NAME: """
            type: allele_score
            table:
                filename: data.mem
                reference:
                  name: reference
                alternative:
                  name: alternative
            scores:
                - id: freq
                  type: float
                  desc: ""
                  name: freq
                  aggregator: max
        """,
        "data.mem": """
            chrom  pos_begin  reference  alternative  freq
            1      10         A          G            0.02
            1      20         A          G            0.02
            1      30         A          G            0.02
            2      10         A          G            0.02
            2      20         A          G            0.02
            2      30         A          G            0.02
            3      10         A          G            0.02
            3      20         A          G            0.02
            3      30         A          G            0.02
        """,
    })
    score = AlleleScore(res)
    score.open()

    result = [
        rec
        for chrom in score.get_all_chromosomes()
        for rec in score.fetch_region_segment_scores(chrom, None, None)
    ]
    assert len(result) == 9
