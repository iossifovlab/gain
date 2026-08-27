# pylint: disable=W0621,C0114,C0116,W0212,W0613

import pathlib
import textwrap

import pytest
from gain.genomic_resources.genomic_position_table.record import (
    ALT,
    POS_BEGIN,
    REF,
)
from gain.genomic_resources.genomic_scores import (
    AlleleScore,
    build_allele_score_from_resource,
)
from gain.genomic_resources.repository import GR_CONF_FILE_NAME, GenomicResource
from gain.genomic_resources.testing import build_inmemory_test_resource
from gain.genomic_resources.testing.builders import an_allele_score


def build_allele_resource(config: str, data: str) -> GenomicResource:
    return build_inmemory_test_resource({
        GR_CONF_FILE_NAME: textwrap.dedent(config),
        "data.mem": textwrap.dedent(data),
    })


@pytest.fixture
def region_allele_score(tmp_path: pathlib.Path) -> AlleleScore:
    """Two alleles at one position and a third further along."""
    resource = (
        an_allele_score()
        .with_score("freq", "float")
        .with_data("""
            chrom  pos_begin  reference  alternative  freq
            1      10         A          G            0.1
            1      10         A          C            0.2
            1      16         C          T            0.3
        """)
        .build_resource(tmp_path)
    )
    return build_allele_score_from_resource(resource)


def test_the_simplest_allele_score() -> None:
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
            1      10         A          C            0.03
            1      10         A          A            0.04
            1      16         CA         G            0.03
            1      16         C          T            0.04
            1      16         C          A            0.05
        """,
    })
    assert res.get_type() == "allele_score"

    score = AlleleScore(res)
    score.open()

    assert score.get_all_scores() == ["freq"]
    assert score.fetch_allele_scores("1", 10, "A", "C") == {"freq": 0.03}


def test_allele_score_fetch_region() -> None:
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
            1      10         A          C            0.03
            1      10         A          A            0.04
            1      16         CA         G            0.03
            1      16         C          T            0.04
            1      16         C          A            0.05
            2      16         CA         G            0.03
            2      16         C          T            EMPTY
            2      16         C          A            0.05
        """,
    })
    score = AlleleScore(res)
    score.open()

    # The in-mem table will sort the records. In this example it will sort
    # the alternatives column (previous columns are the same). That is why
    # the scores (freq) appear out of order
    assert list(score.fetch_region_segments("1", 10, 11, ["freq"])) == \
        [(10, 10, [0.04]),
         (10, 10, [0.03]),
         (10, 10, [0.02])]

    assert list(score.fetch_region_segments("1", 10, 16, ["freq"])) == \
        [(10, 10, [0.04]),
         (10, 10, [0.03]),
         (10, 10, [0.02]),
         (16, 16, [0.05]),
         (16, 16, [0.04]),
         (16, 16, [0.03])]

    assert list(score.fetch_region_segments(
        "2", None, None, ["freq"])) == [
        (16, 16, [0.05]),
        (16, 16, [None]),
        (16, 16, [0.03]),
    ]


def test_allele_score_missing_alt() -> None:
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
            1      10         A          .            0.03
        """,
    })
    score = AlleleScore(res)
    score.open()
    assert score.fetch_allele_scores("1", 10, "A", "A", ["freq"]) is None
    assert score.fetch_allele_scores("1", 10, "A", "G", ["freq"]) is None
    assert score.fetch_allele_scores("1", 10, "A", "T", ["freq"]) is None
    assert score.fetch_allele_scores("1", 10, "A", "C", ["freq"]) is None


def test_allele_score_mode_defaults_to_alleles() -> None:
    res = build_allele_resource(
        """
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
        """
        chrom  pos_begin  reference  alternative  freq
        1      10         A          G            0.02
        """,
    )

    score = AlleleScore(res)

    assert score.alleles_mode()
    assert not score.substitutions_mode()


def test_allele_score_mode_substitutions_config() -> None:
    res = build_allele_resource(
        """
        type: allele_score
        allele_score_mode: substitutions
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
        """
        chrom  pos_begin  reference  alternative  freq
        1      10         A          G            0.02
        """,
    )

    score = AlleleScore(res)

    assert score.substitutions_mode()
    assert not score.alleles_mode()


def test_allele_score_fetch_scores_invalid_chromosome() -> None:
    res = build_allele_resource(
        """
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
        """
        chrom  pos_begin  reference  alternative  freq
        1      10         A          G            0.02
        """,
    )

    score = AlleleScore(res)
    score.open()

    with pytest.raises(
        ValueError, match="not among the available chromosomes",
    ):
        score.fetch_allele_scores("2", 10, "A", "G")


def test_allele_score_fetch_region_spanning_record_at_pos_begin() -> None:
    res = build_allele_resource(
        """
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
        """
        chrom  pos_begin  pos_end  reference  alternative  freq
        1      10         12       A          G            0.02
        """,
    )

    score = AlleleScore(res)
    score.open()

    assert list(score.fetch_region_segments("1", 10, 12, ["freq"])) \
        == [(10, 10, [0.02])]
    # The nucleotides come off the record, not the values stream.
    assert [(r[POS_BEGIN], r[REF], r[ALT])
            for r in score.fetch_records("1", 10, 12)] == [(10, "A", "G")]


def test_a_region_no_allele_overlaps_reads_as_absent(
    region_allele_score: AlleleScore,
) -> None:
    """``None`` is absent data -- no record is there to have an opinion.

    Distinct from the empty selection an accompanying filter can produce,
    which is what this read exists to tell apart.
    """
    with region_allele_score.open() as score:
        records = score.fetch_allele_records("1", 200, 300)

    assert records is None


def test_a_region_reads_every_allele_that_overlaps_it(
    region_allele_score: AlleleScore,
) -> None:
    """Several records legitimately share a position; each is its own.

    Compared as a set: which alleles come back is this read's business,
    while the order they arrive in is the table's, and differs by backend.
    """
    with region_allele_score.open() as score:
        records = score.fetch_allele_records("1", 10, 16)

    assert records is not None
    assert {(r[POS_BEGIN], r[REF], r[ALT]) for r in records} == {
        (10, "A", "C"),
        (10, "A", "G"),
        (16, "C", "T"),
    }


def test_a_region_read_takes_the_contig_bounds_as_its_default(
    region_allele_score: AlleleScore,
) -> None:
    """``None`` bounds mean the whole contig, as they do for the table."""
    with region_allele_score.open() as score:
        records = score.fetch_allele_records("1", None, None)

    assert records is not None
    assert len(records) == 3


def test_a_region_read_refuses_a_contig_the_resource_does_not_have(
    region_allele_score: AlleleScore,
) -> None:
    """A contig that does not exist is not a region holding nothing.

    Refused as the per-allele and fragment reads refuse it: answering
    ``None`` would make a caller's typo indistinguishable from real absent
    data.
    """
    with region_allele_score.open() as score, pytest.raises(
        ValueError, match="not among the available chromosomes",
    ):
        score.fetch_allele_records("2", 10, 16)
