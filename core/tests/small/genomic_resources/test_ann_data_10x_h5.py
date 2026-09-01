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
import warnings
from typing import Any

import h5py
import numpy as np
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

_DESCRIBE_VAR = "statistics/describe_var.csv"
_DESCRIBE_ANN_DATA = "statistics/describe_ann_data.txt"

# The three datasets that hold the counts, and the only ones a matrix-free
# read must never touch.  On the largest real resource they are 151,320,994
# entries -- about 605 MB of ``data`` and 1.2 GB of ``indices``.
_MATRIX_DATASETS = ("data", "indices", "indptr")

# The two groups a modern 10x h5 is built round.
_MATRIX_GROUP = "matrix"
_FEATURES_GROUP = "features"

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

# Two distinct accessions carrying the same symbol -- the ordinary reason
# a 10x feature table has a non-unique variable index, and the shape EVERY
# real ``10x_h5`` resource we have is in: ``TBCE`` is one of the ten
# symbols that repeat in ``anndata/zemke2024Epigenetic`` (#715).
#
# A block of its own rather than a repeat added to ``_MULTIOME_FEATURES``,
# which the byte-exact statistics pin at the end of this file is written
# against: moving that record for an unrelated reason invites re-blessing
# its bytes, which is the failure these tests exist to make loud.
#
# The repeats are deliberately NOT adjacent, so that "in file order" is
# something the assertions can see -- a reader that grouped them together
# would still keep both.  The ``Peaks`` row keeps the block a multiome,
# as the real files are, and gives ``gex_only`` something to actually
# drop: setting the parameter is what takes the filtering path, but a
# filter that removed nothing would be a poor witness for what happens
# to the index when one does.  ``||`` escapes a space.
_DUPLICATE_SYMBOLS = """
    gene     gene_name  feature_type      genome  interval
    ENSG001  TBCE       Gene||Expression  GRCh38  chr1:1-99
    ENSG002  GAPDH      Gene||Expression  GRCh38  chr1:200-299
    ENSG003  TBCE       Gene||Expression  GRCh38  chr17:100-199
    PEAK001  chr1:1-99  Peaks             GRCh38  chr1:1-99
"""

_NOT_UNIQUE = r"Variable names are not unique"


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


def test_the_counts_come_back_as_float32(tmp_path: pathlib.Path) -> None:
    # CellRanger stores counts as int32 and scanpy handed back float32,
    # so every consumer of a 10x resource has been given floats.  Pinned
    # because nothing else here can see it: an int32 and a float32 matrix
    # of the same counts compare equal element-wise and describe
    # themselves identically, so a dropped conversion is invisible to
    # every other assertion in this file.
    resource = (
        an_ann_data().with_format("10x_h5").build_resource(tmp_path)
    )

    ann_data = load_ann_data_from_resource(resource)

    assert ann_data.X.dtype == np.float32


def test_a_matrix_free_read_matches_that_dtype(
    tmp_path: pathlib.Path,
) -> None:
    # The empty matrix stands in for the real one, so it has to be the
    # same kind of matrix -- otherwise the statistics build describes
    # something the full read would not produce.
    resource, path = _realize(an_ann_data(), tmp_path)
    _delete_the_counts(path)

    ann_data = load_ann_data_from_resource(resource, matrix_free=True)

    assert ann_data.X.dtype == np.float32


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
    # Absent a ``filtered_barcodes`` flag -- which neither real resource
    # has -- 10x says nothing about a cell but its barcode, so ``obs``
    # has an index and no columns.  That is what makes ``describe`` of it
    # an empty frame the statistics build declines to write.
    resource = (
        an_ann_data().with_format("10x_h5").build_resource(tmp_path)
    )

    ann_data = load_ann_data_from_resource(resource)

    assert list(ann_data.obs.columns) == []


@pytest.mark.parametrize(
    ("parameters", "expected_var_names"),
    [
        pytest.param(
            {}, ["ACTB", "GAPDH", "MT-ND1", "chr1:1-99", "chr2:1-99"],
            id="whole-resource-by-default"),
        pytest.param(
            {"gex_only": True},
            ["ACTB", "GAPDH", "MT-ND1"],
            id="gene-expression-on-request"),
    ],
)
def test_gex_only_filters_the_feature_types(
    tmp_path: pathlib.Path,
    parameters: dict[str, Any],
    expected_var_names: list[str],
) -> None:
    # A default read returns the whole resource (ADR 0015); filtering a
    # multiome down to its genes is opt-in curation, stated in the config.
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
    # A config asking for the filter states its intent, so the drop is
    # reported as plain forensics, at info -- what a log reader needs is
    # what shape the data took and why.
    resource = (
        an_ann_data()
        .with_format("10x_h5")
        .with_var(_MULTIOME_FEATURES)
        .with_parameters({"gex_only": True})
        .build_resource(tmp_path)
    )

    with caplog.at_level(logging.INFO):
        load_ann_data_from_resource(resource)

    assert "2 of its 5 features are dropped: Peaks (2)" in caplog.text
    assert [
        record.levelno for record in caplog.records
        if "gex_only" in record.getMessage()
    ] == [logging.INFO]


class TestParameters:
    """``parameters:`` is a surface gain defines, not a passthrough.

    The h5 read takes fewer knobs than the triple's, because the file
    answers for itself what the triple needs telling: it names its own
    features, so there is no ``var_names`` choice and nothing to make
    unique.  A key that used to reach ``scanpy.read_10x_h5`` and has no
    meaning here is reported rather than quietly ignored.
    """

    def test_an_unknown_key_is_reported_with_what_is_accepted(
        self, tmp_path: pathlib.Path,
    ) -> None:
        resource = (
            an_ann_data()
            .with_format("10x_h5")
            .with_parameters({"gex_onyl": True})
            .build_resource(tmp_path)
        )

        with pytest.raises(ValueError, match="unknown 10x_h5 parameter") \
                as excinfo:
            load_ann_data_from_resource(resource)

        assert "gex_onyl" in str(excinfo.value)
        assert "gain accepts genome, gex_only" in str(excinfo.value)

    def test_a_triple_only_key_is_not_silently_accepted(
        self, tmp_path: pathlib.Path,
    ) -> None:
        # ``var_names`` is a real knob -- of the OTHER 10x reader.  An h5
        # names its own features, so honouring it here would mean
        # inventing behaviour scanpy never had.
        resource = (
            an_ann_data()
            .with_format("10x_h5")
            .with_parameters({"var_names": "gene_ids"})
            .build_resource(tmp_path)
        )

        with pytest.raises(ValueError, match="unknown 10x_h5 parameter"):
            load_ann_data_from_resource(resource)

    def test_refuses_reading_off_repository_bytes(
        self, tmp_path: pathlib.Path,
    ) -> None:
        # ``backup_url`` downloads the file from an arbitrary URL when it
        # is not on disk.  Refused on principle: a resource that can
        # silently read bytes that are not in the manifest, not hashed
        # and not served by the repository is not a resource.
        resource = (
            an_ann_data()
            .with_format("10x_h5")
            .with_parameters({"backup_url": "https://example.org/m.h5"})
            .build_resource(tmp_path)
        )

        with pytest.raises(ValueError, match="is refused") as excinfo:
            load_ann_data_from_resource(resource)

        assert "not in the manifest" in str(excinfo.value)

    def test_gex_only_must_be_a_boolean(
        self, tmp_path: pathlib.Path,
    ) -> None:
        # yaml's ``gex_only: "false"`` is a non-empty string, which is
        # true -- the opposite of what the author meant.
        resource = (
            an_ann_data()
            .with_format("10x_h5")
            .with_parameters({"gex_only": "false"})
            .build_resource(tmp_path)
        )

        with pytest.raises(ValueError, match="must be true or false"):
            load_ann_data_from_resource(resource)

    def test_a_genome_the_file_carries_selects_its_features(
        self, tmp_path: pathlib.Path,
    ) -> None:
        # Our resources carry one genome, so naming it selects
        # everything.  What is being pinned is that the key is honoured
        # at all rather than ignored.
        resource = (
            an_ann_data()
            .with_format("10x_h5")
            .with_parameters({"genome": "GRCh38"})
            .build_resource(tmp_path)
        )

        ann_data = load_ann_data_from_resource(resource)

        assert list(ann_data.var_names) == [
            "ACTB", "GAPDH", "MALAT1", "XIST"]

    def test_a_genome_the_file_does_not_carry_is_reported(
        self, tmp_path: pathlib.Path,
    ) -> None:
        resource = (
            an_ann_data()
            .with_format("10x_h5")
            .with_parameters({"genome": "hg19"})
            .build_resource(tmp_path)
        )

        with pytest.raises(ValueError, match="which its file does not carry") \
                as excinfo:
            load_ann_data_from_resource(resource)

        assert "available: GRCh38" in str(excinfo.value)


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
            del h5[f"{_MATRIX_GROUP}/{name}"]


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


def _add_filtered_barcodes(path: pathlib.Path) -> None:
    """Add the per-cell flag CellRanger's cell-calling runs write.

    A boolean dataset beside ``barcodes``, marking which of them the
    caller kept.  The builder does not realize it -- no resource we have
    carries one -- so it is written here directly.
    """
    with h5py.File(path, "r+") as h5:
        h5[_MATRIX_GROUP].create_dataset(
            "filtered_barcodes", data=np.array([True, False, True]))


def test_a_filtered_barcodes_flag_becomes_a_cell_annotation(
    tmp_path: pathlib.Path,
) -> None:
    # scanpy read this into ``obs``, so a resource carrying one has
    # statistics that describe it -- a ``describe_obs.csv``, and an
    # ``obs:`` line in ``describe_ann_data.txt``.  Dropping it would be
    # exactly the silent mis-read ADR 0014 exists to prevent: not
    # reproduced, and not refused either.
    resource, path = _realize(an_ann_data(), tmp_path)
    _add_filtered_barcodes(path)

    ann_data = load_ann_data_from_resource(resource)

    assert list(ann_data.obs.columns) == ["filtered_barcodes"]
    assert ann_data.obs["filtered_barcodes"].dtype == bool
    assert list(ann_data.obs["filtered_barcodes"]) == [True, False, True]


def test_the_filtered_barcodes_flag_survives_a_matrix_free_read(
    tmp_path: pathlib.Path,
) -> None:
    # It is a per-cell annotation, not part of the count matrix, so the
    # read that stands in for the full one has to carry it -- otherwise
    # the statistics of such a resource would depend on which read built
    # them.
    resource, path = _realize(an_ann_data(), tmp_path)
    _add_filtered_barcodes(path)
    _delete_the_counts(path)

    ann_data = load_ann_data_from_resource(resource, matrix_free=True)

    assert list(ann_data.obs["filtered_barcodes"]) == [True, False, True]


def _make_legacy(path: pathlib.Path) -> None:
    """Rewrite the file as the CellRanger v2 per-genome layout."""
    with h5py.File(path, "r+") as h5:
        h5.move(_MATRIX_GROUP, "GRCh38")


def _make_probe_barcode(path: pathlib.Path) -> None:
    """Add the dataset that identifies a probe-barcode matrix."""
    with h5py.File(path, "r+") as h5:
        features = h5[f"{_MATRIX_GROUP}/{_FEATURES_GROUP}"]
        features.create_dataset(
            "gene_id", data=features["id"][()])


def _make_probe_barcode_off_position(path: pathlib.Path) -> None:
    """Put the probe marker where 10x does NOT, but a reader still sees it.

    The reference reader flattens the whole ``matrix`` group into one
    namespace before looking for ``gene_id``, so it selects the
    probe-barcode branch from here too.  A refusal that probed only the
    direct children of ``features`` would read this file instead.
    """
    with h5py.File(path, "r+") as h5:
        matrix = h5[_MATRIX_GROUP]
        matrix.create_dataset(
            "gene_id", data=matrix[f"{_FEATURES_GROUP}/id"][()])


def _drop_the_features(path: pathlib.Path) -> None:
    """Remove the feature table entirely."""
    with h5py.File(path, "r+") as h5:
        del h5[f"{_MATRIX_GROUP}/{_FEATURES_GROUP}"]


@pytest.mark.parametrize(
    ("damage", "expected"),
    [
        pytest.param(
            _make_legacy, "no 'matrix' group", id="legacy-per-genome-layout"),
        pytest.param(
            _make_probe_barcode, "probe-barcode", id="probe-barcode-matrix"),
        pytest.param(
            _make_probe_barcode_off_position, "probe-barcode",
            id="probe-barcode-marker-off-position"),
        pytest.param(
            _drop_the_features, "no 'features' group", id="no-feature-table"),
    ],
)
def test_refuses_a_layout_it_does_not_read(
    tmp_path: pathlib.Path,
    damage: Any,
    expected: str,
) -> None:
    # gain reads ONE of the shapes ``scanpy.read_10x_h5`` handles -- the
    # modern single-``matrix``-group feature-barcode file, which is what
    # both real resources are (ADR 0014).  The others are refused by
    # name rather than read as something they are not, because there is
    # no real file to build a fixture from and therefore no way to know
    # a guess was right.  The resource is named, because a bare h5py
    # KeyError says nothing about which resource in a repo sweep it was.
    resource, path = _realize(an_ann_data(), tmp_path)
    damage(path)

    with pytest.raises(ValueError, match=expected) as excinfo:
        load_ann_data_from_resource(resource)

    assert _RESOURCE_ID in str(excinfo.value)


def test_the_statistics_of_a_default_build_are_pinned(
    tmp_path: pathlib.Path,
) -> None:
    """The golden record, in the tier that runs on every commit.

    These bytes are what a statistics build writes for a multiome
    resource with no ``parameters:`` block, and they must never change
    without a deliberate rebuild of every deployed resource's statistics.
    They describe all five features: a default read returns the whole
    resource (ADR 0015), so the record last moved -- from the three
    features scanpy's ``gex_only`` default kept -- with the
    ``grr_manage -f`` reprocess that flip was shipped with.
    """
    resource, path = _realize(
        an_ann_data().with_var(_MULTIOME_FEATURES), tmp_path)

    impl = AnnDataResourceImplementation(resource)
    graph = TaskGraph()
    graph.add_tasks(impl.create_statistics_build_tasks())
    task_graph_run(graph, SequentialExecutor())

    assert resource.get_file_content(_DESCRIBE_ANN_DATA) == (
        # The MULTIPLICATION SIGN is anndata's, and this is a byte-exact
        # record of what it wrote -- an ASCII "x" here would assert
        # something no resource in any GRR contains.
        "AnnData object with n_obs × n_vars = 3 × 5\n"  # ruff: ignore[ambiguous-unicode-character-string]
        "    var: 'gene_ids', 'feature_types', 'genome', 'interval'\n"
        "    layers: None (.X)\n"
    )
    assert resource.get_file_content(_DESCRIBE_VAR) == (
        ",gene_ids,feature_types,genome,interval\n"
        "count,5,5,5,5\n"
        "unique,5,2,1,4\n"
        "top,ENSG001,Gene Expression,GRCh38,chr1:1-99\n"
        "freq,1,3,5,2\n"
    )
    # This resource carries no per-cell annotation, so describing ``obs``
    # yields an empty frame, which the implementation declines to write.
    #
    # Asserted against the DIRECTORY, not the manifest: a statistics
    # build does not refresh the manifest, so `_DESCRIBE_VAR not in
    # get_manifest()` would pass too -- for a file this very test has
    # just read.  Naming the whole expected set also makes an ADDED
    # statistic a failure, which is how a silently-read extra obs
    # column would show up here.
    assert sorted(
        entry.name for entry in (path.parent / "statistics").iterdir()
    ) == ["describe_ann_data.txt", "describe_var.csv"]


class TestANonUniqueVariableIndex:
    """gain does NOT de-duplicate a repeated gene symbol (#715).

    Every real ``10x_h5`` resource has one: a gene symbol carried by two
    Ensembl accessions is ordinary data, not a malformed resource.  The
    reader indexes the variables by symbol and leaves the repeats alone,
    which is what ``scanpy.read_10x_h5`` did -- it has no ``make_unique``
    parameter and never de-duplicated either.

    ``anndata`` says so once per read, from the constructor.  Both
    filtering helpers subset ``var``, and a subset re-checks the index;
    each silences the identical re-warn deliberately, so what a reader of
    the log gets is one line per resource rather than one per filter step.

    The behaviour is correct and this pins it -- nothing here asks for a
    change.  Today the warning could be silenced, or
    ``var_names_make_unique`` called, and the rest of the suite would stay
    green: ``describe_var.csv`` describes ``var``'s COLUMNS, and the
    repeated symbol is its INDEX, so no stored statistic of any resource
    would move.  :meth:`test_both_accessions_survive_under_one_symbol` is
    the assertion that fails instead -- NOT
    :meth:`test_the_statistics_of_such_a_resource_are_pinned`, whatever
    #715 says.
    """

    def test_both_accessions_survive_under_one_symbol(
        self, tmp_path: pathlib.Path,
    ) -> None:
        resource = (
            an_ann_data()
            .with_format("10x_h5")
            .with_var(_DUPLICATE_SYMBOLS)
            .build_resource(tmp_path)
        )

        # The notice this read emits is a behaviour of its own, with its
        # own tests below; silenced here so that this one asserts the
        # index and nothing else.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            ann_data = load_ann_data_from_resource(resource)

        # Both entries, under the same symbol, in the order the file
        # lists them -- not de-duplicated (which would rename the second
        # to ``TBCE-1``) and not grouped (which would move it up beside
        # the first).
        assert list(ann_data.var_names) == [
            "TBCE", "GAPDH", "TBCE", "chr1:1-99"]
        # And they stay tellable apart, because the accession that makes
        # them two genes is a column of its own.
        assert list(ann_data.var["gene_ids"]) == [
            "ENSG001", "ENSG002", "ENSG003", "PEAK001"]

    def test_the_read_says_the_index_is_not_unique(
        self, tmp_path: pathlib.Path,
    ) -> None:
        # Emitted, not swallowed: a resource whose variable index cannot
        # address its own genes unambiguously is worth one line in the
        # log of whatever run reads it, and the reader has no business
        # deciding otherwise on the resource's behalf.
        #
        # #715 describes this as a divergence -- scanpy suppressing the
        # notice and gain letting it through.  It is not one.  Measured
        # against scanpy 1.12.3 on this very fixture, ``read_10x_h5``
        # emits exactly one such warning on every path, default included:
        # it silences only its own ``copy()``, which is the same re-warn
        # the two helpers here silence.  What is pinned is therefore the
        # behaviour both readers have, and no change of gain's.
        resource = (
            an_ann_data()
            .with_format("10x_h5")
            .with_var(_DUPLICATE_SYMBOLS)
            .build_resource(tmp_path)
        )

        with pytest.warns(UserWarning, match=_NOT_UNIQUE):
            load_ann_data_from_resource(resource)

    @pytest.mark.parametrize(
        "parameters",
        [
            pytest.param({"gex_only": True}, id="gex-only"),
            pytest.param({"genome": "GRCh38"}, id="genome"),
        ],
    )
    def test_it_says_so_once_per_read_not_once_per_filter(
        self, tmp_path: pathlib.Path, parameters: dict[str, Any],
    ) -> None:
        # Only a read that FILTERS can tell the two apart: ``gex_only``
        # defaults to false for a 10x_h5 (ADR 0015), so a default read
        # subsets nothing and would emit exactly one warning whether or
        # not the suppression existed.
        #
        # One case per filter, because each silences the re-warn in its
        # own ``catch_warnings`` block and either could be dropped without
        # the other noticing.  Setting both together is left out: it fails
        # exactly when one of these two does, so it discriminates nothing.
        resource = (
            an_ann_data()
            .with_format("10x_h5")
            .with_var(_DUPLICATE_SYMBOLS)
            .with_parameters(parameters)
            .build_resource(tmp_path)
        )

        with pytest.warns(UserWarning, match=_NOT_UNIQUE) as caught:
            load_ann_data_from_resource(resource)

        # The COUNT, not the wording.  Which words anndata chooses is
        # upstream's to change and the drift tier's to notice; a
        # regression tier that pinned them would go red for a reason gain
        # is not answerable for.
        assert len([
            warning for warning in caught
            if _NOT_UNIQUE in str(warning.message)
        ]) == 1

    def test_the_statistics_of_such_a_resource_are_pinned(
        self, tmp_path: pathlib.Path,
    ) -> None:
        """The golden record for a resource whose var index repeats.

        What a statistics build writes for the shape every deployed
        ``10x_h5`` resource is in: a duplicate index neither breaks the
        build nor changes how ``var`` describes itself.  It is NOT the
        guard against de-duplication, for the reason the class docstring
        gives.
        """
        resource, path = _realize(
            an_ann_data().with_var(_DUPLICATE_SYMBOLS), tmp_path)

        impl = AnnDataResourceImplementation(resource)
        graph = TaskGraph()
        graph.add_tasks(impl.create_statistics_build_tasks())
        # The build reads the resource, so it emits the same notice; it
        # has its own tests, and leaking it from here would put a
        # UserWarning in the suite's summary for a test about bytes.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            task_graph_run(graph, SequentialExecutor())

        assert resource.get_file_content(_DESCRIBE_ANN_DATA) == (
            # The MULTIPLICATION SIGN is anndata's, as above.
            "AnnData object with n_obs × n_vars = 3 × 4\n"  # ruff: ignore[ambiguous-unicode-character-string]
            "    var: 'gene_ids', 'feature_types', 'genome', 'interval'\n"
            "    layers: None (.X)\n"
        )
        assert resource.get_file_content(_DESCRIBE_VAR) == (
            ",gene_ids,feature_types,genome,interval\n"
            "count,4,4,4,4\n"
            # ``gene_ids`` is 4 of 4 unique: the two TBCE rows carry
            # different accessions.
            "unique,4,2,1,3\n"
            "top,ENSG001,Gene Expression,GRCh38,chr1:1-99\n"
            "freq,1,3,4,2\n"
        )
        # Asserted against the DIRECTORY, not the manifest, for the
        # reason the default-build pin above gives.
        assert sorted(
            entry.name for entry in (path.parent / "statistics").iterdir()
        ) == ["describe_ann_data.txt", "describe_var.csv"]
