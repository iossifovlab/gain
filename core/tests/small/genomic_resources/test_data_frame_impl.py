# pylint: disable=W0621,C0114,C0116,W0212,W0613
import json
import pathlib

import pytest
from gain.genomic_resources.implementations.data_frame_resource_impl import (
    DataFrameResourceImplementation,
)
from gain.genomic_resources.repository import GenomicResource
from gain.genomic_resources.testing import build_filesystem_test_repository
from gain.genomic_resources.testing.builders import a_grr
from gain.genomic_resources.testing.data_frame_builder import a_data_frame
from gain.task_graph.cache import FileTaskCache
from gain.task_graph.cli_tools import task_graph_run
from gain.task_graph.graph import TaskGraph
from gain.task_graph.sequential_executor import SequentialExecutor

DESCRIBE = "statistics/describe.csv"

DATA = """
    gene  score  count  label
    G1    0.25   10     alpha
    G2    0.50   20     beta
    G3    0.75   30     gamma
"""


@pytest.fixture
def resource(tmp_path: pathlib.Path) -> GenomicResource:
    return (
        a_data_frame()
        .with_data(DATA)
        .build_resource(tmp_path)
    )


def build_statistics(resource: GenomicResource) -> None:
    """Run the implementation's real statistics tasks to completion."""
    impl = DataFrameResourceImplementation(resource)
    graph = TaskGraph()
    graph.add_tasks(impl.create_statistics_build_tasks())
    task_graph_run(graph, SequentialExecutor())


def build_statistics_through(
    resource: GenomicResource, cache_dir: pathlib.Path,
) -> None:
    """Run the statistics tasks through a persistent task cache."""
    impl = DataFrameResourceImplementation(resource)
    graph = TaskGraph()
    graph.add_tasks(impl.create_statistics_build_tasks())
    task_graph_run(
        graph, SequentialExecutor(FileTaskCache(cache_dir=str(cache_dir))))


def refresh_manifest(resource: GenomicResource) -> None:
    """Rewrite the stored manifest, as grr_manage does after a stats run."""
    proto = resource.proto
    proto.save_manifest(resource, proto.build_manifest(resource))
    resource._manifest = None


def test_statistics_task_writes_a_describe_table(
    resource: GenomicResource,
) -> None:
    assert not resource.file_exists(DESCRIBE)

    build_statistics(resource)

    assert resource.file_exists(DESCRIBE)
    describe = resource.get_file_content(DESCRIBE)
    assert "gene" in describe
    assert "score" in describe


def test_statistics_task_id_names_the_resource(
    tmp_path: pathlib.Path,
) -> None:
    # The task id is the FileTaskCache flag-file key (and neither statistics
    # task declares input or output files, so the flag is the ONLY path by
    # which they can be seen as cached).  Interpolating the resource OBJECT
    # rendered its default repr -- a memory address -- so every run minted a
    # fresh id, the flag from the previous run was never found and the
    # statistics were rebuilt unconditionally.
    resource = (
        a_grr()
        .with_resource("tables/genes", a_data_frame().with_data(DATA))
        .build_repo(tmp_path)
        .get_resource("tables/genes")
    )

    tasks = DataFrameResourceImplementation(
        resource).create_statistics_build_tasks()

    assert [task.task.task_id for task in tasks] == [
        "tables/genes_data_frame_statistics"]


def test_statistics_task_id_separates_two_versions_of_one_resource(
    tmp_path: pathlib.Path,
) -> None:
    # Hence get_full_id() rather than the version-less resource_id: the two
    # versions are separate cache entries, and a shared id would also trip
    # TaskGraph's duplicate-id guard when a repo sweep walks both.
    repo = (
        a_grr()
        .with_resource("tables/genes(1.0)", a_data_frame().with_data(DATA))
        .with_resource("tables/genes(2.0)", a_data_frame().with_data(DATA))
        .build_repo(tmp_path)
    )

    older = DataFrameResourceImplementation(
        repo.get_resource("tables/genes(1.0)"),
    ).create_statistics_build_tasks()[0]
    newer = DataFrameResourceImplementation(
        repo.get_resource("tables/genes(2.0)"),
    ).create_statistics_build_tasks()[0]

    assert older.task.task_id == "tables/genes(1.0)_data_frame_statistics"
    assert newer.task.task_id == "tables/genes(2.0)_data_frame_statistics"


def test_a_second_run_over_an_unchanged_resource_reuses_the_cache(
    tmp_path: pathlib.Path,
) -> None:
    # What the unstable id cost in practice: grr_manage rebuilt describe.csv
    # on every run, because the flag file it wrote was keyed on an id that
    # no later run could ever ask for again.  The second run therefore reads
    # the repository afresh, as a second grr_manage process would -- an id
    # derived from the resource OBJECT is stable only within one process.
    grr_dir = tmp_path / "grr"
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    first_run = (
        a_grr()
        .with_resource("tables/genes", a_data_frame().with_data(DATA))
        .build_repo(grr_dir)
        .get_resource("tables/genes")
    )
    build_statistics_through(first_run, cache_dir)
    # Removing the output is what makes the second run speak: only a task
    # that actually executes can put describe.csv back.
    (grr_dir / "tables" / "genes" / DESCRIBE).unlink()

    second_run = build_filesystem_test_repository(
        grr_dir).get_resource("tables/genes")
    build_statistics_through(second_run, cache_dir)

    assert not second_run.file_exists(DESCRIBE)


def test_files_lists_the_declared_table(resource: GenomicResource) -> None:
    impl = DataFrameResourceImplementation(resource)

    assert impl.files == {"data.csv"}


def test_files_excludes_the_config_and_shipped_extras(
    tmp_path: pathlib.Path,
) -> None:
    # ``files`` drives the cache prefetch worklist, so it names what the
    # implementation reads -- not everything the resource happens to ship.
    # The extras have to be IN the manifest for the exclusion to mean
    # anything, and the manifest is a stored artifact written at build
    # time, so they go in before it is rebuilt.
    resource = (
        a_data_frame().with_data(DATA).build_resource(tmp_path)
    )
    (tmp_path / "README.md").write_text("provenance notes\n")
    (tmp_path / "extra.csv").write_text("gene,note\nG1,stray\n")
    refresh_manifest(resource)
    assert {"README.md", "extra.csv", "genomic_resource.yaml"} <= {
        entry.name for entry in resource.get_manifest()}

    assert DataFrameResourceImplementation(resource).files == {"data.csv"}


def test_files_lists_describe_only_once_the_manifest_records_it(
    resource: GenomicResource,
) -> None:
    # Naming a file the resource does not have makes the cache run report a
    # classify failure for it (gain#43), so describe.csv is listed only once
    # the manifest records it.  The manifest is a STORED artifact, not a
    # live scan: grr_manage rewrites it after the statistics step, and a
    # cache can only fetch what the published manifest lists.
    assert DESCRIBE not in DataFrameResourceImplementation(resource).files

    build_statistics(resource)
    refresh_manifest(resource)

    assert DataFrameResourceImplementation(resource).files == {
        "data.csv", DESCRIBE}


def test_files_survives_a_config_without_a_file_key(
    tmp_path: pathlib.Path,
) -> None:
    resource = (
        a_data_frame()
        .without_file_key()
        .with_data(DATA)
        .build_resource(tmp_path)
    )

    assert DataFrameResourceImplementation(resource).files == set()


def test_get_info_renders_the_describe_table(
    resource: GenomicResource,
) -> None:
    build_statistics(resource)
    refresh_manifest(resource)

    info = DataFrameResourceImplementation(resource).get_info()

    assert "score" in info
    assert "Statistics have not been built" not in info


def test_get_info_consults_the_manifest_not_the_filesystem(
    resource: GenomicResource,
) -> None:
    # Asking the protocol whether describe.csv exists is a network round
    # trip per info render on an http or s3 GRR, and the manifest is
    # already in memory.  Same rule as ``files``, then: a file the
    # published manifest does not list is not part of the resource, even
    # if it happens to be sitting next to it.
    build_statistics(resource)

    assert "Statistics have not been built" in \
        DataFrameResourceImplementation(resource).get_info()

    refresh_manifest(resource)

    assert "Statistics have not been built" not in \
        DataFrameResourceImplementation(resource).get_info()


def test_get_info_degrades_when_statistics_were_never_built(
    resource: GenomicResource,
) -> None:
    # A read-only remote GRR cannot build statistics, so grr_browse must
    # still render the resource rather than fail on it.
    info = DataFrameResourceImplementation(resource).get_info()

    assert "Statistics have not been built" in info


def test_get_statistics_info_degrades_too(
    resource: GenomicResource,
) -> None:
    assert DataFrameResourceImplementation(resource).get_statistics_info()


def test_statistics_hash_changes_when_the_data_file_changes(
    tmp_path: pathlib.Path,
) -> None:
    # Hashing the config alone left the table invisible: an edited data file
    # produced an unchanged hash, so grr_manage never rebuilt describe.csv.
    base = a_data_frame().with_data(DATA)
    first = base.build_resource(tmp_path / "first")
    second = (
        base
        .with_data("""
            gene  score  count  label
            G1    0.99   99     omega
        """)
        .build_resource(tmp_path / "second")
    )

    assert (
        DataFrameResourceImplementation(first).calc_statistics_hash()
        != DataFrameResourceImplementation(second).calc_statistics_hash()
    )


def test_statistics_hash_changes_with_the_parameters_block(
    tmp_path: pathlib.Path,
) -> None:
    base = a_data_frame().with_data(DATA)
    plain = base.build_resource(tmp_path / "plain")
    parameterised = (
        base.with_parameters({"skiprows": 1}).build_resource(
            tmp_path / "parameterised")
    )

    assert (
        DataFrameResourceImplementation(plain).calc_statistics_hash()
        != DataFrameResourceImplementation(
            parameterised).calc_statistics_hash()
    )


def test_statistics_hash_is_stable_for_the_same_resource(
    tmp_path: pathlib.Path,
) -> None:
    base = a_data_frame().with_data(DATA)
    first = base.build_resource(tmp_path / "first")
    same = base.build_resource(tmp_path / "same")

    assert (
        DataFrameResourceImplementation(first).calc_statistics_hash()
        == DataFrameResourceImplementation(same).calc_statistics_hash()
    )


def test_statistics_hash_records_the_data_file_md5(
    resource: GenomicResource,
) -> None:
    payload = json.loads(
        DataFrameResourceImplementation(resource).calc_statistics_hash())

    assert payload["files_md5"]["data.csv"] == \
        resource.get_manifest()["data.csv"].md5
    assert payload["config"]["format"] == "csv"


def test_statistics_hash_survives_a_config_without_a_file_key(
    tmp_path: pathlib.Path,
) -> None:
    # ``files`` already degrades to an empty set for this resource, and
    # grr_manage hashes every resource it walks before it opens any of
    # them.  A bare KeyError here would abort that walk on a
    # misconfiguration the loader is the right place to report.
    resource = (
        a_data_frame()
        .without_file_key()
        .with_data(DATA)
        .build_resource(tmp_path)
    )

    payload = json.loads(
        DataFrameResourceImplementation(resource).calc_statistics_hash())

    assert payload["files_md5"] == {}


def test_statistics_hash_survives_a_file_missing_from_the_manifest(
    tmp_path: pathlib.Path,
) -> None:
    # The other half of the same degradation: the config names a table the
    # manifest does not list.
    resource = (
        a_data_frame().with_data(DATA).build_resource(tmp_path)
    )
    (tmp_path / "data.csv").unlink()
    refresh_manifest(resource)

    payload = json.loads(
        DataFrameResourceImplementation(resource).calc_statistics_hash())

    assert payload["files_md5"] == {}


def test_statistics_hash_does_not_change_when_statistics_are_built(
    resource: GenomicResource,
) -> None:
    # The hash answers "do the statistics need rebuilding?", so only the
    # INPUTS may reach it.  describe.csv is the build's own output and
    # joins ``files`` the moment the manifest records it -- hashing it in
    # would make every freshly built resource look stale on the next run.
    before = DataFrameResourceImplementation(resource).calc_statistics_hash()

    build_statistics(resource)
    refresh_manifest(resource)

    assert DataFrameResourceImplementation(
        resource).calc_statistics_hash() == before


def test_info_hash_is_the_package_placeholder(
    resource: GenomicResource,
) -> None:
    # Every implementation in the package returns this; pinned so that a
    # future change here is a deliberate one.
    assert DataFrameResourceImplementation(resource).calc_info_hash() == \
        b"placeholder"


def test_the_implementation_is_reachable_through_the_plugin_registry(
    tmp_path: pathlib.Path,
) -> None:
    # pylint: disable=import-outside-toplevel
    from gain.genomic_resources import get_resource_implementation_builder

    grr = (
        a_grr()
        .with_resource("tables/genes", a_data_frame().with_data(DATA))
        .build_repo(tmp_path)
    )
    resource = grr.get_resource("tables/genes")

    builder = get_resource_implementation_builder(resource.get_type())

    assert builder is not None
    assert isinstance(builder(resource), DataFrameResourceImplementation)
