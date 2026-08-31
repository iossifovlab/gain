# pylint: disable=W0621,C0114,C0116,W0212,W0613
import pathlib  # noqa: I001
import textwrap
from typing import Any, cast

import numpy as np

import pytest
import pytest_mock

from gain.genomic_resources.cli import cli_manage
from gain.genomic_resources.genomic_scores import (
    FragmentScore,
)
from gain.genomic_resources.histogram import (
    HistogramConfig,
    NumberHistogram,
    build_histogram_config,
)
from gain.genomic_resources.implementations.genomic_scores_impl import (
    FragmentScoreImplementation,
    scan,
)
from gain.genomic_resources.repository import (
    GenomicResource,
    GenomicResourceRepo,
)
from gain.genomic_resources.repository_factory import (
    build_genomic_resource_repository,
    build_resource_implementation,
)
from gain.genomic_resources.statistics.min_max import MinMaxValue
from gain.genomic_resources.testing import (
    build_filesystem_test_repository,
    convert_to_tab_separated,
    setup_directories,
)
from gain.task_graph.cli_tools import task_graph_run
from gain.task_graph.sequential_executor import SequentialExecutor
from gain.task_graph.graph import TaskGraph


@pytest.fixture
def test_grr(tmp_path: pathlib.Path) -> GenomicResourceRepo:
    root_path = tmp_path
    setup_directories(
        root_path, {
            "grr.yaml": textwrap.dedent(f"""
                id: reannotation_repo
                type: dir
                directory: "{root_path}/grr"
            """),
            "grr": {
                "score_one": {
                    "genomic_resource.yaml": textwrap.dedent("""
                        type: fragment_score
                        table:
                            filename: data.txt
                        scores:
                            - id: freq
                              name: frequency
                              type: float
                              histogram:
                                type: number
                                number_of_bins: 3
                                view_range:
                                  min: 0
                                  max: 1
                                x_log_scale: false
                                y_log_scale: true
                              desc: some populaton frequency
                            - id: collection
                              name: collection
                              type: str
                              desc: SSC or AGRE
                            - id: status
                              name: affected_status
                              type: str
                              desc: |
                                shows if the child that has the de novo
                                is affected or unaffected
                    """),
                    "data.txt": convert_to_tab_separated(textwrap.dedent("""
            chrom  pos_begin  pos_end  frequency  collection affected_status
            1      10         20       0.02       SSC        affected
            1      50         100      0.1        SSC        affected
            2      1          8        0.00001    AGRE       unaffected
            2      16         20       0.3        SSC        affected
            2      200        203      0.0002     AGRE       unaffected
            15     16         20       0.2        AGRE       affected
                    """)),
                },
            },
        },
    )
    return build_genomic_resource_repository(file_name=str(
        root_path / "grr.yaml",
    ))


@pytest.fixture
def fragments(test_grr: GenomicResourceRepo) -> FragmentScore:
    return FragmentScore(test_grr.get_resource("score_one"))


@pytest.fixture
def fragments_resource(test_grr: GenomicResourceRepo) -> GenomicResource:
    return test_grr.get_resource("score_one")


@pytest.mark.parametrize("chrom,beg,end,count,attributes", [
    ("1", 5, 15, 1, [
        {"freq": 0.02, "collection": "SSC", "status": "affected"},
    ]),
    ("1", 60, 70, 1, [
        {"freq": 0.1, "collection": "SSC", "status": "affected"},
    ]),
    ("1", 10, 65, 2, [
        {"freq": 0.02, "collection": "SSC", "status": "affected"},
        {"freq": 0.1, "collection": "SSC", "status": "affected"},
    ]),
    ("2", 5, 15, 1, [
        {"freq": 0.00001, "collection": "AGRE", "status": "unaffected"},
    ]),
    ("2", 15, 25, 1, [
        {"freq": 0.3, "collection": "SSC", "status": "affected"},
    ]),
    ("2", 8, 25, 2, [
        {"freq": 0.00001, "collection": "AGRE", "status": "unaffected"},
        {"freq": 0.3, "collection": "SSC", "status": "affected"},
    ]),
])
def test_fragment_score_resource(
    fragments: FragmentScore,
    chrom: str,
    beg: int,
    end: int,
    count: int,
    attributes: list[dict[str, Any]],
) -> None:
    with fragments.open() as score:
        aaa = score.fetch_fragment_scores(
            chrom, beg, end)
        assert len(aaa) == count
        assert aaa == attributes


def test_fragment_score_wrong_resource_types(
    fragments_resource: GenomicResource,
    mocker: pytest_mock.MockFixture,
) -> None:
    mocker.patch.object(
        fragments_resource,
        "get_type",
        return_value="aaaa")

    with pytest.raises(
            ValueError,
            match="The resource provided to FragmentScore should be of "
            "'fragment_score' or 'cnv_collection' type, not a 'aaaa'"):
        FragmentScore(fragments_resource)


def test_fragment_score_no_open(fragments: FragmentScore) -> None:
    with pytest.raises(
        ValueError,
        match="The resource <score_one> is not open",
    ):
        fragments.fetch_fragment_scores("1", 5, 15)


def test_fragment_score_bad_chrom(fragments: FragmentScore) -> None:
    """A contig the resource does not have is refused, not answered ``[]``.

    ``[]`` would make "no fragments here" and "no such contig" the same
    answer, and the per-position reads refuse it too.
    """
    score = fragments.open()
    with pytest.raises(ValueError, match="not among the available"):
        score.fetch_fragment_scores("3", 5, 15)


@pytest.fixture
def fragments_impl(
    fragments_resource: GenomicResource,
) -> FragmentScoreImplementation:

    return FragmentScoreImplementation(fragments_resource)


def test_fragment_score_implementation(
    fragments_impl: FragmentScoreImplementation,
) -> None:
    assert fragments_impl is not None
    task_graph = TaskGraph()
    tasks = fragments_impl.create_statistics_build_tasks()
    assert len(tasks) == 4
    task_graph.add_tasks(tasks)

    executor = SequentialExecutor()
    task_graph_run(task_graph, executor)

    res_hash = fragments_impl.calc_info_hash()
    assert res_hash == b"infohash"

    res_hash = fragments_impl.calc_statistics_hash()
    assert b"affected_status" in res_hash

    info = fragments_impl.get_info()
    assert "some populaton frequency" in info

    info = fragments_impl.get_statistics_info()
    assert "Filename" in info


def test_fragment_score_histogram_scan(
    fragments_resource: GenomicResource,
) -> None:
    hist_conf = build_histogram_config({
        "histogram": {
            "type": "number",
            "view_range": {"min": 0, "max": 0.3},
            "number_of_bins": 2,
        },
    })
    assert isinstance(hist_conf, HistogramConfig)

    hist_confs = {"freq": hist_conf}

    histograms = scan.do_histogram(
        fragments_resource, hist_confs, "2", 0, 300,
    )

    assert isinstance(histograms["freq"], NumberHistogram)
    assert histograms["freq"].min_value == 1e-05
    assert histograms["freq"].max_value == 0.3
    bars = histograms["freq"].bars
    assert isinstance(bars, np.ndarray)
    assert cast(list[int], bars.tolist()) == [2, 1]

    assert cast(list[float], histograms["freq"].bins.tolist()) \
        == [0, 0.15, 0.3]


def test_fragment_score_min_max_scan(
    fragments_resource: GenomicResource,
) -> None:
    hist_conf = build_histogram_config({
        "histogram": {
            "type": "number",
            "view_range": {"min": 0, "max": 0.3},
            "number_of_bins": 2,
        },
    })
    assert isinstance(hist_conf, HistogramConfig)

    statistics = scan.do_min_max(
        fragments_resource, ["freq"], "2", 0, 300,
    )

    assert isinstance(statistics["freq"], MinMaxValue)
    assert statistics["freq"].min == 0.00001
    assert statistics["freq"].max == 0.3


def test_cli_manage_fragment_score_histograms(
    tmp_path: pathlib.Path,
    test_grr: GenomicResourceRepo,
) -> None:
    grr_path = tmp_path / "grr"
    assert not (grr_path / "/score_one/statistics").exists()

    cli_manage([
        "resource-repair",
        "-R", str(grr_path),
        "-r", "score_one",
        "-j", "1",
    ])

    assert (grr_path / "score_one/statistics").exists()

    assert (grr_path / "score_one/statistics/histogram_freq.json").exists()
    hist_file = (
        grr_path / "score_one/statistics/histogram_freq.json"
    ).read_text().replace(" ", "").replace("\n", "")
    assert hist_file.find('"bars":[6,0,0]') != -1


@pytest.fixture
def named_columns_grr(tmp_path: pathlib.Path) -> pathlib.Path:
    """A fragment score whose core columns are addressed by NAME.

    The addressing is the whole point of the fixture: ``get_column_key`` has
    an index to resolve only when the config names a column, so a table that
    leaves chrom/pos_begin/pos_end to their defaults never reaches the
    resolution these tests are about.  Naming them is the shape of a
    typical fragment-score resource (#502).
    """
    setup_directories(tmp_path, {
        "score_one": {
            "genomic_resource.yaml": textwrap.dedent("""
                type: fragment_score
                table:
                    filename: data.txt
                    chrom:
                        column_name: chromosome
                    pos_begin:
                        column_name: pos_beg
                    pos_end:
                        column_name: pos_end
                scores:
                    - id: cnv_type
                      column_name: cnv_type
                      type: str
                      desc: deletion or duplication
                      histogram:
                        type: categorical
            """),
            "data.txt": convert_to_tab_separated(textwrap.dedent("""
                chromosome  pos_beg  pos_end  cnv_type
                1           10       20       deletion
                1           50       100      duplication
                2           1        8        deletion
            """)),
        },
    })
    return tmp_path


def test_the_statistics_hash_survives_opening_the_score(
    named_columns_grr: pathlib.Path,
) -> None:
    """The statistics hash describes the resource, not this process's state.

    ``repo-repair`` computes it on both sides of the rebuild it is deciding:
    once up front, to ask whether the statistics are stale, and once in the
    worker that has just rebuilt them, to record what they were built from.
    An open score between the two makes those two answers differ forever,
    which is a resource that is rebuilt on every run (#502).
    """
    repo = build_filesystem_test_repository(named_columns_grr)
    impl = cast(
        FragmentScoreImplementation,
        build_resource_implementation(repo.get_resource("score_one")))

    before_open = impl.calc_statistics_hash()
    impl.score.open()

    assert impl.calc_statistics_hash() == before_open


def test_repo_repair_does_not_rebuild_the_statistics_it_just_built(
    named_columns_grr: pathlib.Path,
) -> None:
    """The symptom itself: a second repair of an untouched GRR is a no-op."""
    build_filesystem_test_repository(named_columns_grr)
    cli_manage(["repo-repair", "-R", str(named_columns_grr), "-j", "1"])

    # Nothing carries an opened score from the first run into the second:
    # every factory hands back a fresh score, so the second run reads the
    # resource exactly as a new process would.  The precise guard on #502 is
    # `test_the_statistics_hash_survives_opening_the_score`; this one checks
    # the end-to-end symptom the user would see.
    try:
        cli_manage([
            "repo-repair", "--dry-run",
            "-R", str(named_columns_grr), "-j", "1",
        ])
    except SystemExit as exit_call:
        pytest.fail(
            f"nothing changed between the two runs, yet the second reports "
            f"{exit_call.code} resource(s) needing an update")
