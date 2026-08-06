# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""gain's own reader for the 10x-Genomics HDF5 format.

Every test here runs in the small tier, where scanpy is deliberately
absent.  That absence is the point: these assert what gain reads, not
that gain called somebody else.  The live comparison against scanpy is
``tests/integration/test_ann_data_10x_h5.py``, which answers a different
question (did upstream move?) in the one tier that installs it.
"""
import logging
import pathlib
from typing import Any

import h5py
import pytest
from gain.genomic_resources.ann_data_resource import (
    load_ann_data_from_resource,
)
from gain.genomic_resources.implementations.ann_data_resource_impl import (
    AnnDataResourceImplementation,
)
from gain.genomic_resources.repository import GenomicResource
from gain.genomic_resources.testing.ann_data_builder import (
    AnnDataBuilder,
    an_ann_data,
)
from gain.genomic_resources.testing.builders import a_grr
from gain.task_graph.cli_tools import task_graph_run
from gain.task_graph.graph import TaskGraph
from gain.task_graph.sequential_executor import SequentialExecutor

_RESOURCE_ID = "single_cell/matrix"

_DESCRIBE_OBS = "statistics/describe_obs.csv"
_DESCRIBE_VAR = "statistics/describe_var.csv"
_DESCRIBE_ANN_DATA = "statistics/describe_ann_data.txt"

# The three datasets that hold the counts, and the only ones a matrix-free
# read must never touch.  On the largest real resource they are 151,320,994
# entries -- about 605 MB of ``data`` and 1.2 GB of ``indices``.
_MATRIX_DATASETS = ("data", "indices", "indptr")

# A CellRanger-ARC multiome shape -- two feature types, one of which
# ``gex_only`` drops.  ONE genome, which is what both real resources are.
# ``||`` escapes a space.
_MULTIOME_FEATURES = """
    gene     gene_name  feature_type      genome  interval
    ENSG001  ACTB       Gene||Expression  GRCh38  chr1:1-99
    ENSG002  GAPDH      Gene||Expression  GRCh38  chr1:200-299
    ENSG003  MT-ND1     Gene||Expression  GRCh38  NA
    PEAK001  chr1:1-99  Peaks             GRCh38  chr1:1-99
    PEAK002  chr2:1-99  Peaks             GRCh38  chr2:1-99
"""


def test_reads_a_10x_h5_without_scanpy(tmp_path: pathlib.Path) -> None:
    resource = (
        an_ann_data().with_format("10x_h5").build_resource(tmp_path)
    )

    ann_data = load_ann_data_from_resource(resource)

    # 10x stores the matrix as features x barcodes and says so in its
    # ``shape`` dataset; an AnnData is cells x genes, so what comes back
    # is the transpose of what is stored.
    assert ann_data.shape == (3, 4)
    assert list(ann_data.obs_names) == ["CELL_1", "CELL_2", "CELL_3"]
    assert list(ann_data.var_names) == ["ACTB", "GAPDH", "MALAT1", "XIST"]
    assert ann_data.X.todense().tolist() == [
        [4.0, 7.0, 10.0, 13.0],
        [5.0, 8.0, 11.0, 14.0],
        [6.0, 9.0, 12.0, 15.0],
    ]


def test_var_carries_the_named_fields_then_the_rest(
    tmp_path: pathlib.Path,
) -> None:
    # The three datasets 10x names -- ``id``, ``name``, ``feature_type`` --
    # become the index, ``gene_ids`` and ``feature_types``; every OTHER
    # dataset under ``features/`` is copied in verbatim after them.  The
    # column ORDER is asserted because ``describe_var.csv`` is a stored
    # statistic of every deployed resource, and its header is this order.
    resource = (
        an_ann_data().with_format("10x_h5").build_resource(tmp_path)
    )

    ann_data = load_ann_data_from_resource(resource)

    assert ann_data.var.to_csv().splitlines()[0] == (
        ",gene_ids,feature_types,genome,interval")
    assert list(ann_data.var["gene_ids"]) == [
        "ENSG001", "ENSG002", "ENSG003", "ENSG004"]
    assert list(ann_data.var["feature_types"]) == ["Gene Expression"] * 4
    assert list(ann_data.var["genome"]) == ["GRCh38"] * 4
    assert list(ann_data.var["interval"]) == [
        "chr1:1-1000", "chr1:1001-2000", "chr1:2001-3000", "chr1:3001-4000"]
    # ``_all_tag_keys`` is 10x's own index OF those metadata datasets, not
    # one of them, and a reader that copied it in would put a column of
    # the wrong length into every resource's statistics.
    assert "_all_tag_keys" not in ann_data.var.columns


def test_a_10x_read_has_no_per_cell_annotation(
    tmp_path: pathlib.Path,
) -> None:
    # 10x carries barcodes and nothing else about a cell, so ``obs`` has
    # an index and no columns -- which is what makes ``describe`` of it an
    # empty frame the statistics build declines to write.
    resource = (
        an_ann_data().with_format("10x_h5").build_resource(tmp_path)
    )

    ann_data = load_ann_data_from_resource(resource)

    assert list(ann_data.obs.columns) == []


@pytest.mark.parametrize(
    ("parameters", "expected_var_names"),
    [
        pytest.param(
            {}, ["ACTB", "GAPDH", "MT-ND1"], id="gex-only-by-default"),
        pytest.param(
            {"gex_only": False},
            ["ACTB", "GAPDH", "MT-ND1", "chr1:1-99", "chr2:1-99"],
            id="all-feature-types"),
    ],
)
def test_gex_only_filters_the_feature_types(
    tmp_path: pathlib.Path,
    parameters: dict[str, Any],
    expected_var_names: list[str],
) -> None:
    # The default is scanpy's and stays, so every existing resource keeps
    # the feature count its stored statistics record.
    resource = (
        an_ann_data()
        .with_format("10x_h5")
        .with_var(_MULTIOME_FEATURES)
        .with_parameters(parameters)
        .build_resource(tmp_path)
    )

    ann_data = load_ann_data_from_resource(resource)

    assert list(ann_data.var_names) == expected_var_names


def test_dropping_features_says_what_went(
    tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture,
) -> None:
    # The defect ADR 0014 names is the SILENCE, not the default: a
    # multiome resource read with ``gex_only`` on loses three quarters of
    # its features, and scanpy said nothing about it.
    resource = (
        an_ann_data()
        .with_format("10x_h5")
        .with_var(_MULTIOME_FEATURES)
        .build_resource(tmp_path)
    )

    with caplog.at_level(logging.WARNING):
        load_ann_data_from_resource(resource)

    assert "2 of its 5 features are dropped: Peaks (2)" in caplog.text


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


def _delete_the_counts(path: pathlib.Path) -> None:
    """Remove the three matrix datasets from a realized 10x h5.

    What is left is a file that describes its shape and its two axes and
    has no counts at all -- so a read that reaches for them raises, and
    "did not touch the matrix" becomes something a test can assert rather
    than something a reader can claim.
    """
    with h5py.File(path, "r+") as h5:
        for name in _MATRIX_DATASETS:
            del h5[f"matrix/{name}"]


def test_a_matrix_free_read_does_not_touch_the_counts(
    tmp_path: pathlib.Path,
) -> None:
    resource, path = _realize(an_ann_data(), tmp_path)
    _delete_the_counts(path)

    ann_data = load_ann_data_from_resource(resource, matrix_free=True)

    # The shape is the resource's, read from ``shape`` alone.
    assert ann_data.shape == (3, 4)
    assert ann_data.X.nnz == 0
    # ``_gen_repr`` emits its ``layers: None (.X)`` line only when X is
    # set, so a matrix-free read that left X unset would describe itself
    # differently from the full read it stands in for.
    assert ann_data.X is not None


def test_a_full_read_of_that_same_file_cannot_succeed(
    tmp_path: pathlib.Path,
) -> None:
    # The control for the test above: without it, a reader that silently
    # returned an empty matrix on ANY file would pass it.
    resource, path = _realize(an_ann_data(), tmp_path)
    _delete_the_counts(path)

    with pytest.raises(KeyError):
        load_ann_data_from_resource(resource)


def test_the_statistics_are_the_ones_scanpy_used_to_build(
    tmp_path: pathlib.Path,
) -> None:
    """The golden record, in the tier that runs on every commit.

    Both real ``10x_h5`` resources have statistics scanpy built, and this
    reader has to keep producing them byte for byte or every deployed
    resource silently changes the next time it is rebuilt.  The same
    bytes are asserted in ``tests/integration/test_ann_data_10x_h5.py``,
    which had to be written while scanpy was still installed; this is the
    copy that survives its removal, and the two agreeing is what says the
    replacement landed without moving the output.
    """
    resource, _path = _realize(
        an_ann_data().with_var(_MULTIOME_FEATURES), tmp_path)

    impl = AnnDataResourceImplementation(resource)
    graph = TaskGraph()
    graph.add_tasks(impl.create_statistics_build_tasks())
    task_graph_run(graph, SequentialExecutor())

    assert resource.get_file_content(_DESCRIBE_ANN_DATA) == (
        # The MULTIPLICATION SIGN is anndata's, and this is a byte-exact
        # record of what it wrote -- an ASCII "x" here would assert
        # something no resource in any GRR contains.
        "AnnData object with n_obs × n_vars = 3 × 3\n"  # noqa: RUF001
        "    var: 'gene_ids', 'feature_types', 'genome', 'interval'\n"
        "    layers: None (.X)\n"
    )
    assert resource.get_file_content(_DESCRIBE_VAR) == (
        ",gene_ids,feature_types,genome,interval\n"
        "count,3,3,3,3\n"
        "unique,3,1,1,3\n"
        "top,ENSG001,Gene Expression,GRCh38,chr1:1-99\n"
        "freq,1,3,3,1\n"
    )
    # 10x carries no per-cell annotation at all, so describing ``obs``
    # yields an empty frame, which the implementation declines to write.
    assert _DESCRIBE_OBS not in {
        entry.name for entry in resource.get_manifest()}
