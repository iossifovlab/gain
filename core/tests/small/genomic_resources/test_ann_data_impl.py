# pylint: disable=W0621,C0114,C0116,W0212,W0613
import gzip
import json
import pathlib

import h5py
import pytest
from gain.genomic_resources.ann_data_resource import (
    load_ann_data_from_resource,
)
from gain.genomic_resources.implementations.ann_data_resource_impl import (
    AnnDataResourceImplementation,
)
from gain.genomic_resources.repository import GenomicResource
from gain.genomic_resources.testing.ann_data_builder import an_ann_data
from gain.genomic_resources.testing.builders import a_grr
from gain.task_graph.cli_tools import task_graph_run
from gain.task_graph.graph import TaskGraph
from gain.task_graph.sequential_executor import SequentialExecutor

DESCRIBE_OBS = "statistics/describe_obs.csv"
DESCRIBE_VAR = "statistics/describe_var.csv"
DESCRIBE_ANN_DATA = "statistics/describe_ann_data.txt"

STATISTICS = {DESCRIBE_OBS, DESCRIBE_VAR, DESCRIBE_ANN_DATA}

# anndata renders a shape with U+00D7 MULTIPLICATION SIGN rather than an
# ASCII ``x``, and the statistic is that rendering verbatim.  Spelled as an
# escape so the source carries no character a reader could mistake for ``x``.
TIMES = "\u00d7"


@pytest.fixture
def resource(tmp_path: pathlib.Path) -> GenomicResource:
    return an_ann_data().build_resource(tmp_path)


def build_statistics(resource: GenomicResource) -> None:
    """Run the implementation's real statistics tasks to completion."""
    impl = AnnDataResourceImplementation(resource)
    graph = TaskGraph()
    graph.add_tasks(impl.create_statistics_build_tasks())
    task_graph_run(graph, SequentialExecutor())


def refresh_manifest(resource: GenomicResource) -> None:
    """Rewrite the stored manifest, as grr_manage does after a stats run."""
    proto = resource.proto
    proto.save_manifest(resource, proto.build_manifest(resource))
    resource._manifest = None


def test_statistics_task_writes_the_describe_tables(
    resource: GenomicResource,
) -> None:
    assert not resource.file_exists(DESCRIBE_OBS)

    build_statistics(resource)

    assert resource.file_exists(DESCRIBE_OBS)
    assert resource.file_exists(DESCRIBE_VAR)
    assert resource.file_exists(DESCRIBE_ANN_DATA)
    assert "cell_type" in resource.get_file_content(DESCRIBE_OBS)
    assert "gene_name" in resource.get_file_content(DESCRIBE_VAR)


class TestTheStatisticsOfATenXResource:
    """Built without materialising the matrix, and identical either way.

    The statistics are 221 bytes describing the two axis tables and the
    shape.  Reading the matrix to produce them costs about 10 GB on the
    benchmark GRR's largest 10x resource, which is why ``resource-repair``
    cannot finish on it at all.
    """

    @staticmethod
    def _ten_x(tmp_path: pathlib.Path) -> GenomicResource:
        return (
            an_ann_data().with_format("10x_mtx").build_resource(tmp_path)
        )

    def test_they_describe_what_the_full_read_describes(
        self, tmp_path: pathlib.Path,
    ) -> None:
        # The equivalence stated against the read itself rather than
        # against a recorded expectation, so it cannot be satisfied by
        # updating a fixture.
        resource = self._ten_x(tmp_path)

        build_statistics(resource)

        assert resource.get_file_content(DESCRIBE_ANN_DATA) == \
            f"{load_ann_data_from_resource(resource)}\n"
        assert "gene_name" not in resource.get_file_content(DESCRIBE_VAR)
        assert "gene_ids" in resource.get_file_content(DESCRIBE_VAR)

    def test_they_are_built_without_reading_the_matrix(
        self, tmp_path: pathlib.Path,
    ) -> None:
        # A matrix whose size line is honest and whose body is absent: the
        # full read fails on it outright, so a build that succeeds is one
        # that never went past the header.
        resource = self._ten_x(tmp_path)
        matrix = next(tmp_path.rglob("matrix.mtx.gz"))
        header = gzip.decompress(matrix.read_bytes()).split(b"\n")[:3]
        matrix.write_bytes(gzip.compress(b"\n".join([*header, b""])))

        build_statistics(resource)

        # ``var`` is real -- it comes from the feature table, which is read
        # in full either way; it is only X that is a stand-in.  There is no
        # ``describe_obs.csv``: a 10x read's ``obs`` is barcodes and no
        # columns, which the implementation declines to describe.
        assert resource.get_file_content(DESCRIBE_ANN_DATA).startswith(
            f"AnnData object with n_obs {TIMES} n_vars = 3 {TIMES} 4")
        assert "ENSG001" in resource.get_file_content(DESCRIBE_VAR)
        assert not resource.file_exists(DESCRIBE_OBS)


def test_the_statistics_build_leaves_no_h5ad_handle_open(
    resource: GenomicResource,
) -> None:
    # An h5ad is read ``backed="r"``, which holds an open h5py file, and
    # the loader hands that handle to its caller on purpose.  This task is
    # the caller that runs once per resource in a repo sweep, so it is the
    # one that has to close -- relying on a garbage collection that may
    # never come is gain#480's shape.
    #
    # Counted through h5py rather than asserted on an AnnData, because the
    # build never hands one back: what matters is that no handle survives
    # it, not which object held it.
    before = h5py.h5f.get_obj_count(h5py.h5f.OBJ_ALL, h5py.h5f.OBJ_FILE)

    build_statistics(resource)

    assert h5py.h5f.get_obj_count(
        h5py.h5f.OBJ_ALL, h5py.h5f.OBJ_FILE) == before


def test_statistics_task_skips_an_annotation_table_with_no_columns(
    tmp_path: pathlib.Path,
) -> None:
    # ``describe`` of a frame with no columns is an empty frame, and writing
    # it would put a statistic in the manifest that cannot be read back.
    resource = (
        an_ann_data().without_obs_columns().build_resource(tmp_path)
    )

    build_statistics(resource)

    assert not resource.file_exists(DESCRIBE_OBS)
    assert resource.file_exists(DESCRIBE_VAR)


def test_ann_data_description_omits_the_backing_file_path(
    resource: GenomicResource, tmp_path: pathlib.Path,
) -> None:
    # ``AnnData._gen_repr`` appends ``backed at '<filename>'`` whenever the
    # read is backed, and the h5ad loader backs every read.  That is the
    # reader's own state, not the resource's: it makes the statistic record
    # where it happened to be built, so the same resource describes itself
    # differently on two machines -- and the file is published from the GRR.
    build_statistics(resource)

    description = resource.get_file_content(DESCRIBE_ANN_DATA)

    assert "backed at" not in description
    assert str(tmp_path) not in description


def test_ann_data_description_still_reports_the_shape_and_columns(
    resource: GenomicResource,
) -> None:
    # Removing the backing clause must take nothing else with it: the shape
    # and the axis-table columns are the whole content of this statistic.
    build_statistics(resource)

    description = resource.get_file_content(DESCRIBE_ANN_DATA)

    assert f"n_obs {TIMES} n_vars = 3 {TIMES} 4" in description
    assert "obs: 'cell_type', 'n_genes'" in description
    assert "var: 'gene_name', 'highly_variable'" in description


def test_statistics_task_id_names_the_resource(
    tmp_path: pathlib.Path,
) -> None:
    # The task id is the FileTaskCache flag-file key (and this task declares
    # neither input nor output files, so the flag is the ONLY path by which
    # it can be seen as cached).  Interpolating the resource OBJECT rendered
    # its default repr -- a memory address -- so every run minted a fresh
    # id, the flag from the previous run was never found and the statistics
    # were rebuilt unconditionally.
    resource = (
        a_grr()
        .with_resource("single_cell/atlas", an_ann_data())
        .build_repo(tmp_path)
        .get_resource("single_cell/atlas")
    )

    tasks = AnnDataResourceImplementation(
        resource).create_statistics_build_tasks()

    assert [task.task.task_id for task in tasks] == [
        "single_cell/atlas_ann_data_statistics"]


def test_statistics_task_id_separates_two_versions_of_one_resource(
    tmp_path: pathlib.Path,
) -> None:
    # Hence get_full_id() rather than the version-less resource_id: the two
    # versions are separate cache entries, and a shared id would also trip
    # TaskGraph's duplicate-id guard when a repo sweep walks both.
    repo = (
        a_grr()
        .with_resource("single_cell/atlas(1.0)", an_ann_data())
        .with_resource("single_cell/atlas(2.0)", an_ann_data())
        .build_repo(tmp_path)
    )

    older = AnnDataResourceImplementation(
        repo.get_resource("single_cell/atlas(1.0)"),
    ).create_statistics_build_tasks()[0]
    newer = AnnDataResourceImplementation(
        repo.get_resource("single_cell/atlas(2.0)"),
    ).create_statistics_build_tasks()[0]

    assert older.task.task_id == "single_cell/atlas(1.0)_ann_data_statistics"
    assert newer.task.task_id == "single_cell/atlas(2.0)_ann_data_statistics"


def test_files_lists_the_declared_file(resource: GenomicResource) -> None:
    assert AnnDataResourceImplementation(resource).files == {"data.h5ad"}


def test_files_lists_the_whole_ten_x_triple(
    tmp_path: pathlib.Path,
) -> None:
    # The config names only the matrix member, but all three files are
    # statistics inputs: editing the barcodes has to invalidate the build
    # the same way editing the matrix does.
    resource = (
        an_ann_data().with_format("10x_mtx").build_resource(tmp_path)
    )

    assert AnnDataResourceImplementation(resource).files == {
        "matrix.mtx.gz", "barcodes.tsv.gz", "features.tsv.gz"}


def test_files_lists_the_legacy_ten_x_triple(
    tmp_path: pathlib.Path,
) -> None:
    # The CellRanger v2 layout ships plain text and calls the feature table
    # ``genes.tsv``.  Naming ``features.tsv`` for it resolved to a file the
    # resource does not have, the manifest filter dropped it silently, and
    # editing the real gene table left the statistics looking fresh --
    # exactly the staleness the triple is enumerated to prevent.
    resource = (
        an_ann_data()
        .with_format("10x_mtx")
        .with_legacy_layout()
        .build_resource(tmp_path)
    )

    assert AnnDataResourceImplementation(resource).files == {
        "matrix.mtx", "barcodes.tsv", "genes.tsv"}


def test_files_lists_a_prefixed_ten_x_triple(
    tmp_path: pathlib.Path,
) -> None:
    resource = (
        an_ann_data()
        .with_format("10x_mtx")
        .with_prefix("sample1_")
        .build_resource(tmp_path)
    )

    assert AnnDataResourceImplementation(resource).files == {
        "sample1_matrix.mtx.gz", "sample1_barcodes.tsv.gz",
        "sample1_features.tsv.gz"}


def test_files_excludes_the_config_and_shipped_extras(
    tmp_path: pathlib.Path,
) -> None:
    resource = an_ann_data().build_resource(tmp_path)
    (tmp_path / "README.md").write_text("provenance notes\n")
    refresh_manifest(resource)
    assert {"README.md", "genomic_resource.yaml"} <= {
        entry.name for entry in resource.get_manifest()}

    assert AnnDataResourceImplementation(resource).files == {"data.h5ad"}


def test_files_lists_statistics_only_once_the_manifest_records_them(
    resource: GenomicResource,
) -> None:
    assert not STATISTICS & AnnDataResourceImplementation(resource).files

    build_statistics(resource)
    refresh_manifest(resource)

    assert AnnDataResourceImplementation(resource).files == {
        "data.h5ad", *STATISTICS}


def test_files_survives_a_config_without_a_file_key(
    tmp_path: pathlib.Path,
) -> None:
    resource = an_ann_data().without_file_key().build_resource(tmp_path)

    assert AnnDataResourceImplementation(resource).files == set()


def test_get_info_renders_the_describe_tables(
    resource: GenomicResource,
) -> None:
    build_statistics(resource)
    refresh_manifest(resource)

    info = AnnDataResourceImplementation(resource).get_info()

    assert "cell_type" in info
    assert "gene_name" in info
    assert "Statistics have not been built" not in info


def test_get_info_consults_the_manifest_not_the_filesystem(
    resource: GenomicResource,
) -> None:
    build_statistics(resource)

    assert "Statistics have not been built" in \
        AnnDataResourceImplementation(resource).get_info()

    refresh_manifest(resource)

    assert "Statistics have not been built" not in \
        AnnDataResourceImplementation(resource).get_info()


def test_get_info_degrades_when_statistics_were_never_built(
    resource: GenomicResource,
) -> None:
    assert "Statistics have not been built" in \
        AnnDataResourceImplementation(resource).get_info()


def test_get_statistics_info_degrades_too(
    resource: GenomicResource,
) -> None:
    assert AnnDataResourceImplementation(resource).get_statistics_info()


def test_get_info_escapes_the_ann_data_description(
    tmp_path: pathlib.Path,
) -> None:
    # The description is ``str(ann_data)``, which lists the obs and var
    # column names -- data the resource carries, not anything gain wrote.
    # The template escapes it explicitly; the environment autoescapes on
    # top of that, and neither double-escapes the other.
    # h5py rejects a forward slash in a key, so the column name carries an
    # opening tag only -- still enough to reach the browser as an element
    # rather than as text.  The tag name is a distinctive one because the
    # surrounding page legitimately contains script and style elements of
    # its own, and a bare `<script>` assertion would match those instead.
    resource = (
        an_ann_data()
        .with_obs("""
            cell     <gainxss>
            CELL_1   7
            CELL_2   9
        """)
        .build_resource(tmp_path)
    )
    build_statistics(resource)
    refresh_manifest(resource)

    info = AnnDataResourceImplementation(resource).get_info()

    assert "<gainxss>" not in info
    assert "&lt;gainxss&gt;" in info


def test_statistics_hash_changes_when_the_data_file_changes(
    tmp_path: pathlib.Path,
) -> None:
    first = an_ann_data().build_resource(tmp_path / "first")
    second = (
        an_ann_data()
        .with_obs("""
            cell     cell_type  n_genes
            CELL_9   astrocyte  999
        """)
        .build_resource(tmp_path / "second")
    )

    assert (
        AnnDataResourceImplementation(first).calc_statistics_hash()
        != AnnDataResourceImplementation(second).calc_statistics_hash()
    )


def test_statistics_hash_changes_when_a_ten_x_sidecar_changes(
    tmp_path: pathlib.Path,
) -> None:
    # The whole point of enumerating the triple: the config names only the
    # matrix, so a hash built from it alone cannot see a barcode edit.
    resource = (
        an_ann_data().with_format("10x_mtx").build_resource(tmp_path)
    )
    before = AnnDataResourceImplementation(resource).calc_statistics_hash()

    (tmp_path / "barcodes.tsv.gz").write_bytes(b"edited-not-really-gzip")
    refresh_manifest(resource)

    assert AnnDataResourceImplementation(
        resource).calc_statistics_hash() != before


def test_statistics_hash_changes_with_the_parameters_block(
    tmp_path: pathlib.Path,
) -> None:
    plain = an_ann_data().build_resource(tmp_path / "plain")
    parameterised = (
        an_ann_data()
        .with_parameters({"as_sparse": "X"})
        .build_resource(tmp_path / "parameterised")
    )

    assert (
        AnnDataResourceImplementation(plain).calc_statistics_hash()
        != AnnDataResourceImplementation(
            parameterised).calc_statistics_hash()
    )


def test_statistics_hash_is_stable_for_the_same_resource(
    tmp_path: pathlib.Path,
) -> None:
    first = an_ann_data().build_resource(tmp_path / "first")
    same = an_ann_data().build_resource(tmp_path / "same")

    assert (
        AnnDataResourceImplementation(first).calc_statistics_hash()
        == AnnDataResourceImplementation(same).calc_statistics_hash()
    )


def test_statistics_hash_records_the_data_file_md5(
    resource: GenomicResource,
) -> None:
    payload = json.loads(
        AnnDataResourceImplementation(resource).calc_statistics_hash())

    assert payload["files_md5"]["data.h5ad"] == \
        resource.get_manifest()["data.h5ad"].md5
    assert payload["config"]["format"] == "h5ad"


def test_statistics_hash_defaults_the_format_to_h5ad(
    tmp_path: pathlib.Path,
) -> None:
    # The default the loader actually applies, not data_frame's ``csv``.
    # This is the ``.h5ad`` suffix resolving to ``h5ad``; it cannot tell a
    # suffix-derived answer from a hardcoded one, because here they agree --
    # that is what let the drift ship.  The 10x case above discriminates.
    resource = an_ann_data().without_format_key().build_resource(tmp_path)

    payload = json.loads(
        AnnDataResourceImplementation(resource).calc_statistics_hash())

    assert payload["config"]["format"] == "h5ad"


def test_statistics_hash_records_the_suffix_derived_format(
    tmp_path: pathlib.Path,
) -> None:
    # A config that spells out no ``format:`` is read as whatever its suffix
    # implies, so that is what the hash has to record.  Recording the h5ad
    # fallback regardless makes an explicit ``format: h5ad`` a no-op on the
    # hash -- the read changes and the statistics never rebuild.
    resource = (
        an_ann_data()
        .with_format("10x_mtx")
        .without_format_key()
        .build_resource(tmp_path)
    )

    payload = json.loads(
        AnnDataResourceImplementation(resource).calc_statistics_hash())

    assert payload["config"]["format"] == "10x_mtx"


def test_statistics_hash_records_a_declared_format_over_the_suffix(
    tmp_path: pathlib.Path,
) -> None:
    # The declaration is what the loader reads the resource as, so it is what
    # the hash records -- deriving from the suffix regardless would put the
    # two back out of step, in the other direction.
    resource = (
        an_ann_data()
        .with_format("10x_mtx")
        .with_declared_format("h5ad")
        .build_resource(tmp_path)
    )

    payload = json.loads(
        AnnDataResourceImplementation(resource).calc_statistics_hash())

    assert payload["config"]["format"] == "h5ad"


def test_statistics_hash_survives_a_config_without_a_file_key(
    tmp_path: pathlib.Path,
) -> None:
    resource = an_ann_data().without_file_key().build_resource(tmp_path)

    payload = json.loads(
        AnnDataResourceImplementation(resource).calc_statistics_hash())

    assert payload["files_md5"] == {}


def test_statistics_hash_survives_a_file_missing_from_the_manifest(
    tmp_path: pathlib.Path,
) -> None:
    resource = an_ann_data().build_resource(tmp_path)
    (tmp_path / "data.h5ad").unlink()
    refresh_manifest(resource)

    payload = json.loads(
        AnnDataResourceImplementation(resource).calc_statistics_hash())

    assert payload["files_md5"] == {}


def test_statistics_hash_does_not_change_when_statistics_are_built(
    resource: GenomicResource,
) -> None:
    before = AnnDataResourceImplementation(resource).calc_statistics_hash()

    build_statistics(resource)
    refresh_manifest(resource)

    assert AnnDataResourceImplementation(
        resource).calc_statistics_hash() == before


def test_info_hash_is_the_package_placeholder(
    resource: GenomicResource,
) -> None:
    assert AnnDataResourceImplementation(resource).calc_info_hash() == \
        b"placeholder"


def test_the_implementation_is_reachable_through_the_plugin_registry(
    tmp_path: pathlib.Path,
) -> None:
    # pylint: disable=import-outside-toplevel
    from gain.genomic_resources import get_resource_implementation_builder

    grr = (
        a_grr()
        .with_resource("single_cell/atlas", an_ann_data())
        .build_repo(tmp_path)
    )
    resource = grr.get_resource("single_cell/atlas")

    builder = get_resource_implementation_builder(resource.get_type())

    assert builder is not None
    assert isinstance(builder(resource), AnnDataResourceImplementation)
