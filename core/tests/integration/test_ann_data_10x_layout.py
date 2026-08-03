# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""Pins the 10x layout rule the sidecar resolution mirrors.

``resolve_10x_sidecars`` decides between the two CellRanger layouts by
asking whether a ``genes.tsv`` sits beside the matrix -- which is what
``scanpy.read_10x_mtx`` itself does.  Nothing in the small tier can falsify
that belief: scanpy is the optional ``gain-core[ann_data_10x]`` extra and is
deliberately absent there, so the 10x tests reach it through a stub.

Getting the rule wrong is silent.  scanpy still finds its own files and the
resource still loads; only our statistics hash and our cache prefetch go
stale, which is precisely the staleness the sidecar enumeration exists to
prevent.  So the belief is checked here, against the real reader, in the
tier that installs the extra (see ``core/Jenkinsfile.integration``).

Runs in the downstream ``gain-core-integration`` job.  The main suite
excludes this directory by path, so no marker is needed.
"""
import pathlib

import scanpy
from gain.genomic_resources.ann_data_resource import (
    load_ann_data_from_resource,
    resolve_10x_sidecars,
)
from gain.genomic_resources.testing.ann_data_builder import an_ann_data


def _resolved(resource: object, file_name: str) -> set[str]:
    return resolve_10x_sidecars(
        resource.get_manifest(), file_name)  # type: ignore[attr-defined]


def test_scanpy_reads_the_current_layout_we_resolve(
    tmp_path: pathlib.Path,
) -> None:
    resource = (
        an_ann_data().with_format("10x_mtx").build_resource(tmp_path)
    )

    ann_data = load_ann_data_from_resource(resource)

    assert ann_data.shape == (3, 4)
    assert _resolved(resource, "matrix.mtx.gz") == {
        "barcodes.tsv.gz", "features.tsv.gz"}


def test_scanpy_reads_the_legacy_layout_we_resolve(
    tmp_path: pathlib.Path,
) -> None:
    # The v2 spelling. If scanpy ever stopped keying off genes.tsv, this is
    # where it would show: the read would fail, or the names we resolve
    # would no longer be the names it read.
    resource = (
        an_ann_data()
        .with_format("10x_mtx")
        .with_legacy_layout()
        .build_resource(tmp_path)
    )

    ann_data = load_ann_data_from_resource(resource)

    assert ann_data.shape == (3, 4)
    assert _resolved(resource, "matrix.mtx") == {
        "barcodes.tsv", "genes.tsv"}


def test_scanpy_still_probes_for_genes_tsv(tmp_path: pathlib.Path) -> None:
    """The rule itself, asserted against scanpy rather than restated.

    A directory carrying ``genes.tsv`` is legacy to scanpy no matter what
    the matrix member is called; that probe is the whole basis of
    ``resolve_10x_sidecars``.
    """
    an_ann_data() \
        .with_format("10x_mtx") \
        .with_legacy_layout() \
        .build_resource(tmp_path)
    matrix = next(tmp_path.rglob("matrix.mtx"))
    assert (matrix.parent / "genes.tsv").exists()

    read = scanpy.read_10x_mtx(str(matrix.parent))

    assert read.shape == (3, 4)
