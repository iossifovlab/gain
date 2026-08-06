# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""What ``an_ann_data().with_format("10x_h5")`` puts on disk.

The fixture is asserted here through h5py, one structural claim at a
time, because the small tier has no scanpy: the file is the deliverable,
and a reader gain does not have yet cannot stand in for the assertions.
That the *live* scanpy accepts what is realized here is the other half,
and it is a drift test -- ``tests/integration/test_ann_data_10x_h5.py``.

Only one layout is realized: the modern single ``matrix`` group, feature
barcode, one genome, which is what both ``10x_h5`` resources we have
are (#707).
"""
import gzip
import pathlib

import h5py
import pytest
import scipy.io
from gain.genomic_resources.testing.ann_data_builder import (
    AnnDataBuilder,
    an_ann_data,
)
from gain.genomic_resources.testing.builders import a_grr
from gain.genomic_resources.testing.score_specs import (
    ResourceValidationError,
)
from scipy.sparse import csr_matrix

# A CellRanger-ARC multiome shape: two feature types, and per-feature
# metadata authored rather than defaulted.  ONE genome, including for the
# peaks -- which is what both real resources are, and a second genome
# would realize the multi-genome file ADR 0014 puts out of scope.  ``||``
# is the authored block's escape for a space.
_MULTIOME_FEATURES = """
    gene     gene_name  feature_type      genome  interval
    ENSG001  ACTB       Gene||Expression  GRCh38  chr1:1-99
    ENSG002  GAPDH      Gene||Expression  GRCh38  chr1:200-299
    PEAK001  chr1:1-99  Peaks             GRCh38  chr1:1-99
    PEAK002  chr2:1-99  Peaks             GRCh38  chr2:1-99
"""

# One genome still, but not the default one -- which is what makes an
# authored ``genome`` column distinguishable from the value that stands
# in for it.
_MOUSE_FEATURES = """
    gene     gene_name  genome
    ENSM001  Actb       mm39
    ENSM002  Gapdh      mm39
"""


def _realize(
    builder: AnnDataBuilder, tmp_path: pathlib.Path,
) -> pathlib.Path:
    """Realize ``builder`` and return the path of the file ``file:`` names."""
    resource = builder.build_resource(tmp_path)
    return tmp_path / resource.get_config()["file"]


def _strings(dataset: h5py.Dataset) -> list[str]:
    """Decode a 10x byte-string dataset the way a reader's ``astype`` does."""
    return [value.decode() for value in dataset[:]]


def test_realizes_a_single_h5_file(tmp_path: pathlib.Path) -> None:
    # One file, unlike the triple: everything a 10x h5 carries is inside
    # it, so there is nothing to resolve and nothing to keep in step.
    resource = an_ann_data().with_format("10x_h5").build_resource(tmp_path)
    path = tmp_path / resource.get_config()["file"]

    assert path.name == "matrix.h5"
    assert path.is_file()
    # Through the manifest rather than the directory, which also carries
    # the protocol's own bookkeeping.
    assert sorted(entry.name for entry in resource.get_manifest()) == [
        "genomic_resource.yaml", "matrix.h5"]


def test_the_file_is_the_modern_single_matrix_group_layout(
    tmp_path: pathlib.Path,
) -> None:
    # ``read_10x_h5`` picks its parser by probing for ``/matrix``: present
    # is the modern feature-barcode file, absent means the legacy layout
    # whose root carries genome-named groups instead.  Realizing the
    # modern one is the whole point of the fixture.
    path = _realize(an_ann_data().with_format("10x_h5"), tmp_path)

    with h5py.File(path, "r") as h5:
        assert list(h5.keys()) == ["matrix"]


@pytest.mark.parametrize(
    "dataset",
    ["barcodes", "data", "indices", "indptr", "shape", "features"])
def test_the_matrix_group_carries_the_members_a_read_requires(
    tmp_path: pathlib.Path, dataset: str,
) -> None:
    # Each of these is a read that fails outright when it is missing --
    # the five datasets with "missing one or more required datasets", and
    # the features group with "10x h5 has no features group".
    path = _realize(an_ann_data().with_format("10x_h5"), tmp_path)

    with h5py.File(path, "r") as h5:
        assert dataset in h5["matrix"]


def test_the_stored_shape_is_features_by_barcodes(
    tmp_path: pathlib.Path,
) -> None:
    # The authored default is 3 cells x 4 genes, and 10x stores the
    # TRANSPOSE of what a reader hands back.  This is the one number that
    # decides whether the matrix-free read of this format is right, since
    # the shape is a dataset here rather than a header to parse.
    path = _realize(an_ann_data().with_format("10x_h5"), tmp_path)

    with h5py.File(path, "r") as h5:
        assert list(h5["matrix/shape"][:]) == [4, 3]


def test_the_stored_buffers_are_the_csr_of_barcodes_by_features(
    tmp_path: pathlib.Path,
) -> None:
    # 10x stores the CSC of features x barcodes, which is bit-for-bit the
    # CSR of barcodes x features -- so a reader hands these three buffers
    # to ``csr_matrix`` with no transposition at all.  Asserting the dense
    # result outright is what pins the orientation: a builder that wrote
    # the genuine transpose would still produce a well-formed file, and
    # every structural assertion above would still pass.
    path = _realize(an_ann_data().with_format("10x_h5"), tmp_path)

    with h5py.File(path, "r") as h5:
        matrix = csr_matrix(
            (h5["matrix/data"][:],
             h5["matrix/indices"][:],
             h5["matrix/indptr"][:]),
            shape=(3, 4))

    assert matrix.todense().tolist() == [
        [4, 7, 10, 13],
        [5, 8, 11, 14],
        [6, 9, 12, 15],
    ]


def test_the_features_group_carries_the_authored_var_block(
    tmp_path: pathlib.Path,
) -> None:
    # The same positional convention the triple's feature table uses: the
    # authored index is the gene id, ``gene_name`` is the symbol and
    # ``feature_type`` is the type.  One authored block, two formats.
    path = _realize(an_ann_data().with_format("10x_h5"), tmp_path)

    with h5py.File(path, "r") as h5:
        features = h5["matrix/features"]

        assert _strings(features["id"]) == [
            "ENSG001", "ENSG002", "ENSG003", "ENSG004"]
        assert _strings(features["name"]) == [
            "ACTB", "GAPDH", "MALAT1", "XIST"]
        assert _strings(features["feature_type"]) == ["Gene Expression"] * 4


def test_the_features_group_carries_the_metadata_a_read_copies_into_var(
    tmp_path: pathlib.Path,
) -> None:
    # A reader copies every ``features/`` dataset into ``var`` EXCEPT
    # ``name``, ``feature_type``, ``id``, ``gene_id`` and
    # ``_all_tag_keys`` -- which is why the statistics of both our real
    # resources carry ``genome`` and ``interval`` columns.  A fixture
    # without them would let a reader that drops them look correct.
    path = _realize(an_ann_data().with_format("10x_h5"), tmp_path)

    with h5py.File(path, "r") as h5:
        features = h5["matrix/features"]

        assert _strings(features["genome"]) == ["GRCh38"] * 4
        # Distinct per feature, so a read that misaligns them is caught.
        assert _strings(features["interval"]) == [
            "chr1:1-1000",
            "chr1:1001-2000",
            "chr1:2001-3000",
            "chr1:3001-4000",
        ]


def test_all_tag_keys_names_the_metadata_datasets(
    tmp_path: pathlib.Path,
) -> None:
    # 10x's own index of the per-feature metadata.  A reader skips it
    # rather than copying it into ``var``, so it is asserted for
    # faithfulness -- and derived from the datasets it names, so the two
    # cannot drift.
    path = _realize(an_ann_data().with_format("10x_h5"), tmp_path)

    with h5py.File(path, "r") as h5:
        assert _strings(h5["matrix/features/_all_tag_keys"]) == [
            "genome", "interval"]


def test_more_than_one_feature_type_is_expressible(
    tmp_path: pathlib.Path,
) -> None:
    # Both real ``10x_h5`` resources are CellRanger-ARC multiome files
    # carrying Gene Expression AND Peaks, and their stored statistics are
    # post-filter -- so a fixture that cannot express a second feature
    # type cannot make ``gex_only`` observable at all.
    path = _realize(
        an_ann_data().with_format("10x_h5").with_var(_MULTIOME_FEATURES),
        tmp_path)

    with h5py.File(path, "r") as h5:
        assert _strings(h5["matrix/features/feature_type"]) == [
            "Gene Expression", "Gene Expression", "Peaks", "Peaks"]


def test_the_per_feature_metadata_may_be_authored(
    tmp_path: pathlib.Path,
) -> None:
    # ``genome`` and ``interval`` follow the same authoring convention as
    # ``gene_name`` and ``feature_type``: a column of the ``var`` block,
    # defaulted when absent.  Both authored values differ from what stands
    # in for them, so this cannot pass by accident.
    path = _realize(
        an_ann_data().with_format("10x_h5").with_var(_MULTIOME_FEATURES),
        tmp_path)

    with h5py.File(path, "r") as h5:
        assert _strings(h5["matrix/features/interval"]) == [
            "chr1:1-99", "chr1:200-299", "chr1:1-99", "chr2:1-99"]

    path = _realize(
        an_ann_data().with_format("10x_h5").with_var(_MOUSE_FEATURES),
        tmp_path / "mouse")

    with h5py.File(path, "r") as h5:
        assert _strings(h5["matrix/features/genome"]) == ["mm39", "mm39"]


@pytest.mark.parametrize(
    "builder",
    [
        pytest.param(
            an_ann_data().with_format("10x_h5").with_legacy_layout(),
            id="legacy"),
        pytest.param(
            an_ann_data().with_format("10x_h5").with_uncompressed_layout(),
            id="uncompressed"),
        pytest.param(
            an_ann_data().with_format("10x_h5").with_prefix("sample1_"),
            id="prefix"),
    ],
)
def test_the_triple_only_options_are_refused(
    tmp_path: pathlib.Path, builder: AnnDataBuilder,
) -> None:
    # All three describe the matrix-market triple: which of its two
    # generations to write, whether to gzip it, and the prefix its three
    # members share.  A ``10x_h5`` is ONE file with none of those
    # questions -- accepting them silently would realize a fixture that
    # ignores what the test asked for, which is the failure mode the
    # equivalent h5ad guard exists to prevent.
    with pytest.raises(ResourceValidationError, match="single file"):
        builder.build_resource(tmp_path)


def test_a_probe_barcode_var_block_is_refused(
    tmp_path: pathlib.Path,
) -> None:
    # ``gene_id`` is what a reader keys the probe-barcode branch on, and
    # that branch is deliberately not realized.  Dropping the column
    # silently would hand a feature-barcode file to a test that asked for
    # the other kind -- and it would pass, because the file is valid.
    probe_features = """
        gene     gene_name  gene_id
        PROBE01  ACTB       ENSG001
        PROBE02  GAPDH      ENSG002
    """

    with pytest.raises(ResourceValidationError, match="probe-barcode"):
        (
            an_ann_data()
            .with_format("10x_h5")
            .with_var(probe_features)
            .build_resource(tmp_path)
        )


def test_it_composes_into_a_multi_resource_repo(
    tmp_path: pathlib.Path,
) -> None:
    # Every builder has to compose the same way, and the ann_data ones
    # reach the filesystem through a content dict rather than by writing
    # files themselves -- so a format that realized bytes correctly but
    # returned them in a shape ``GRRBuilder`` could not place would still
    # pass every assertion above.
    repo = (
        a_grr()
        .with_resource("single_cell/matrix", an_ann_data().with_format(
            "10x_h5"))
        .with_resource("single_cell/triple", an_ann_data().with_format(
            "10x_mtx"))
        .build_repo(tmp_path)
    )

    resource = repo.get_resource("single_cell/matrix")

    assert resource.get_type() == "ann_data"
    assert resource.get_config()["file"] == "matrix.h5"
    assert "matrix.h5" in {
        entry.name for entry in resource.get_manifest()}
    with h5py.File(tmp_path / "single_cell/matrix" / "matrix.h5", "r") as h5:
        assert "matrix" in h5


def test_the_realized_file_may_be_renamed(tmp_path: pathlib.Path) -> None:
    # Unlike the triple, which is addressed by prefix, a 10x h5 is a
    # single file -- so ``with_file`` renames it, as it does an h5ad.  The
    # real resources are named after their GEO accession, not ``matrix``.
    path = _realize(
        an_ann_data()
        .with_format("10x_h5")
        .with_file("GSE278576_hc73_raw_feature_bc_matrix.h5"),
        tmp_path)

    assert path.name == "GSE278576_hc73_raw_feature_bc_matrix.h5"
    with h5py.File(path, "r") as h5:
        assert "matrix" in h5


def test_the_two_10x_formats_of_one_builder_carry_the_same_matrix(
    tmp_path: pathlib.Path,
) -> None:
    # The h5 and the triple are two encodings of one authored dataset, and
    # a test that reads the same fixture through both formats is only
    # meaningful if they agree.  Matrix Market is features x barcodes and
    # 1-indexed; the h5 buffers are already barcodes x features.
    builder = an_ann_data()
    h5_path = _realize(builder.with_format("10x_h5"), tmp_path / "as_h5")
    mtx_path = _realize(
        builder.with_format("10x_mtx"), tmp_path / "as_mtx")

    with gzip.open(mtx_path, "rb") as stream:
        # ``spmatrix=False`` asks for the sparse ARRAY outright; the
        # default is mid-migration and warns.
        from_mtx = scipy.io.mmread(stream, spmatrix=False).todense().T
    with h5py.File(h5_path, "r") as h5:
        from_h5 = csr_matrix(
            (h5["matrix/data"][:],
             h5["matrix/indices"][:],
             h5["matrix/indptr"][:]),
            shape=(3, 4)).todense()

    assert from_h5.tolist() == from_mtx.tolist()
