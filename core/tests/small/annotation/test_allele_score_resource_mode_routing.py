# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""The resource's ``allele_score_mode`` routes a ``VCFAllele`` (gain#1748).

The ``allele_score`` annotator in its default ``allele`` mode asks the
resource how it is keyed.  A ``substitutions`` resource answers exact
matches for substitutions only: every other ``VCFAllele`` -- insertion,
deletion, complex -- is reduced over the bases it covers.  An ``alleles``
resource answers every ``VCFAllele`` by exact match.  The annotator's
``mode: region`` overrides both.

The score plane itself stays mode-blind: the routing is the annotator's.

Every fixture row below is chosen so the exact match and the region fold
over the same allele answer *different* values -- otherwise a test would
pass whichever path the annotator took.  Positions and values:

- position 11 holds ``C>T`` (0.2), ``C>A`` (0.25) and the deletion
  ``CG>C`` (0.05);
- position 12 holds ``G>A`` (0.3) and the insertion ``G>GT`` (0.07);
- position 10 holds ``ACG>A`` (0.5), a deletion three bases long.

So with ``max``: the substitution ``11 C>T`` is 0.2 by match and 0.25
folded; the deletion ``11 CG>C`` is 0.05 by match and 0.3 folded; the
insertion ``12 G>GT`` is 0.07 by match and 0.3 folded.
"""
import textwrap
from typing import Any

import pytest
from gain.annotation.annotatable import VCFAllele
from gain.annotation.annotation_factory import load_pipeline_from_yaml
from gain.genomic_resources.repository import (
    GR_CONF_FILE_NAME,
    GenomicResourceRepo,
)
from gain.genomic_resources.testing import build_inmemory_test_repository

_DATA = """
    chrom  pos_begin  reference  alternative  freq
    1      10         ACG        A            0.5
    1      11         C          T            0.2
    1      11         C          A            0.25
    1      11         CG         C            0.05
    1      12         G          A            0.3
    1      12         G          GT           0.07
"""

SUBSTITUTION = VCFAllele("1", 11, "C", "T")
DELETION = VCFAllele("1", 11, "CG", "C")
INSERTION = VCFAllele("1", 12, "G", "GT")
LONG_DELETION = VCFAllele("1", 10, "ACG", "A")
UNLISTED_INSERTION = VCFAllele("1", 11, "C", "CA")


def _repo(allele_score_mode: str | None) -> GenomicResourceRepo:
    mode_line = (
        "" if allele_score_mode is None
        else f"allele_score_mode: {allele_score_mode}")
    return build_inmemory_test_repository({
        "score": {
            GR_CONF_FILE_NAME: textwrap.dedent(f"""
                type: allele_score
                {mode_line}
                table:
                    filename: data.mem
                    reference:
                      name: reference
                    alternative:
                      name: alternative
                scores:
                - id: freq
                  type: float
                  name: freq
                  aggregator: max
            """),
            "data.mem": _DATA,
        },
    })


def _annotate(
    repo: GenomicResourceRepo,
    allele: VCFAllele,
    extra: str = "",
) -> dict[str, Any]:
    pipeline_config = textwrap.dedent(f"""
        - allele_score:
            resource_id: score
            {extra}
            attributes:
            - source: freq
            - source: allele
              internal: false
        """)
    pipeline = load_pipeline_from_yaml(pipeline_config, repo)
    with pipeline.open() as work_pipeline:
        return work_pipeline.annotate(allele)


@pytest.fixture(scope="module")
def substitutions_repo() -> GenomicResourceRepo:
    return _repo("substitutions")


@pytest.fixture(scope="module")
def alleles_repo() -> GenomicResourceRepo:
    return _repo("alleles")


def test_substitution_on_a_substitutions_resource_is_matched_exactly(
    substitutions_repo: GenomicResourceRepo,
) -> None:
    assert _annotate(substitutions_repo, SUBSTITUTION)["freq"] == 0.2


@pytest.mark.parametrize("allele,folded", [
    (DELETION, 0.3),
    (INSERTION, 0.3),
])
def test_indel_on_a_substitutions_resource_is_region_folded(
    substitutions_repo: GenomicResourceRepo,
    allele: VCFAllele,
    folded: float,
) -> None:
    assert _annotate(substitutions_repo, allele)["freq"] == folded


@pytest.mark.parametrize("allele,matched", [
    (SUBSTITUTION, 0.2),
    (DELETION, 0.05),
    (INSERTION, 0.07),
])
def test_every_allele_on_an_alleles_resource_is_matched_exactly(
    alleles_repo: GenomicResourceRepo,
    allele: VCFAllele,
    matched: float,
) -> None:
    assert _annotate(alleles_repo, allele)["freq"] == matched


def test_unlisted_indel_on_an_alleles_resource_is_not_folded(
    alleles_repo: GenomicResourceRepo,
) -> None:
    assert _annotate(alleles_repo, UNLISTED_INSERTION)["freq"] is None


def test_explicit_region_mode_folds_a_substitution_too(
    substitutions_repo: GenomicResourceRepo,
) -> None:
    result = _annotate(substitutions_repo, SUBSTITUTION, "mode: region")

    assert result["freq"] == 0.25


def test_indel_routed_to_the_fold_honours_region_length_cutoff(
    substitutions_repo: GenomicResourceRepo,
) -> None:
    result = _annotate(
        substitutions_repo, LONG_DELETION, "region_length_cutoff: 1")

    assert result == {"freq": None, "allele": None}


def test_allele_attribute_of_a_folded_indel_lists_the_overlapping_keys(
    substitutions_repo: GenomicResourceRepo,
) -> None:
    result = _annotate(substitutions_repo, DELETION)

    assert sorted(result["allele"]) == [
        "1:11:C:A", "1:11:C:T", "1:11:CG:C", "1:12:G:A", "1:12:G:GT"]
