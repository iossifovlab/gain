# pylint: disable=W0621,C0114,C0116
import pathlib

import pytest
from gain.genomic_resources.liftover_chain import (
    LiftoverChain,
    build_liftover_chain_from_resource,
)
from gain.genomic_resources.repository import GenomicResource
from gain.genomic_resources.testing.builders import a_grr
from gain.genomic_resources.testing.liftover_chain_builder import (
    ResourceValidationError,
    a_liftover_chain,
)


def opened_chain(resource: GenomicResource) -> LiftoverChain:
    return build_liftover_chain_from_resource(resource).open()


def lift(
    resource: GenomicResource, chrom: str, pos: int,
) -> tuple[str, int, str] | None:
    """Lift ``chrom:pos`` over; ``(chrom, pos, strand)`` or ``None``."""
    lifted = opened_chain(resource).convert_coordinate(chrom, pos)
    return None if lifted is None else lifted[:3]


#: ``chr2`` onto the reverse strand of a same-length ``chr2``: 1-based
#: position ``p`` lifts to ``101 - p`` on ``-``.
REVERSED_CHR2 = """
chain 1000 chr2 100 + 0 100 chr2 100 - 0 100 2
100
"""

#: ``chr3`` onto ``chr3`` shifted by 20.
SHIFTED_CHR3 = """
chain 1000 chr3 100 + 0 100 chr3 120 + 20 120 3
100
"""


def test_bare_builder_lifts_over_through_its_default_chain(
    tmp_path: pathlib.Path,
) -> None:
    resource = a_liftover_chain().build_resource(tmp_path)

    assert lift(resource, "chr1", 5) == ("chr1", 15, "+")


def test_an_authored_chain_replaces_the_default(
    tmp_path: pathlib.Path,
) -> None:
    resource = a_liftover_chain().with_chain(REVERSED_CHR2) \
        .build_resource(tmp_path)

    assert lift(resource, "chr2", 5) == ("chr2", 96, "-")
    assert lift(resource, "chr1", 5) is None


def test_every_authored_chain_is_written(tmp_path: pathlib.Path) -> None:
    resource = a_liftover_chain() \
        .with_chain(REVERSED_CHR2) \
        .with_chain(SHIFTED_CHR3) \
        .build_resource(tmp_path)

    assert lift(resource, "chr2", 5) == ("chr2", 96, "-")
    assert lift(resource, "chr3", 5) == ("chr3", 25, "+")


def test_chrom_prefix_is_applied_on_both_sides_of_the_liftover(
    tmp_path: pathlib.Path,
) -> None:
    resource = a_liftover_chain().with_chrom_prefix(
        variant_coordinates={"add_prefix": "chr"},
        target_coordinates={"del_prefix": "chr"},
    ).build_resource(tmp_path)

    assert lift(resource, "1", 5) == ("1", 15, "+")


def test_chrom_prefix_with_neither_side_is_refused() -> None:
    with pytest.raises(ResourceValidationError, match="chrom_prefix"):
        a_liftover_chain().with_chrom_prefix()


def test_chrom_prefix_does_not_share_the_callers_mapping(
    tmp_path: pathlib.Path,
) -> None:
    target = {"del_prefix": "chr"}
    builder = a_liftover_chain().with_chrom_prefix(
        target_coordinates=target)
    target["del_prefix"] = "c"

    resource = builder.build_resource(tmp_path)

    assert lift(resource, "chr1", 5) == ("1", 15, "+")


def test_specialising_a_builder_leaves_the_base_unchanged(
    tmp_path: pathlib.Path,
) -> None:
    base = a_liftover_chain()
    base.with_chain(REVERSED_CHR2)

    resource = base.build_resource(tmp_path)

    assert lift(resource, "chr1", 5) == ("chr1", 15, "+")


def test_composes_into_a_grr_by_resource_id(tmp_path: pathlib.Path) -> None:
    repo = a_grr() \
        .with_resource("liftover/reversed", a_liftover_chain()
                       .with_chain(REVERSED_CHR2)) \
        .build_repo(tmp_path)

    resource = repo.get_resource("liftover/reversed")

    assert lift(resource, "chr2", 5) == ("chr2", 96, "-")


def test_labels_are_read_back_through_the_resource(
    tmp_path: pathlib.Path,
) -> None:
    resource = a_liftover_chain() \
        .with_labels(source_genome="hg19", target_genome="hg38") \
        .build_resource(tmp_path)

    chain = opened_chain(resource)

    assert chain.source_genome_id == "hg19"
    assert chain.target_genome_id == "hg38"
