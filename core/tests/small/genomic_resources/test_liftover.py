# pylint: disable=redefined-outer-name,C0114,C0116,protected-access,fixme

import logging
import pathlib
from typing import Any

import pytest
import yaml
from gain.genomic_resources.fsspec_protocol import build_local_resource
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
from gain.genomic_resources.resource_errors import MalformedResourceError
from gain.genomic_resources.testing import (
    build_filesystem_test_repository,
    build_inmemory_test_resource,
    setup_directories,
)

from .conftest import UNUSABLE_RESOURCE_ID_LABELS, captured_warnings


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


def _a_chain_config(labels: Any) -> str:
    """The rendered config of a chain whose ``meta.labels`` is ``labels``.

    Only the config is read by the tests below -- constructing a
    ``LiftoverChain`` never opens the chain file -- so the resource
    around it needs no chain in it.
    """
    return yaml.safe_dump({
        "type": "liftover_chain",
        "filename": "liftover.chain.gz",
        "meta": {"labels": labels},
    })


def _a_liftover_chain_resource(labels: Any) -> GenomicResource:
    """A chain resource whose ``meta.labels`` is the value passed."""
    return build_inmemory_test_resource({
        "genomic_resource.yaml": _a_chain_config(labels),
    })


def test_a_chain_takes_its_genome_ids_from_the_labels() -> None:
    chain = LiftoverChain(_a_liftover_chain_resource(
        {"source_genome": "hg19", "target_genome": "hg38"}))

    assert chain.source_genome_id == "hg19"
    assert chain.target_genome_id == "hg38"


def test_a_chain_whose_labels_are_null_declares_no_genomes() -> None:
    """``labels: null`` is a well-formed way of declaring no labels."""
    chain = LiftoverChain(_a_liftover_chain_resource(None))

    assert chain.source_genome_id is None
    assert chain.target_genome_id is None


#: The chain's id, deep enough to look like a real one and distinctive
#: enough that finding it in a warning means something.
A_CHAIN_GROUP, A_CHAIN_NAME = "liftover", "chain776"
A_CHAIN_ID = f"{A_CHAIN_GROUP}/{A_CHAIN_NAME}"


def _a_liftover_chain_under_a_real_id(
    tmp_path: pathlib.Path, labels: Any,
) -> GenomicResource:
    """The same chain, under an id a warning can name.

    ``build_inmemory_test_resource`` gives a resource an EMPTY id,
    against which ``id in message`` holds for every message ever
    written and so pins nothing.
    """
    return _a_chain_under_a_real_id_configured(
        tmp_path, _a_chain_config(labels))


def _a_chain_under_a_real_id_configured(
    tmp_path: pathlib.Path, config: str,
) -> GenomicResource:
    """A chain under ``A_CHAIN_ID`` whose config is ``config``, verbatim."""
    setup_directories(tmp_path, {
        A_CHAIN_GROUP: {
            A_CHAIN_NAME: {"genomic_resource.yaml": config},
        },
    })
    return build_filesystem_test_repository(tmp_path).get_resource(A_CHAIN_ID)


@pytest.mark.parametrize("config", [
    # A typo of `filename`.
    pytest.param({"type": "liftover_chain",
                  "filename": "liftover.chain.gz",
                  "file_name": "liftover.chain.gz"},
                 id="a-key-the-schema-does-not-know"),
    # A reference genome spells `chrom_prefix` as a flat string, and the
    # two resource types sit side by side in a repository -- on a chain
    # it is a mapping of two coordinate sides.
    pytest.param({"type": "liftover_chain",
                  "filename": "liftover.chain.gz",
                  "chrom_prefix": "chr"},
                 id="the-genome-spelling-of-chrom_prefix"),
    # No `filename` key at all: the one field a chain cannot do without,
    # refused here rather than on first use of the file.
    pytest.param({"type": "liftover_chain"},
                 id="no-filename"),
])
def test_a_chain_config_outside_its_schema_is_refused(
    tmp_path: pathlib.Path, config: dict[str, Any],
) -> None:
    """The chain runs the schema it declares: a config outside it refuses
    the chain at construction, naming the resource."""
    resource = _a_chain_under_a_real_id_configured(
        tmp_path, yaml.safe_dump(config))

    with pytest.raises(MalformedResourceError, match=A_CHAIN_ID):
        LiftoverChain(resource)


def test_a_chain_whose_chrom_prefix_is_null_rewrites_nothing(
    tmp_path: pathlib.Path,
) -> None:
    """A bare ``chrom_prefix:`` line -- the key with its subsections
    commented out -- means no rewrite on either side, as absence does."""
    resource = _a_chain_under_a_real_id_configured(tmp_path, yaml.safe_dump({
        "type": "liftover_chain",
        "filename": "liftover.chain.gz",
        "chrom_prefix": None,
    }))

    chain = LiftoverChain(resource)

    assert chain.map_chromosome("chr1", chain.chrom_variant_coordinates) \
        == "chr1"
    assert chain.map_chromosome("chr1", chain.chrom_target_coordinates) \
        == "chr1"


@pytest.mark.parametrize("labels", ["some text", ["a", "b"], 2019])
def test_a_chain_whose_labels_are_not_a_mapping_is_refused(
    tmp_path: pathlib.Path, labels: Any,
) -> None:
    """A non-mapping ``meta.labels`` refuses the chain as the resource's
    own fault, naming it -- the way every validating type refuses one
    (ADR 0031), and not the bare ``AttributeError`` of gain#654."""
    resource = _a_liftover_chain_under_a_real_id(tmp_path, labels)

    with pytest.raises(MalformedResourceError, match=A_CHAIN_ID):
        LiftoverChain(resource)


@pytest.mark.parametrize(
    ("value", "reported_as"), UNUSABLE_RESOURCE_ID_LABELS)
def test_a_chain_whose_genome_labels_cannot_name_a_resource(
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
    value: Any,
    reported_as: str,
) -> None:
    """A mapping whose VALUES are unusable, which gain#654 did not cover.

    That issue narrowed the ``labels:`` block; these are well-formed
    blocks holding values that cannot be resource ids.  Read unnarrowed
    into attributes annotated ``str | None`` they were handed to
    ``get_resource`` by the liftover annotator's builder and died there
    with a ``TypeError`` naming neither the chain nor the label -- the
    annotator's own ``is None`` check never sees them.  Both labels are
    reported, each once, and then behave as absent (gain#1053).
    """
    resource = _a_liftover_chain_under_a_real_id(
        tmp_path, {"source_genome": value, "target_genome": value})

    with caplog.at_level(logging.WARNING):
        chain = LiftoverChain(resource)

    assert chain.source_genome_id is None
    assert chain.target_genome_id is None
    warnings = captured_warnings(caplog)
    assert len(warnings) == 2
    assert all(A_CHAIN_ID in message for message in warnings)
    assert all(reported_as in message for message in warnings)
    assert any("source_genome" in message for message in warnings)
    assert any("target_genome" in message for message in warnings)


@pytest.mark.parametrize("labels", [
    pytest.param({}, id="an-empty-mapping"),
    pytest.param({"domain": "liftover"}, id="labels-without-the-keys"),
    pytest.param(
        {"source_genome": None, "target_genome": None},
        id="the-keys-explicit-yaml-nulls"),
])
def test_a_chain_declaring_no_genomes_is_silent(
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
    labels: Any,
) -> None:
    """Not declaring the genomes is not a curator mistake, so it is not one.

    The annotator takes both ids as parameters and falls back to the
    labels, so a chain that declares neither is a supported spelling --
    warning on it would fire on the resources that are already right.
    """
    resource = _a_liftover_chain_under_a_real_id(tmp_path, labels)

    with caplog.at_level(logging.WARNING):
        chain = LiftoverChain(resource)

    assert chain.source_genome_id is None
    assert chain.target_genome_id is None
    assert captured_warnings(caplog) == []


def test_two_caller_built_resources_keep_their_own_chain(
    tmp_path: pathlib.Path,
) -> None:
    """A caller built root resource is identified by the file it describes.

    ``build_local_resource`` roots the resource at the repository root, so
    two resources over one directory share an id and a repository url and
    differ only in their config.  Keying the memo on identity alone makes
    them collide -- the second caller is served the first caller's chain
    (#912).

    As above, constructing a chain never opens the chain file, so neither
    file needs to exist for the memo to be exercised.
    """
    hg19_resource = build_local_resource(str(tmp_path), {
        "type": "liftover_chain",
        "filename": "hg19_to_hg38.chain.gz",
        "meta": {"labels": {
            "source_genome": "hg19", "target_genome": "hg38"}},
    })
    hg38_resource = build_local_resource(str(tmp_path), {
        "type": "liftover_chain",
        "filename": "hg38_to_hg19.chain.gz",
        "meta": {"labels": {
            "source_genome": "hg38", "target_genome": "hg19"}},
    })

    hg19_chain = build_liftover_chain_from_resource(hg19_resource)
    hg38_chain = build_liftover_chain_from_resource(hg38_resource)

    assert hg19_chain.source_genome_id == "hg19"
    assert hg38_chain.source_genome_id == "hg38"
    assert hg19_chain.files == {"hg19_to_hg38.chain.gz"}
    assert hg38_chain.files == {"hg38_to_hg19.chain.gz"}


def test_one_chain_resource_is_still_memoised(
    tmp_path: pathlib.Path,
) -> None:
    """Telling two resources apart must not stop reuse of either."""
    resource = build_local_resource(str(tmp_path), {
        "type": "liftover_chain",
        "filename": "hg19_to_hg38.chain.gz",
    })

    first = build_liftover_chain_from_resource(resource)
    second = build_liftover_chain_from_resource(resource)

    assert first is second
