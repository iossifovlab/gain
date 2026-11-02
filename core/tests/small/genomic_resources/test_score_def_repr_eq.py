# pylint: disable=W0621,C0114,C0116
"""``repr()`` and ``==`` of a ``GenomicScoreDef`` in each state (#2044).

``score_index`` exists only after ``open()`` resolves the payload column,
and a VCF definition never gets it.  So ``repr()`` and ``==`` must not read
it, while an early read of the attribute itself still raises.
"""
import pathlib

import pytest
from gain.genomic_resources.genomic_scores import (
    AlleleScore,
    PositionScore,
    build_allele_score_from_resource,
    build_position_score_from_resource,
)
from gain.genomic_resources.repository import GenomicResource
from gain.genomic_resources.testing.builders import (
    a_position_score,
    a_vcf_info_score,
)


@pytest.fixture
def tabix_resource(tmp_path: pathlib.Path) -> GenomicResource:
    return (
        a_position_score()
        .with_score("freq", "float")
        .with_data("""
            chrom  pos_begin  freq
            1      10         0.1
        """)
        .build_resource(tmp_path)
    )


@pytest.fixture
def vcf_resource(tmp_path: pathlib.Path) -> GenomicResource:
    return a_vcf_info_score().with_data("""
##fileformat=VCFv4.1
##INFO=<ID=scoreA,Number=1,Type=Float,Description="score A">
#CHROM POS ID REF ALT QUAL FILTER INFO
chr1   10  .  A   T   .    .      scoreA=0.1
""").build_resource(tmp_path)


def _tabix_score(resource: GenomicResource) -> PositionScore:
    return build_position_score_from_resource(resource)


def _vcf_score(resource: GenomicResource) -> AlleleScore:
    return build_allele_score_from_resource(resource)


def test_repr_of_an_unopened_column_backed_def_names_the_score(
    tabix_resource: GenomicResource,
) -> None:
    score_def = _tabix_score(tabix_resource).score_definitions["freq"]

    text = repr(score_def)

    assert "score_id='freq'" in text


def test_two_unopened_column_backed_defs_of_one_resource_are_equal(
    tabix_resource: GenomicResource,
) -> None:
    first = _tabix_score(tabix_resource).score_definitions["freq"]
    second = _tabix_score(tabix_resource).score_definitions["freq"]

    assert first is not second
    assert first == second


def test_repr_of_an_opened_column_backed_def_names_the_score(
    tabix_resource: GenomicResource,
) -> None:
    with _tabix_score(tabix_resource).open() as score:
        score_def = score.score_definitions["freq"]

        text = repr(score_def)

    assert "score_id='freq'" in text


def test_an_opened_column_backed_def_equals_an_unopened_one(
    tabix_resource: GenomicResource,
) -> None:
    unopened = _tabix_score(tabix_resource).score_definitions["freq"]
    with _tabix_score(tabix_resource).open() as score:
        opened = score.score_definitions["freq"]

        assert opened == unopened


def test_repr_of_a_def_is_the_same_before_and_after_open(
    tabix_resource: GenomicResource,
) -> None:
    score = _tabix_score(tabix_resource)
    before = repr(score.score_definitions["freq"])

    with score.open():
        after = repr(score.score_definitions["freq"])

    assert after == before


def test_reading_score_index_before_open_raises_attribute_error(
    tabix_resource: GenomicResource,
) -> None:
    score_def = _tabix_score(tabix_resource).score_definitions["freq"]

    with pytest.raises(AttributeError, match="score_index"):
        _ = score_def.score_index


def test_repr_of_an_opened_vcf_backed_def_names_the_score(
    vcf_resource: GenomicResource,
) -> None:
    with _vcf_score(vcf_resource).open() as score:
        score_def = score.score_definitions["scoreA"]

        text = repr(score_def)

    assert "score_id='scoreA'" in text


def test_two_opened_vcf_backed_defs_of_one_resource_are_equal(
    vcf_resource: GenomicResource,
) -> None:
    with (
        _vcf_score(vcf_resource).open() as first_score,
        _vcf_score(vcf_resource).open() as second_score,
    ):
        first = first_score.score_definitions["scoreA"]
        second = second_score.score_definitions["scoreA"]

        assert first is not second
        assert first == second
