# pylint: disable=W0621,C0114,C0116,W0212,W0613

import textwrap

import pytest
from gain.genomic_resources.genomic_scores import AlleleScore
from gain.genomic_resources.repository import GR_CONF_FILE_NAME, GenomicResource
from gain.genomic_resources.testing import build_inmemory_test_resource


def build_allele_resource(config: str, data: str) -> GenomicResource:
    return build_inmemory_test_resource({
        GR_CONF_FILE_NAME: textwrap.dedent(config),
        "data.mem": textwrap.dedent(data),
    })


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
    assert score.fetch_scores("1", 10, "A", "C") == {"freq": 0.03}


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
    assert list(score.fetch_region_values("1", 10, 11, ["freq"])) == \
        [(10, 10, [0.04]),
         (10, 10, [0.03]),
         (10, 10, [0.02])]

    assert list(score.fetch_region_values("1", 10, 16, ["freq"])) == \
        [(10, 10, [0.04]),
         (10, 10, [0.03]),
         (10, 10, [0.02]),
         (16, 16, [0.05]),
         (16, 16, [0.04]),
         (16, 16, [0.03])]

    assert list(score.fetch_region_values("2", None, None, ["freq"])) == [
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
    assert score.fetch_scores("1", 10, "A", "A", ["freq"]) is None
    assert score.fetch_scores("1", 10, "A", "G", ["freq"]) is None
    assert score.fetch_scores("1", 10, "A", "T", ["freq"]) is None
    assert score.fetch_scores("1", 10, "A", "C", ["freq"]) is None


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


def test_allele_score_np_score_defaults_to_substitutions(
        caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("WARNING")
    res = build_allele_resource(
        """
        type: np_score
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
    assert any("deprecated" in rec.message for rec in caplog.records)


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
        score.fetch_scores("2", 10, "A", "G")


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

    result = list(score.fetch_region("1", 10, 12, ["freq"]))
    assert result == [(10, "A", "G", [0.02])]
