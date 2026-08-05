# pylint: disable=redefined-outer-name,C0114,C0116,protected-access,fixme

from typing import Any

import pytest
import yaml
from gain.genomic_resources.liftover_chain import (
    LiftoverChain,
    build_liftover_chain_from_resource,
)
from gain.genomic_resources.reference_genome import (
    build_reference_genome_from_resource,
)
from gain.genomic_resources.repository import (
    GenomicResource,
    GenomicResourceRepo,
)
from gain.genomic_resources.testing import build_inmemory_test_resource


@pytest.mark.parametrize("schrom, spos, expected", [
    ("foo", 3, None),
    ("foo", 4, None),
    ("foo", 5, ("chrFoo", 1, "+", 4900)),
    ("foo", 6, ("chrFoo", 2, "+", 4900)),
    ("foo", 7, ("chrFoo", 3, "+", 4900)),
    ("foo", 14, ("chrFoo", 10, "+", 4900)),
    ("foo", 51, ("chrFoo", 47, "+", 4900)),
    ("foo", 52, ("chrFoo", 48, "+", 4900)),
    ("foo", 53, None),
    ("foo", 54, None),
    ("foo", 55, None),
    ("foo", 56, None),
    ("foo", 57, ("chrFoo", 49, "+", 4900)),
    ("foo", 58, ("chrFoo", 50, "+", 4900)),
    ("foo", 80, ("chrFoo", 72, "+", 4900)),
    ("foo", 103, ("chrFoo", 95, "+", 4900)),
    ("foo", 104, ("chrFoo", 96, "+", 4900)),
    ("foo", 105, None),
    ("bar", 5, ("chrBar", 1, "+", 4800)),
    ("bar", 52, ("chrBar", 48, "+", 4800)),
    ("bar", 53, None),
    ("bar", 60, None),
    ("bar", 61, ("chrBar", 49, "+", 4800)),
    ("bar", 108, ("chrBar", 96, "+", 4800)),
    ("bar", 109, None),
    ("bar", 112, None),
    ("baz", 5, ("chrBaz", 96, "-", 4700)),
    ("baz", 6, ("chrBaz", 95, "-", 4700)),
    ("baz", 51, ("chrBaz", 50, "-", 4700)),
    ("baz", 52, ("chrBaz", 49, "-", 4700)),
    ("baz", 53, None),
    ("baz", 56, None),
    ("baz", 57, ("chrBaz", 48, "-", 4700)),
    ("baz", 104, ("chrBaz", 1, "-", 4700)),
    ("baz", 105, None),
])
def test_liftover_chain_fixture(
        schrom: str,
        spos: int,
        expected: tuple[str, int, str, int] | None,
        liftover_grr_fixture: GenomicResourceRepo) -> None:
    res = liftover_grr_fixture.get_resource("liftover_chain")
    liftover_chain = build_liftover_chain_from_resource(res)

    res = liftover_grr_fixture.get_resource("target_genome")
    target_genome = build_reference_genome_from_resource(res).open()
    res = liftover_grr_fixture.get_resource("source_genome")
    source_genome = build_reference_genome_from_resource(res).open()

    assert liftover_chain is not None
    liftover_chain.open()
    lo_coordinates = liftover_chain.convert_coordinate(schrom, spos)
    assert lo_coordinates == expected

    if expected is not None:
        sseq = source_genome.get_sequence(schrom, spos, spos)
        chrom, pos, _, _ = expected
        tseq = target_genome.get_sequence(chrom, pos, pos)
        assert sseq == tseq


def _a_liftover_chain_resource(labels: Any) -> GenomicResource:
    """A chain resource whose ``meta.labels`` is the value passed.

    Only the config is read here -- constructing a ``LiftoverChain`` never
    opens the chain file -- so the resource needs no chain in it.
    """
    return build_inmemory_test_resource({
        "genomic_resource.yaml": yaml.safe_dump({
            "type": "liftover_chain",
            "filename": "liftover.chain.gz",
            "meta": {"labels": labels},
        }),
    })


def test_a_chain_takes_its_genome_ids_from_the_labels() -> None:
    chain = LiftoverChain(_a_liftover_chain_resource(
        {"source_genome": "hg19", "target_genome": "hg38"}))

    assert chain.source_genome_id == "hg19"
    assert chain.target_genome_id == "hg38"


@pytest.mark.parametrize("labels", ["some text", ["a", "b"], 2019, None])
def test_a_chain_whose_labels_are_not_a_mapping_still_builds(
    labels: Any,
) -> None:
    """Constructing a chain must not raise on a malformed ``meta.labels``.

    The chain used to index ``config["meta"]["labels"]`` itself instead of
    reading the resource's labels, so a scalar there ended a *liftover
    annotation* -- not merely a search -- in an ``AttributeError`` at
    construction (gain#654). Both genome ids are simply left unset.
    """
    chain = LiftoverChain(_a_liftover_chain_resource(labels))

    assert chain.source_genome_id is None
    assert chain.target_genome_id is None
