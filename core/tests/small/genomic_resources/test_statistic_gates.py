# pylint: disable=C0116
"""Which kinds publish which per-region statistics.

Each statistics module answers for itself, through a ``region_*_for``
function gated on the BUILT SCORE CLASS.  The three functions are the
declaration of the kind-to-statistic relation, and the table below pins
it for every kind and every gate at once: one kind, in every spelling,
gets an accumulator for the region; every other kind gets ``None``.
"""
import pathlib
from collections.abc import Callable
from typing import Any

import pytest
from gain.genomic_resources.genomic_scores import (
    GenomicScore,
    build_score_from_resource,
)
from gain.genomic_resources.resource_types import (
    LEGACY_FRAGMENT_SCORE_TYPE,
    PREFERRED_FRAGMENT_SCORE_TYPE,
)
from gain.genomic_resources.statistics.alleles import (
    RegionAlleles,
    region_alleles_for,
)
from gain.genomic_resources.statistics.coverage import (
    RegionCoverage,
    region_coverage_for,
)
from gain.genomic_resources.statistics.fragments import (
    RegionFragments,
    region_fragments_for,
)
from gain.genomic_resources.testing.builders import (
    a_fragment_score,
    a_position_score,
    an_allele_score,
)

MakeScore = Callable[[pathlib.Path], GenomicScore]
Gate = Callable[[GenomicScore, str, int | None, int | None], Any]

LEGACY = pytest.mark.legacy_vocabulary


# The builders' default data is enough: a gate reads the built score's
# class and never opens the table.

def _position_score(tmp_path: pathlib.Path) -> GenomicScore:
    return build_score_from_resource(
        a_position_score().build_resource(tmp_path))


def _allele_score(tmp_path: pathlib.Path) -> GenomicScore:
    return build_score_from_resource(
        an_allele_score().build_resource(tmp_path))


def _fragment_score(tmp_path: pathlib.Path) -> GenomicScore:
    return build_score_from_resource(
        a_fragment_score()
        .with_resource_type(PREFERRED_FRAGMENT_SCORE_TYPE)
        .build_resource(tmp_path))


def _legacy_fragment_score(tmp_path: pathlib.Path) -> GenomicScore:
    return build_score_from_resource(
        a_fragment_score()
        .with_resource_type(LEGACY_FRAGMENT_SCORE_TYPE)
        .build_resource(tmp_path))


@pytest.mark.parametrize(("make", "gate", "expected"), [
    # Coverage: position scores alone.
    pytest.param(
        _position_score, region_coverage_for, RegionCoverage,
        id="coverage-position_score"),
    pytest.param(
        _allele_score, region_coverage_for, None,
        id="coverage-allele_score"),
    pytest.param(
        _fragment_score, region_coverage_for, None,
        id="coverage-fragment_score"),
    pytest.param(
        _legacy_fragment_score, region_coverage_for, None,
        id="coverage-cnv_collection", marks=LEGACY),
    # Fragments: fragment scores alone, in both spellings.
    pytest.param(
        _fragment_score, region_fragments_for, RegionFragments,
        id="fragments-fragment_score"),
    pytest.param(
        _legacy_fragment_score, region_fragments_for, RegionFragments,
        id="fragments-cnv_collection", marks=LEGACY),
    pytest.param(
        _position_score, region_fragments_for, None,
        id="fragments-position_score"),
    pytest.param(
        _allele_score, region_fragments_for, None,
        id="fragments-allele_score"),
    # Alleles: allele scores alone.
    pytest.param(
        _allele_score, region_alleles_for, RegionAlleles,
        id="alleles-allele_score"),
    pytest.param(
        _position_score, region_alleles_for, None,
        id="alleles-position_score"),
    pytest.param(
        _fragment_score, region_alleles_for, None,
        id="alleles-fragment_score"),
    pytest.param(
        _legacy_fragment_score, region_alleles_for, None,
        id="alleles-cnv_collection", marks=LEGACY),
])
def test_a_kind_gets_an_accumulator_from_exactly_the_gates_it_publishes(
    tmp_path: pathlib.Path,
    make: MakeScore,
    gate: Gate,
    expected: type | None,
) -> None:
    score = make(tmp_path)

    region = gate(score, "chr1", 1, 100)

    assert (None if region is None else type(region)) is expected


@pytest.mark.parametrize(("make", "gate"), [
    pytest.param(_position_score, region_coverage_for, id="coverage"),
    pytest.param(_fragment_score, region_fragments_for, id="fragments"),
    pytest.param(_allele_score, region_alleles_for, id="alleles"),
])
def test_an_accumulator_is_built_for_the_region_asked(
    tmp_path: pathlib.Path,
    make: MakeScore,
    gate: Gate,
) -> None:
    score = make(tmp_path)

    region = gate(score, "chr1", 1, 100)

    assert (region.chrom, region.start, region.end) == ("chr1", 1, 100)
