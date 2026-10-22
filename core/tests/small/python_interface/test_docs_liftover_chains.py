"""Pin the offline claims of the "Liftover chains" page.

The tests run the code that the page describes and assert the results
that the page states. They read no ``.rst`` file. The page is
``docs/source/development/resources/liftover_chains.rst``. The examples of
the page use ``liftover/hg19_to_hg38``; mini-GRR holds no liftover chain,
so the tests build one with ``a_liftover_chain``. Its default chain takes
``chr1`` onto ``chr1`` shifted by 10, so ``chr1:5`` lifts to ``chr1:15``.
The tests pin these claims:

- Both builders give a ``LiftoverChain``, and repeated builds of a
  resource return the same object.
- A new chain is closed, ``open()`` returns the chain, and a second
  ``open()`` has no effect.
- A chain is not a context manager, and ``close()`` releases nothing.
- ``convert_coordinate`` takes a 1-based position and returns the
  chromosome, the 1-based position, the strand and the chain score.
- A position outside every aligned block gives ``None``, and a position
  that maps twice gives the first mapping.
- ``chrom_prefix`` renames the input contig before the chain and the
  target contig after it.
"""
import pathlib

import pytest
from gain import grr
from gain.genomic_resources.liftover_chain import LiftoverChain
from gain.genomic_resources.repository import GenomicResourceRepo
from gain.genomic_resources.testing.builders import a_grr
from gain.genomic_resources.testing.liftover_chain_builder import (
    a_liftover_chain,
)

_SHIFT_BY_10 = "chain 1000 chr1 100 + 0 100 chr1 110 + 10 110 1\n100\n"
_ONTO_CHR2 = "chain 500 chr1 100 + 0 100 chr2 200 + 50 150 2\n100\n"


@pytest.fixture(name="chain_repo")
def fixture_chain_repo(tmp_path: pathlib.Path) -> GenomicResourceRepo:
    """A repository with three liftover chain resources."""
    return a_grr() \
        .with_resource("liftover", a_liftover_chain()) \
        .with_resource(
            "two_mappings",
            a_liftover_chain().with_chain(_SHIFT_BY_10)
            .with_chain(_ONTO_CHR2)) \
        .with_resource(
            "renamed",
            a_liftover_chain().with_chrom_prefix(
                variant_coordinates={"add_prefix": "chr"},
                target_coordinates={"del_prefix": "chr"})) \
        .build_repo(tmp_path)


def test_repeated_builds_return_the_same_object(
    chain_repo: GenomicResourceRepo,
) -> None:
    """Both builders return the one ``LiftoverChain`` of the resource."""
    from_id = grr.build_liftover_chain_from_resource_id(
        "liftover", chain_repo)
    from_resource = grr.build_liftover_chain_from_resource(
        chain_repo.get_resource("liftover"))

    assert isinstance(from_id, LiftoverChain)
    assert from_resource is from_id


def test_open_returns_the_chain_and_a_second_open_does_nothing(
    chain_repo: GenomicResourceRepo,
) -> None:
    """``open()`` chains to the builder, and is idempotent."""
    chain = grr.build_liftover_chain_from_resource_id("liftover", chain_repo)
    assert not chain.is_open()

    assert chain.open() is chain
    assert chain.is_open()
    assert chain.open() is chain
    assert chain.is_open()


def test_chain_is_not_a_context_manager(
    chain_repo: GenomicResourceRepo,
) -> None:
    """A liftover chain cannot be used in a ``with`` block."""
    chain = grr.build_liftover_chain_from_resource_id("liftover", chain_repo)

    assert not hasattr(chain, "__enter__")
    assert not hasattr(chain, "__exit__")


def test_close_releases_nothing(chain_repo: GenomicResourceRepo) -> None:
    """After ``close()``, the chain still converts coordinates."""
    chain = grr.build_liftover_chain_from_resource_id(
        "liftover", chain_repo).open()

    chain.close()

    assert chain.convert_coordinate("chr1", 5) == ("chr1", 15, "+", 1000)


def test_convert_coordinate_returns_the_target_tuple(
    chain_repo: GenomicResourceRepo,
) -> None:
    """The result is the chromosome, the position, the strand and score."""
    chain = grr.build_liftover_chain_from_resource_id(
        "liftover", chain_repo).open()

    assert chain.convert_coordinate("chr1", 5) == ("chr1", 15, "+", 1000)
    assert chain.convert_coordinate("chr1", 1) == ("chr1", 11, "+", 1000)


def test_unaligned_position_gives_none(
    chain_repo: GenomicResourceRepo,
) -> None:
    """A position outside every block, or an unknown contig, gives None."""
    chain = grr.build_liftover_chain_from_resource_id(
        "liftover", chain_repo).open()

    assert chain.convert_coordinate("chr1", 1000) is None
    assert chain.convert_coordinate("chr7", 5) is None


def test_position_that_maps_twice_gives_the_first_mapping(
    chain_repo: GenomicResourceRepo,
) -> None:
    """Two chains cover ``chr1:5``, and the first one answers."""
    chain = grr.build_liftover_chain_from_resource_id(
        "two_mappings", chain_repo).open()

    assert chain.convert_coordinate("chr1", 5) == ("chr1", 15, "+", 1000)


def test_chrom_prefix_renames_input_and_target(
    chain_repo: GenomicResourceRepo,
) -> None:
    """``1`` becomes ``chr1`` for the chain, and ``chr1`` comes back ``1``."""
    chain = grr.build_liftover_chain_from_resource_id(
        "renamed", chain_repo).open()

    assert chain.convert_coordinate("1", 5) == ("1", 15, "+", 1000)
