# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""Compares gain's own 10x reader against the live scanpy one.

gain reads the matrix-market triple itself (ADR 0014), so the small tier
asserts what gain reads and never mentions scanpy.  These do the other
job: they hold gain's reader against the implementation it replaced, on
every axis the two are supposed to agree on -- the layout probe, the
variable index, ``make_unique``, ``gex_only``, and the matrix itself.

**A failure here means upstream moved, and someone has to decide -- it does
NOT mean gain regressed.**  gain's contract with its own resources is the
small tier and the statistics rebuilt from it; this is an early warning
that scanpy's semantics and gain's have parted company, which is worth
knowing deliberately rather than discovering from a re-annotated study.

Runs in the downstream ``gain-core-integration`` job, the one tier that
installs the ``scanpy-drift`` dependency group.  The main suite excludes
this directory by path, so no marker is needed.
"""
import pathlib
from typing import Any

import anndata as ad
import pytest
import scanpy
from gain.genomic_resources.ann_data_resource import (
    load_ann_data_from_resource,
    resolve_10x_layout,
)
from gain.genomic_resources.repository import GenomicResource
from gain.genomic_resources.testing.ann_data_builder import (
    AnnDataBuilder,
    an_ann_data,
)
from gain.genomic_resources.testing.builders import a_grr

# Realized at a known id so the directory scanpy is handed is a computed
# path.  Globbing for the matrix finds ``.grr/matrix.mtx.gz.state`` too --
# the protocol's state file, which sorts first.
_RESOURCE_ID = "single_cell/triple"

# ``||`` is the authored block's escape for a space.
_MULTIOME_FEATURES = """
    gene     gene_name  feature_type
    ENSG001  ACTB       Gene||Expression
    ENSG002  ACTB       Gene||Expression
    PEAK001  chr1:1-99  Peaks
    PEAK002  chr2:1-99  Peaks
"""


def _realize(
    builder: AnnDataBuilder, tmp_path: pathlib.Path,
) -> tuple[GenomicResource, pathlib.Path]:
    """Realize one 10x resource, returning it and the directory it is in."""
    repo = (
        a_grr()
        .with_resource(_RESOURCE_ID, builder.with_format("10x_mtx"))
        .build_repo(tmp_path)
    )
    return repo.get_resource(_RESOURCE_ID), tmp_path / _RESOURCE_ID


def _scanpy_read(directory: pathlib.Path, **params: Any) -> ad.AnnData:
    """Read the realized triple with scanpy, addressed as scanpy wants."""
    return scanpy.read_10x_mtx(str(directory), **params)


def _assert_same_read(ours: ad.AnnData, theirs: ad.AnnData) -> None:
    """Assert two reads of one triple agree on everything they carry."""
    assert ours.shape == theirs.shape
    assert list(ours.obs_names) == list(theirs.obs_names)
    assert list(ours.var_names) == list(theirs.var_names)
    assert ours.var.equals(theirs.var)
    assert (ours.X != theirs.X).nnz == 0
    # The rendering the ``describe_ann_data.txt`` statistic is made of.
    assert str(ours) == str(theirs)


@pytest.mark.parametrize(
    ("builder", "scanpy_params"),
    [
        pytest.param(an_ann_data(), {}, id="current"),
        pytest.param(
            an_ann_data().with_legacy_layout(), {}, id="legacy"),
        pytest.param(
            an_ann_data().with_uncompressed_layout(),
            {"compressed": False}, id="uncompressed"),
        pytest.param(
            an_ann_data().with_prefix("sample1_"),
            {"prefix": "sample1_"}, id="prefixed"),
        # ``gex_only`` is explicit wherever a multiome is read: its
        # DEFAULT is the one axis where the two readers deliberately
        # disagree -- gain reads the whole resource, scanpy filters
        # (ADR 0015) -- so only the filter itself is held against scanpy.
        pytest.param(
            an_ann_data().with_var(_MULTIOME_FEATURES),
            {"gex_only": True}, id="gex-only"),
        pytest.param(
            an_ann_data().with_var(_MULTIOME_FEATURES),
            {"gex_only": False}, id="all-feature-types"),
        pytest.param(
            an_ann_data().with_var(_MULTIOME_FEATURES),
            {"make_unique": False, "gex_only": False},
            id="duplicate-symbols-kept"),
        pytest.param(
            an_ann_data(), {"var_names": "gene_ids"}, id="by-gene-id"),
    ],
)
def test_gain_reads_a_triple_the_way_scanpy_does(
    tmp_path: pathlib.Path,
    builder: AnnDataBuilder,
    scanpy_params: dict[str, Any],
) -> None:
    # gain's parameters are the resource's; scanpy's are the call's.  The
    # two spellings of the same request have to produce the same AnnData --
    # except ``prefix`` and ``compressed``, which gain derives from the
    # resource and scanpy has to be told.
    gain_params = {
        name: value for name, value in scanpy_params.items()
        if name not in ("prefix", "compressed")
    }
    resource, directory = _realize(
        builder.with_parameters(gain_params), tmp_path)

    ours = load_ann_data_from_resource(resource)

    _assert_same_read(ours, _scanpy_read(directory, **scanpy_params))


def test_the_matrix_free_read_describes_what_scanpy_reads(
    tmp_path: pathlib.Path,
) -> None:
    """The claim the statistics build rests on, against the real oracle.

    The small tier compares the matrix-free read to gain's own full read,
    which would stay green if both drifted together.  This compares it to
    scanpy's, told explicitly to read the whole resource -- the defaults
    of the two readers diverge on purpose (ADR 0015), and what is held
    here is the read, not the default.
    """
    resource, directory = _realize(
        an_ann_data().with_var(_MULTIOME_FEATURES), tmp_path)
    theirs = _scanpy_read(directory, gex_only=False)

    lean = load_ann_data_from_resource(resource, matrix_free=True)

    assert str(lean) == str(theirs)
    assert lean.var.equals(theirs.var)
    assert list(lean.obs_names) == list(theirs.obs_names)
    # And the one thing it deliberately does NOT reproduce.
    assert lean.X.nnz == 0
    assert theirs.X.nnz > 0


@pytest.mark.parametrize(
    ("builder", "matrix", "sidecars"),
    [
        pytest.param(
            an_ann_data(), "matrix.mtx.gz",
            {"barcodes.tsv.gz", "features.tsv.gz"}, id="current"),
        pytest.param(
            an_ann_data().with_legacy_layout(), "matrix.mtx",
            {"barcodes.tsv", "genes.tsv"}, id="legacy"),
        pytest.param(
            an_ann_data().with_uncompressed_layout(), "matrix.mtx",
            {"barcodes.tsv", "features.tsv"}, id="uncompressed"),
    ],
)
def test_the_resolved_sidecars_are_the_ones_scanpy_reads(
    tmp_path: pathlib.Path,
    builder: AnnDataBuilder,
    matrix: str,
    sidecars: set[str],
) -> None:
    # The statistics hash and the cache prefetch both come from this
    # enumeration, and getting it wrong is silent: the resource still
    # loads, and only the staleness the enumeration exists to prevent
    # comes back.  So the names are checked against the files scanpy
    # itself opened.
    resource, directory = _realize(builder, tmp_path)
    scanpy_read = _scanpy_read(
        directory, compressed=matrix.endswith(".gz"))

    assert resolve_10x_layout(
        resource.get_manifest(), matrix).sidecars == sidecars
    assert scanpy_read.shape == (3, 4)
    for sidecar in sidecars:
        assert (directory / sidecar).exists()
