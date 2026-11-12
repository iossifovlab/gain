# pylint: disable=W0621,C0114,C0116,W0212,W0613
import json
import pathlib
import textwrap

import pytest
from gain.gene_sets.implementations.gene_sets_impl import GeneSetCollectionImpl
from gain.genomic_resources.cli import cli_manage
from gain.genomic_resources.histogram import (
    CategoricalHistogram,
    NumberHistogram,
)
from gain.genomic_resources.repository import (
    GenomicResourceRepo,
    ReadWriteRepositoryProtocol,
)
from gain.genomic_resources.repository_ids import GR_CONF_FILE_NAME
from gain.genomic_resources.testing import (
    build_filesystem_test_repository,
    setup_directories,
)


@pytest.fixture
def gene_sets_repo(
    grr_contents: dict,
    tmp_path: pathlib.Path,
) -> GenomicResourceRepo:
    root_path = tmp_path
    setup_directories(root_path, grr_contents)
    return build_filesystem_test_repository(root_path)


@pytest.fixture
def gene_sets_impl(
    gene_sets_repo: GenomicResourceRepo,
) -> GeneSetCollectionImpl:
    res = gene_sets_repo.get_resource("test")
    assert res is not None
    return GeneSetCollectionImpl(res)


def test_add_statistics_build_tasks(
    gene_sets_repo: GenomicResourceRepo,
) -> None:

    res = gene_sets_repo.get_resource("test")
    assert res is not None

    gene_sets_collection_impl = GeneSetCollectionImpl(res)
    assert gene_sets_collection_impl is not None

    tasks = gene_sets_collection_impl.create_statistics_build_tasks()
    assert len(tasks) == 1


def test_compute_and_save_statistics(
    gene_sets_impl: GeneSetCollectionImpl,
    tmp_path: pathlib.Path,
) -> None:

    statistics_path = tmp_path / "test" / "statistics"
    assert not statistics_path.exists()

    gene_sets_impl._compute_and_save_all_statistics()

    assert (statistics_path / "gene_sets_per_gene_histogram.json").exists()
    assert (statistics_path / "genes_per_gene_set_histogram.json").exists()
    assert (statistics_path / "gene_collection_count_statistics.json").exists()
    assert (statistics_path / "gene_sets_list_statistics.json").exists()

    result = json.loads((
        statistics_path / "gene_collection_count_statistics.json").read_text())
    assert result == {
        "number_of_gene_sets": 3,
        "number_of_unique_genes": 2,
    }

    result = json.loads((
        statistics_path / "genes_per_gene_set_histogram.json").read_text())
    assert result["values"] == {"1": 2, "2": 1}

    result = json.loads((
        statistics_path / "gene_sets_per_gene_histogram.json").read_text())
    assert result["values"] == {"2": 2}

    result = json.loads((
        statistics_path / "gene_sets_list_statistics.json").read_text())
    assert result == [
        {"name": "test:02", "count": 2, "desc": "test_second"},
        {"name": "test:01", "count": 1, "desc": "test_first"},
        {"name": "test:03", "count": 1, "desc": "test_third"},
    ]


def test_resource_info(
    tmp_path: pathlib.Path,
    gene_sets_repo: GenomicResourceRepo,
) -> None:
    assert not (tmp_path / "test/index.html").exists()
    assert not (tmp_path / "index.html").exists()

    cli_manage([
        "resource-info", "-R", str(tmp_path), "-r", "test", "-j", "1",
    ])

    assert (tmp_path / "test/index.html").exists()
    assert (
        tmp_path / "test/statistics/gene_sets_list_statistics.json").exists()


def test_repo_info(
    tmp_path: pathlib.Path,
    gene_sets_repo: GenomicResourceRepo,
) -> None:
    assert not (tmp_path / "test/index.html").exists()
    assert not (tmp_path / "index.html").exists()

    cli_manage([
        "repo-info", "-R", str(tmp_path), "-j", "1",
    ])

    assert (tmp_path / "index.html").exists()
    assert (tmp_path / "test/index.html").exists()
    assert (
        tmp_path / "test/statistics/gene_sets_list_statistics.json").exists()

    assert (tmp_path / "main/index.html").exists()
    assert (
        tmp_path / "main/statistics/gene_sets_list_statistics.json").exists()


def test_gene_set_collection_statistics_hash(
    gene_sets_impl: GeneSetCollectionImpl,
) -> None:
    result = json.loads(gene_sets_impl.calc_statistics_hash().decode())
    assert "files_md5" in result
    assert "genes_per_gene_set" in result
    assert "gene_sets_per_gene" in result


def test_statistics_hash_skips_a_file_the_manifest_does_not_carry(
    tmp_path: pathlib.Path,
) -> None:
    """The hash is built from a manifest that can lag the files on disk.

    A map collection reports its companion names file whenever the file
    is there -- the question ``load_gene_sets`` asks before reading it.
    The manifest is a separate record, and a resource whose manifest was
    written before the names file arrived carries no entry for it, so
    hashing every reported file by manifest lookup would fail on a
    resource that loads perfectly well.
    """
    setup_directories(tmp_path, {
        "stale": {
            GR_CONF_FILE_NAME: textwrap.dedent("""
                type: gene_set_collection
                id: stale
                format: map
                filename: test-map.txt
                web_label: Stale
                web_format_str: "key| (|count|)"
            """),
            "test-map.txt": "#geneNS\tsym\nPOGZ\ttest:01\n",
            "test-mapnames.txt": "test:01\ttest_first\n",
        },
    })
    repo = build_filesystem_test_repository(tmp_path)
    resource = repo.get_resource("stale")
    assert resource is not None

    # Roll the manifest back to what it would have held had it been
    # written before the names file arrived.  It has to happen after the
    # repository is built, which manifests every resource it finds.
    manifest = resource.get_manifest()
    del manifest.entries["test-mapnames.txt"]
    proto = resource.proto
    assert isinstance(proto, ReadWriteRepositoryProtocol)
    proto.save_manifest(resource, manifest)
    assert "test-mapnames.txt" not in resource.get_manifest()

    impl = GeneSetCollectionImpl(resource)

    result = json.loads(impl.calc_statistics_hash().decode())

    assert sorted(result["files_md5"]) == ["test-map.txt"]


def _build_impl_with_histograms(
    tmp_path: pathlib.Path,
    histograms_yaml: str,
) -> GeneSetCollectionImpl:
    setup_directories(tmp_path, {
        "numbers": {
            GR_CONF_FILE_NAME: textwrap.dedent("""
                type: gene_set_collection
                id: numbers
                format: map
                filename: test-map.txt
            """) + textwrap.dedent(histograms_yaml),
            "test-map.txt": (
                "#geneNS\tsym\n"
                "POGZ\ttest:01 test:02\n"
                "CHD8\ttest:02 test:03\n"
            ),
        },
    })
    repo = build_filesystem_test_repository(tmp_path)
    resource = repo.get_resource("numbers")
    assert resource is not None
    impl = GeneSetCollectionImpl(resource)
    impl.gene_set_collection.load()
    return impl


def test_genes_per_gene_set_number_hist_computes_the_view_range(
    tmp_path: pathlib.Path,
) -> None:
    impl = _build_impl_with_histograms(tmp_path, """
        histograms:
          genes_per_gene_set:
            type: number
            number_of_bins: 2
    """)

    hist = impl._calc_genes_per_gene_set_hist()

    assert isinstance(hist, NumberHistogram)
    assert hist.view_range == (1, 2)
    assert hist.bars.tolist() == [2, 1]


def test_genes_per_gene_set_number_hist_uses_the_configured_view_range(
    tmp_path: pathlib.Path,
) -> None:
    impl = _build_impl_with_histograms(tmp_path, """
        histograms:
          genes_per_gene_set:
            type: number
            number_of_bins: 4
            view_range:
              min: 0
              max: 4
    """)

    hist = impl._calc_genes_per_gene_set_hist()

    assert isinstance(hist, NumberHistogram)
    assert hist.view_range == (0, 4)
    assert hist.bars.tolist() == [0, 2, 1, 0]


def test_gene_sets_per_gene_number_hist_computes_the_view_range(
    tmp_path: pathlib.Path,
) -> None:
    impl = _build_impl_with_histograms(tmp_path, """
        histograms:
          gene_sets_per_gene:
            type: number
            number_of_bins: 2
    """)

    hist = impl._calc_gene_sets_per_gene_hist()

    assert isinstance(hist, NumberHistogram)
    assert hist.view_range == (0, 2)
    assert hist.bars.tolist() == [0, 2]


def test_gene_sets_per_gene_number_hist_uses_the_configured_view_range(
    tmp_path: pathlib.Path,
) -> None:
    impl = _build_impl_with_histograms(tmp_path, """
        histograms:
          gene_sets_per_gene:
            type: number
            number_of_bins: 4
            view_range:
              min: 0
              max: 4
    """)

    hist = impl._calc_gene_sets_per_gene_hist()

    assert isinstance(hist, NumberHistogram)
    assert hist.view_range == (0, 4)
    assert hist.bars.tolist() == [0, 0, 2, 0]


def test_histograms_without_config_are_categorical(
    tmp_path: pathlib.Path,
) -> None:
    impl = _build_impl_with_histograms(tmp_path, "")

    genes_per_gene_set = impl._calc_genes_per_gene_set_hist()
    gene_sets_per_gene = impl._calc_gene_sets_per_gene_hist()

    assert isinstance(genes_per_gene_set, CategoricalHistogram)
    assert isinstance(gene_sets_per_gene, CategoricalHistogram)
    assert genes_per_gene_set.raw_values == {1: 2, 2: 1}
    assert gene_sets_per_gene.raw_values == {2: 2}
