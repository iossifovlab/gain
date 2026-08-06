# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""Holds the ``10x_h5`` fixture against the live ``scanpy.read_10x_h5``.

The small tier asserts what the builder writes, structurally, through
h5py -- it has no scanpy.  This asserts the thing that makes those bytes
worth writing: that the reader they exist to feed accepts them, and
produces the AnnData the fixture claims to describe.

**This has to exist before scanpy is removed** (ADR 0014).  gain does not
read ``10x_h5`` itself yet, so today the loader below reaches scanpy and
these are fixture-validity tests.  When gain's own reader lands they
become the drift test for it, unchanged -- which is the point of writing
them at the loader rather than at scanpy.

Runs in the downstream ``gain-core-integration`` job, the one tier that
installs the ``gain-core[ann_data_10x]`` extra.  The main suite excludes
this directory by path, so no marker is needed.
"""
import pathlib
from typing import Any

import anndata as ad
import pytest
import scanpy
from gain.genomic_resources.ann_data_resource import (
    load_ann_data_from_resource,
)
from gain.genomic_resources.repository import GenomicResource
from gain.genomic_resources.testing.ann_data_builder import (
    AnnDataBuilder,
    an_ann_data,
)
from gain.genomic_resources.testing.builders import a_grr

_RESOURCE_ID = "single_cell/matrix"

# A CellRanger-ARC multiome shape -- two feature types, one of which
# ``gex_only`` drops.  ``||`` is the authored block's escape for a space.
_MULTIOME_FEATURES = """
    gene     gene_name  feature_type      genome   interval
    ENSG001  ACTB       Gene||Expression  GRCh38   chr1:1-99
    ENSG002  GAPDH      Gene||Expression  GRCh38   chr1:200-299
    PEAK001  chr1:1-99  Peaks             GRCh38m  chr1:1-99
    PEAK002  chr2:1-99  Peaks             GRCh38m  chr2:1-99
"""


def _realize(
    builder: AnnDataBuilder, tmp_path: pathlib.Path,
) -> tuple[GenomicResource, pathlib.Path]:
    """Realize one 10x_h5 resource, returning it and the file's path."""
    repo = (
        a_grr()
        .with_resource(_RESOURCE_ID, builder.with_format("10x_h5"))
        .build_repo(tmp_path)
    )
    resource = repo.get_resource(_RESOURCE_ID)
    return resource, tmp_path / _RESOURCE_ID / resource.get_config()["file"]


def _scanpy_read(path: pathlib.Path, **params: Any) -> ad.AnnData:
    """Read the realized file with scanpy, addressed as scanpy wants."""
    return scanpy.read_10x_h5(path, **params)


def test_scanpy_reads_the_realized_fixture(tmp_path: pathlib.Path) -> None:
    # The whole claim of the fixture, in one assertion set: the authored
    # obs and var blocks come back as the barcodes and features, and the
    # matrix comes back in the AnnData orientation -- cells x genes --
    # from buffers stored as the transpose.
    _resource, path = _realize(an_ann_data(), tmp_path)

    ann_data = _scanpy_read(path)

    assert ann_data.shape == (3, 4)
    assert list(ann_data.obs_names) == ["CELL_1", "CELL_2", "CELL_3"]
    assert list(ann_data.var_names) == ["ACTB", "GAPDH", "MALAT1", "XIST"]
    assert ann_data.X.todense().tolist() == [
        [4.0, 7.0, 10.0, 13.0],
        [5.0, 8.0, 11.0, 14.0],
        [6.0, 9.0, 12.0, 15.0],
    ]


def test_the_var_table_is_the_shape_the_stored_statistics_have(
    tmp_path: pathlib.Path,
) -> None:
    # The two real ``10x_h5`` resources have statistics that scanpy built,
    # and a reader replacing it has to reproduce them byte for byte.  This
    # pins the header those files carry -- ``genome`` and ``interval`` are
    # in it because a read copies every ``features/`` dataset it does not
    # recognise into ``var``, and a fixture that omitted them would let a
    # reader that drops them look correct.
    _resource, path = _realize(an_ann_data(), tmp_path)

    ann_data = _scanpy_read(path)

    assert ann_data.var.to_csv().splitlines()[0] == (
        ",gene_ids,feature_types,genome,interval")
    assert list(ann_data.var["gene_ids"]) == [
        "ENSG001", "ENSG002", "ENSG003", "ENSG004"]
    assert list(ann_data.var["genome"]) == ["GRCh38"] * 4
    assert list(ann_data.var["interval"]) == [
        "chr1:1-1000", "chr1:1001-2000", "chr1:2001-3000", "chr1:3001-4000"]


@pytest.mark.parametrize(
    ("params", "expected_var_names"),
    [
        pytest.param({}, ["ACTB", "GAPDH"], id="gex-only-by-default"),
        pytest.param(
            {"gex_only": False},
            ["ACTB", "GAPDH", "chr1:1-99", "chr2:1-99"],
            id="all-feature-types"),
    ],
)
def test_gex_only_filters_the_realized_feature_types(
    tmp_path: pathlib.Path,
    params: dict[str, Any],
    expected_var_names: list[str],
) -> None:
    # ``gex_only`` defaults to True, and the stored feature count of both
    # real resources is post-filter -- so a fixture that cannot carry a
    # second feature type cannot make the default observable at all.
    _resource, path = _realize(
        an_ann_data().with_var(_MULTIOME_FEATURES), tmp_path)

    ann_data = _scanpy_read(path, **params)

    assert list(ann_data.var_names) == expected_var_names


def test_the_loader_reads_a_10x_h5_resource(tmp_path: pathlib.Path) -> None:
    # Through the resource, which is how gain reaches the bytes: the
    # config's ``file:`` names the h5, the ``.h5`` suffix resolves the
    # format with no ``format:`` key needed, and ``parameters:`` is the
    # resource's spelling of the read's arguments.
    resource, path = _realize(
        an_ann_data().with_var(_MULTIOME_FEATURES)
        .with_parameters({"gex_only": False}),
        tmp_path)

    ours = load_ann_data_from_resource(resource)

    theirs = _scanpy_read(path, gex_only=False)
    assert ours.shape == theirs.shape
    assert list(ours.var_names) == list(theirs.var_names)
    assert ours.var.equals(theirs.var)
    assert (ours.X != theirs.X).nnz == 0
    # The rendering the ``describe_ann_data.txt`` statistic is made of.
    assert str(ours) == str(theirs)
