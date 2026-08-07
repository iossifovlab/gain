# pylint: disable=W0621,C0114,C0116,W0212,W0613
import json
import os
import pathlib
import shutil
import textwrap
from typing import Any

import pytest
import pytest_mock
from gain.genomic_resources import register_implementation
from gain.genomic_resources.cli import cli_manage
from gain.genomic_resources.genomic_scores import build_score_from_resource
from gain.genomic_resources.histogram import (
    CategoricalHistogram,
    HistogramError,
    NumberHistogram,
)
from gain.genomic_resources.repository import (
    GR_CONF_FILE_NAME,
    GR_SQLITE_META_FILE_NAME,
    GenomicResource,
)
from gain.genomic_resources.repository_factory import (
    build_resource_implementation,
)
from gain.genomic_resources.resource_implementation import (
    GenomicResourceImplementation,
    ResourceStatistics,
)
from gain.genomic_resources.testing import (
    build_filesystem_test_repository,
    setup_directories,
    setup_tabix,
)
from gain.genomic_resources.testing.builders import (
    PositionScoreBuilder,
    a_grr,
    a_position_score,
)
from gain.task_graph.graph import TaskDesc, TaskGraph


class SomeTestImplementation(GenomicResourceImplementation):
    """Simple implementation used for testing."""

    STATISTICS_FOLDER = "statistics"

    def calc_statistics_hash(self) -> bytes:
        """
        Compute the statistics hash.

        This hash is used to decide whether the resource statistics should be
        recomputed.
        """
        return b"somehash"

    def create_statistics_build_tasks(
        self, **kwargs: Any,
    ) -> list[TaskDesc]:
        """Add tasks for calculating resource statistics to a task graph."""
        task = TaskGraph.make_task(
            "test_resource_sample_statistic",
            self._do_sample_statistic,
            args=[],
            deps=[],
        )
        return [task]

    def _do_sample_statistic(self) -> bool:
        proto = self.resource.proto
        with proto.open_raw_file(
            self.resource, f"{self.STATISTICS_FOLDER}/somestat", mode="wt",
        ) as outfile:
            outfile.write("test")
        return True

    def get_statistics(self) -> ResourceStatistics:
        return MockStatistics.build_statistics(self)

    def calc_info_hash(self) -> bytes:
        """Compute and return the info hash."""
        return b"infohash"

    def get_info(self, **kwargs: Any) -> str:
        """Construct the contents of the implementation's HTML info page."""
        return textwrap.dedent(
            """
            <h1>Test page</h1>
            """,
        )

    def get_statistics_info(self, **kwargs: Any) -> str:
        """Construct the contents of the implementation's statistics
        HTML info page."""
        return textwrap.dedent(
            """
            <h1>Test page</h1>
            """,
        )


class MockStatistics(ResourceStatistics):
    @staticmethod
    def build_statistics(
        genomic_resource: GenomicResourceImplementation,
    ) -> ResourceStatistics:
        return MockStatistics(genomic_resource.resource_id)


def build_test_implementation(
    resource: GenomicResource,
) -> SomeTestImplementation:
    return SomeTestImplementation(resource)


@pytest.fixture(scope="module")
def register_test_implementation() -> None:
    register_implementation("test_resource", build_test_implementation)


def test_cli_stats(
    tmp_path: pathlib.Path, register_test_implementation: None,
) -> None:
    setup_directories(tmp_path, {
        "one": {
            GR_CONF_FILE_NAME: """
                type: test_resource
                some_random_value: test
                """,
        },
    })

    repo = build_filesystem_test_repository(tmp_path)

    assert repo is not None

    cli_manage(["repo-stats", "-R", str(tmp_path), "-j", "1"])

    statistic_path = os.path.join(tmp_path, "one", "statistics", "somestat")
    assert os.path.exists(statistic_path)
    assert pathlib.Path(statistic_path).read_text() == "test"

    statistic_hash_path = os.path.join(
        tmp_path, "one", "statistics", "stats_hash",
    )
    assert os.path.exists(statistic_hash_path)
    assert pathlib.Path(statistic_hash_path).read_text() == "somehash"


def test_stats_allele_score(tmp_path: pathlib.Path) -> None:
    setup_directories(tmp_path, {
        "one": {
            GR_CONF_FILE_NAME: """
                type: allele_score
                table:
                    filename: data.txt.gz
                    format: tabix
                scores:
                    - id: freq
                      type: float
                      desc: ""
                      name: freq
                      histogram:
                        type: number
                        number_of_bins: 100
                        view_range:
                          min: 0.0
                          max: 1.0
                        y_log_scale: true
                """,
        },
    })
    setup_tabix(
        tmp_path / "one" / "data.txt.gz",
        """
        #chrom pos_begin  reference  alternative  freq
        1      10         A          G            0.02
        1      10         A          C            0.03
        1      10         A          A            0.04
        1      16         CA         G            0.03
        1      16         C          T            0.04
        1      16         C          A            0.05
        2      16         CA         G            0.03
        2      16         C          T            EMPTY
        2      16         C          A            0.05
        """, seq_col=0, start_col=1, end_col=1)

    repo = build_filesystem_test_repository(tmp_path)

    assert repo is not None

    cli_manage(["repo-stats", "-R", str(tmp_path), "-j", "1"])
    minmax_statistic_path = os.path.join(
        tmp_path, "one", "statistics", "min_max_freq.yaml",
    )
    histogram_statistic_path = os.path.join(
        tmp_path, "one", "statistics", "histogram_freq.json",
    )
    histogram_image_path = os.path.join(
        tmp_path, "one", "statistics", "histogram_freq.json",
    )
    assert not os.path.exists(minmax_statistic_path)
    assert os.path.exists(histogram_statistic_path)
    assert os.path.exists(histogram_image_path)

    freq_hist = NumberHistogram.deserialize(
        pathlib.Path(histogram_statistic_path).read_text())

    assert len(freq_hist.bars) == 100
    assert freq_hist.bars[0] == 0
    assert freq_hist.bars[2] == 1  # region [10]
    assert freq_hist.bars[3] == 3  # region [10, 16, 16]
    assert freq_hist.bars[4] == 2  # region [10, 16]
    assert freq_hist.bars[5] == 2  # region [16, 16]
    assert freq_hist.bars.sum() == (1 + 3 + 2 + 2)


def test_stats_position_score(tmp_path: pathlib.Path) -> None:
    setup_directories(tmp_path, {
        "one": {
            GR_CONF_FILE_NAME: """
                type: position_score
                table:
                    filename: data.txt.gz
                    format: tabix
                scores:
                    - id: phastCons100way
                      type: float
                      desc: "The phastCons computed over the tree of 100 \
                              verterbarte species"
                      name: s1
                      histogram:
                        type: number
                        number_of_bins: 100
                        view_range:
                          min: 0.0
                          max: 1.0
                    - id: phastCons5way
                      type: int
                      aggregator: max
                      na_values: "-1"
                      desc: "The phastCons computed over the tree of 5 \
                              verterbarte species"
                      name: s2
                      histogram:
                        type: number
                        number_of_bins: 4
                        view_range:
                          min: 0.0
                          max: 4.0
                """,
        },
    })
    setup_tabix(
        tmp_path / "one" / "data.txt.gz",
        """
        #chrom pos_begin  pos_end  s1    s2
        1      10         15       0.02  -1
        1      17         19       0.03  0
        1      22         25       0.46  EMPTY
        2      5          80       0.01  3
        2      81         90       0.02  3
        """, seq_col=0, start_col=1, end_col=2)

    repo = build_filesystem_test_repository(tmp_path)

    assert repo is not None

    cli_manage(["repo-stats", "-R", str(tmp_path), "-j", "1"])
    minmax_100way_path = os.path.join(
        tmp_path, "one", "statistics", "min_max_phastCons100way.yaml",
    )
    histogram_100way_path = os.path.join(
        tmp_path, "one", "statistics", "histogram_phastCons100way.json",
    )
    histogram_image_100way_path = os.path.join(
        tmp_path, "one", "statistics", "histogram_phastCons100way.png",
    )
    minmax_5way_path = os.path.join(
        tmp_path, "one", "statistics", "min_max_phastCons5way.yaml",
    )
    histogram_5way_path = os.path.join(
        tmp_path, "one", "statistics", "histogram_phastCons5way.json",
    )
    histogram_image_5way_path = os.path.join(
        tmp_path, "one", "statistics", "histogram_phastCons5way.png",
    )
    assert not os.path.exists(minmax_100way_path)
    assert os.path.exists(histogram_100way_path)
    assert os.path.exists(histogram_image_100way_path)
    assert not os.path.exists(minmax_5way_path)
    assert os.path.exists(histogram_5way_path)
    assert os.path.exists(histogram_image_5way_path)

    phast_cons_100way_hist = NumberHistogram.deserialize(
        pathlib.Path(histogram_100way_path).read_text(),
    )

    phast_cons_5way_hist = NumberHistogram.deserialize(
        pathlib.Path(histogram_5way_path).read_text(),
    )

    assert len(phast_cons_100way_hist.bars) == 100
    assert phast_cons_100way_hist.bars[0] == 0
    assert phast_cons_100way_hist.bars[1] == 76  # region [5-80]
    assert phast_cons_100way_hist.bars[2] == 16  # region [10-15] and [10-11]
    assert phast_cons_100way_hist.bars[3] == 3  # region [17-19]
    assert phast_cons_100way_hist.bars[4] == 0
    assert phast_cons_100way_hist.bars[46] == 4  # region [22-24]
    assert phast_cons_100way_hist.bars.sum() == (76 + 16 + 3 + 4)

    assert len(phast_cons_5way_hist.bars) == 4
    assert phast_cons_5way_hist.bars[0] == 3
    assert phast_cons_5way_hist.bars[3] == 86
    assert phast_cons_5way_hist.bars.sum() == 89


def test_stats_np_score(tmp_path: pathlib.Path) -> None:
    setup_directories(tmp_path, {
        "one": {
            GR_CONF_FILE_NAME: """
                type: allele_score
                table:
                    filename: data.txt.gz
                    format: tabix
                scores:
                    - id: cadd_raw
                      type: float
                      desc: ""
                      name: s1
                      histogram:
                        type: number
                        number_of_bins: 100
                        view_range:
                          min: 0.0
                          max: 1.0

                    - id: cadd_test
                      type: int
                      aggregator: max
                      aggregator: mean
                      na_values: "-1"
                      desc: ""
                      name: s2
                      histogram:
                        type: number
                        number_of_bins: 4
                        view_range:
                          min: 0.0
                          max: 4.0
            """,
        },
    })
    setup_tabix(
        tmp_path / "one" / "data.txt.gz",
        """
        #chrom pos_begin  reference  alternative  s1    s2
        1      10         A          G            0.02  2
        1      10         A          C            0.03  -1
        1      10         A          T            0.04  4
        1      16         C          G            0.03  3
        1      16         C          T            0.04  EMPTY
        1      16         C          A            0.05  0
        2      16         C          A            0.03  3
        2      16         C          T            0.04  3
        2      16         C          G            0.05  4
        """, seq_col=0, start_col=1, end_col=1)

    repo = build_filesystem_test_repository(tmp_path)

    assert repo is not None

    cli_manage(["repo-stats", "-R", str(tmp_path), "-j", "1"])
    minmax_cadd_raw_path = os.path.join(
        tmp_path, "one", "statistics", "min_max_cadd_raw.yaml",
    )
    histogram_cadd_raw_path = os.path.join(
        tmp_path, "one", "statistics", "histogram_cadd_raw.json",
    )
    histogram_image_cadd_raw_path = os.path.join(
        tmp_path, "one", "statistics", "histogram_cadd_raw.png",
    )
    minmax_cadd_test_path = os.path.join(
        tmp_path, "one", "statistics", "min_max_cadd_test.yaml",
    )
    histogram_cadd_test_path = os.path.join(
        tmp_path, "one", "statistics", "histogram_cadd_test.json",
    )
    histogram_image_cadd_test_path = os.path.join(
        tmp_path, "one", "statistics", "histogram_cadd_test.png",
    )
    assert not os.path.exists(minmax_cadd_raw_path)
    assert os.path.exists(histogram_cadd_raw_path)
    assert os.path.exists(histogram_image_cadd_raw_path)
    assert not os.path.exists(minmax_cadd_test_path)
    assert os.path.exists(histogram_cadd_test_path)
    assert os.path.exists(histogram_image_cadd_test_path)

    cadd_raw_hist = NumberHistogram.deserialize(
        pathlib.Path(histogram_cadd_raw_path).read_text(),
    )

    cadd_test_hist = NumberHistogram.deserialize(
        pathlib.Path(histogram_cadd_test_path).read_text(),
    )

    assert len(cadd_raw_hist.bars) == 100
    assert cadd_raw_hist.bars[2] == 1
    assert cadd_raw_hist.bars[3] == 3
    assert cadd_raw_hist.bars[4] == 3
    assert cadd_raw_hist.bars[5] == 2
    assert cadd_raw_hist.bars.sum() == (1 + 3 + 3 + 2)

    assert len(cadd_test_hist.bars) == 4
    assert cadd_test_hist.bars[0] == 1
    assert cadd_test_hist.bars[1] == 0
    assert cadd_test_hist.bars[2] == 1
    assert cadd_test_hist.bars[3] == 5
    assert cadd_test_hist.bars.sum() == (1 + 1 + 5)


def test_reference_genome_usage(
    tmp_path: pathlib.Path, mocker: pytest_mock.MockerFixture,
) -> None:
    setup_directories(tmp_path, {
        "one": {
            GR_CONF_FILE_NAME: """
                type: position_score
                table:
                    filename: data.txt.gz
                    format: tabix
                scores:
                    - id: phastCons100way
                      type: float
                      desc: "The phastCons computed over the tree of 100 \
                              verterbarte species"
                      name: s1
                      histogram:
                        type: number
                        number_of_bins: 100
                        x_log_scale: false
                        y_log_scale: false
                meta:
                    labels:
                        reference_genome: genome
            """,
        },
        "genome": {
            GR_CONF_FILE_NAME: """
                type: genome
                filename: data.fa
            """,
            "data.fa": textwrap.dedent("""
                >1
                NACGTNACGT
                NACGTNACGT
                NACGTNACGT
                >2
                NACGTNACGT
                NACGTNACGT
                NACGTNACGT
                >3
                NACGTNACGT
                NACGTNACGT
                NACGTNACGT
            """),
            "data.fa.fai": textwrap.dedent("""\
                1	30	3	10	11
                2	30	39	10	11
                3	30	75	10	11
            """),

        },
    })
    setup_tabix(
        tmp_path / "one" / "data.txt.gz",
        """
        #chrom  pos_begin  pos_end  s1
        1      10         15       0.0
        1      17         19       0.03
        1      22         25       0.46
        2      5          8        0.01
        2      10         11       1.0
        3      5          17       1.0
        3      18         20       0.01
        """, seq_col=0, start_col=1, end_col=2)

    repo = build_filesystem_test_repository(tmp_path)

    assert repo is not None

    ref_genome_length_mock = mocker.Mock(return_value=30)
    mocker.patch(
        "gain.genomic_resources.reference_genome"
        ".ReferenceGenome.get_chrom_length",
        new=ref_genome_length_mock,
    )
    assert ref_genome_length_mock.call_count == 0
    cli_manage([
        "resource-stats", "-r", "one", "-R", str(tmp_path), "-j", "1",
    ])
    assert ref_genome_length_mock.call_count == 3
    assert (tmp_path / "one" / "statistics" / "stats_hash").exists()

    labels_mock = mocker.Mock(return_value={})
    mocker.patch(
        "gain.genomic_resources.repository."
        "GenomicResource.get_labels",
        new=labels_mock,
    )

    genomic_table_length_mock = mocker.Mock(return_value=30)
    # Patched where the probe lives: the implementation layer reads contig
    # length off the table now and no longer imports the tabix probe (gain#509).
    mocker.patch(
        "gain.genomic_resources.genomic_position_table."
        "table_tabix.get_chromosome_length_tabix",
        new=genomic_table_length_mock,
    )

    os.remove(os.path.join(tmp_path, "one", "statistics", "stats_hash"))

    assert genomic_table_length_mock.call_count == 0

    cli_manage([
        "resource-stats", "-r", "one", "-R", str(tmp_path), "-j", "1",
    ])

    assert genomic_table_length_mock.call_count == 3
    assert ref_genome_length_mock.call_count == 3


def test_stats_categorical(tmp_path: pathlib.Path) -> None:
    setup_directories(tmp_path, {
        "one": {
            GR_CONF_FILE_NAME: """
                type: position_score
                table:
                    filename: data.txt.gz
                    format: tabix
                scores:
                    - id: some_stat
                      type: str
                      desc: "desc"
                      name: s1
                      histogram:
                        type: categorical
                        value_order: []
                """,
        },
    })
    setup_tabix(
        tmp_path / "one" / "data.txt.gz",
        """
        #chrom  pos_begin  pos_end  s1
        1       10         10       value1
        1       17         17       value1
        1       22         22       value2
        2       5          5        value3
        2       10         10       value2
        """, seq_col=0, start_col=1, end_col=2)

    repo = build_filesystem_test_repository(tmp_path)

    assert repo is not None

    cli_manage(["repo-stats", "-R", str(tmp_path), "-j", "1"])

    histogram_statistic_path = os.path.join(
        tmp_path, "one", "statistics", "histogram_some_stat.json",
    )

    stat_hist = CategoricalHistogram.deserialize(
        pathlib.Path(histogram_statistic_path).read_text(),
    )

    assert len(stat_hist.display_values) == 3
    assert stat_hist.display_values["value1"] == 2
    assert stat_hist.display_values["value2"] == 2
    assert stat_hist.display_values["value3"] == 1


CATEGORIES_PAST_LIMIT = CategoricalHistogram.UNIQUE_VALUES_LIMIT + 50
CATEGORIES_WITHIN_LIMIT = 50


def a_categorical_score(
    tmp_path: pathlib.Path,
    unique_values: int,
    histogram: dict[str, Any] | None = None,
) -> None:
    """Realize a tabix position score with distinct per-position str values."""
    data_rows = "\n".join(
        f"1 {10 + i} {10 + i} v{i:03d}"
        for i in range(unique_values))
    (
        a_position_score()
        .with_score("cell", "str")
        .with_histogram(
            histogram or {"type": "categorical", "value_order": []})
        .with_data("chrom pos_begin pos_end cell\n" + data_rows)
        .with_tabix()
        .build_resource(tmp_path)
    )


def a_categorical_score_past_limit(
    tmp_path: pathlib.Path,
) -> None:
    """Realize a tabix position score with 150 distinct str values."""
    a_categorical_score(tmp_path, CATEGORIES_PAST_LIMIT)


def drop_everything_but_statistics(tmp_path: pathlib.Path) -> None:
    """Remove the resource's files so a smaller twin can be realized."""
    for entry in tmp_path.iterdir():
        if entry.name == "statistics":
            continue
        if entry.is_dir():
            shutil.rmtree(entry)
        else:
            entry.unlink()


def test_stats_categorical_past_limit_writes_truncated_sidecar(
        tmp_path: pathlib.Path) -> None:
    a_categorical_score_past_limit(tmp_path)

    cli_manage(["repo-stats", "-R", str(tmp_path), "-j", "1"])

    sidecar = json.loads(
        (tmp_path / "statistics" / "histogram_cell_truncated.json")
        .read_text())
    assert sidecar["truncated"] is True
    assert sidecar["unique_values"] == CATEGORIES_PAST_LIMIT
    assert sidecar["total_count"] == CATEGORIES_PAST_LIMIT
    assert len(sidecar["values"]) == 20


def test_stats_categorical_past_limit_keeps_full_histogram_format(
        tmp_path: pathlib.Path) -> None:
    a_categorical_score_past_limit(tmp_path)

    cli_manage(["repo-stats", "-R", str(tmp_path), "-j", "1"])

    full = json.loads(
        (tmp_path / "statistics" / "histogram_cell.json").read_text())
    assert set(full) == {"config", "values"}
    assert len(full["values"]) == CATEGORIES_PAST_LIMIT


def test_stats_categorical_past_limit_manifests_the_sidecar(
        tmp_path: pathlib.Path) -> None:
    a_categorical_score_past_limit(tmp_path)

    cli_manage(["repo-stats", "-R", str(tmp_path), "-j", "1"])

    manifest = (tmp_path / ".MANIFEST").read_text()
    assert "statistics/histogram_cell_truncated.json" in manifest


def test_stats_categorical_within_limit_writes_no_sidecar(
        tmp_path: pathlib.Path) -> None:
    (
        a_position_score()
        .with_score("cell", "str")
        .with_histogram({"type": "categorical", "value_order": []})
        .with_data("""
            chrom  pos_begin  pos_end  cell
            1      10         10       value1
            1      17         17       value2
        """)
        .with_tabix()
        .build_resource(tmp_path)
    )

    cli_manage(["repo-stats", "-R", str(tmp_path), "-j", "1"])

    assert (tmp_path / "statistics" / "histogram_cell.json").exists()
    assert not (
        tmp_path / "statistics" / "histogram_cell_truncated.json").exists()


def test_get_score_histogram_truncated_reads_the_sidecar(
        tmp_path: pathlib.Path) -> None:
    a_categorical_score_past_limit(tmp_path)
    cli_manage(["repo-stats", "-R", str(tmp_path), "-j", "1"])
    score = build_score_from_resource(
        build_filesystem_test_repository(tmp_path).get_resource(""))

    hist = score.get_score_histogram("cell", truncated=True)

    assert isinstance(hist, CategoricalHistogram)
    assert hist.truncated
    assert hist.unique_values == CATEGORIES_PAST_LIMIT
    assert len(hist.raw_values) == 20


def test_get_score_histogram_truncated_falls_back_to_the_full_file(
        tmp_path: pathlib.Path) -> None:
    (
        a_position_score()
        .with_score("cell", "str")
        .with_histogram({"type": "categorical", "value_order": []})
        .with_data("""
            chrom  pos_begin  pos_end  cell
            1      10         10       value1
            1      17         17       value2
        """)
        .with_tabix()
        .build_resource(tmp_path)
    )
    cli_manage(["repo-stats", "-R", str(tmp_path), "-j", "1"])
    score = build_score_from_resource(
        build_filesystem_test_repository(tmp_path).get_resource(""))

    hist = score.get_score_histogram("cell", truncated=True)

    assert isinstance(hist, CategoricalHistogram)
    assert not hist.truncated
    assert hist.raw_values == {"value1": 1, "value2": 1}


def test_get_score_histogram_truncated_is_a_noop_for_number_histograms(
        tmp_path: pathlib.Path) -> None:
    (
        a_position_score()
        .with_score("score", "float")
        .with_data("""
            chrom  pos_begin  pos_end  score
            1      10         10       0.1
            1      17         17       0.4
        """)
        .with_tabix()
        .build_resource(tmp_path)
    )
    cli_manage(["repo-stats", "-R", str(tmp_path), "-j", "1"])
    score = build_score_from_resource(
        build_filesystem_test_repository(tmp_path).get_resource(""))

    hist = score.get_score_histogram("score", truncated=True)

    assert isinstance(hist, NumberHistogram)


def test_get_score_histogram_truncated_falls_back_when_sidecar_missing(
        tmp_path: pathlib.Path) -> None:
    a_categorical_score_past_limit(tmp_path)
    cli_manage(["repo-stats", "-R", str(tmp_path), "-j", "1"])
    score = build_score_from_resource(
        build_filesystem_test_repository(tmp_path).get_resource(""))
    # Prime the manifest cache while the sidecar is still manifested, then
    # remove the file: the sidecar is now listed but unreadable.
    score.get_score_histogram("cell", truncated=True)
    (tmp_path / "statistics" / "histogram_cell_truncated.json").unlink()

    hist = score.get_score_histogram("cell", truncated=True)

    assert isinstance(hist, CategoricalHistogram)
    assert not hist.truncated
    assert len(hist.raw_values) == CATEGORIES_PAST_LIMIT


def test_get_score_histogram_default_raises_when_full_values_absent(
        tmp_path: pathlib.Path) -> None:
    a_categorical_score_past_limit(tmp_path)
    cli_manage(["repo-stats", "-R", str(tmp_path), "-j", "1"])
    (tmp_path / "statistics" / "histogram_cell.json").unlink()
    score = build_score_from_resource(
        build_filesystem_test_repository(tmp_path).get_resource(""))

    with pytest.raises(HistogramError, match="histogram_cell"):
        score.get_score_histogram("cell")


def test_get_score_histogram_default_wraps_existence_probe_errors(
        tmp_path: pathlib.Path,
        mocker: pytest_mock.MockerFixture) -> None:
    a_categorical_score_past_limit(tmp_path)
    cli_manage(["repo-stats", "-R", str(tmp_path), "-j", "1"])
    score = build_score_from_resource(
        build_filesystem_test_repository(tmp_path).get_resource(""))
    mocker.patch.object(
        score.resource, "file_exists",
        side_effect=OSError("connection failed"))

    with pytest.raises(HistogramError, match="histogram_cell"):
        score.get_score_histogram("cell")


def test_get_score_histogram_truncated_survives_absent_full_values(
        tmp_path: pathlib.Path) -> None:
    a_categorical_score_past_limit(tmp_path)
    cli_manage(["repo-stats", "-R", str(tmp_path), "-j", "1"])
    (tmp_path / "statistics" / "histogram_cell.json").unlink()
    score = build_score_from_resource(
        build_filesystem_test_repository(tmp_path).get_resource(""))

    hist = score.get_score_histogram("cell", truncated=True)

    assert isinstance(hist, CategoricalHistogram)
    assert hist.truncated


def test_stats_rebuild_below_limit_removes_the_stale_sidecar(
        tmp_path: pathlib.Path) -> None:
    a_categorical_score(tmp_path, CATEGORIES_PAST_LIMIT)
    cli_manage(["repo-stats", "-R", str(tmp_path), "-j", "1"])
    drop_everything_but_statistics(tmp_path)
    a_categorical_score(tmp_path, CATEGORIES_WITHIN_LIMIT)

    cli_manage(["repo-stats", "-R", str(tmp_path), "-j", "1"])

    assert not (
        tmp_path / "statistics" / "histogram_cell_truncated.json").exists()
    assert "histogram_cell_truncated.json" not in (
        tmp_path / ".MANIFEST").read_text()


def test_info_pages_render_without_the_full_histogram_values(
        tmp_path: pathlib.Path) -> None:
    a_categorical_score_past_limit(tmp_path)
    cli_manage(["repo-stats", "-R", str(tmp_path), "-j", "1"])
    (tmp_path / "statistics" / "histogram_cell.json").unlink()
    impl = build_resource_implementation(
        build_filesystem_test_repository(tmp_path).get_resource(""))

    info = impl.get_info()
    statistics_info = impl.get_statistics_info()

    assert f"top 20 of {CATEGORIES_PAST_LIMIT} values" in info
    assert "histogram_cell.png" in statistics_info


def test_contents_db_not_rebuilt_when_contents_unchanged(
    tmp_path: pathlib.Path, register_test_implementation: None,
) -> None:
    setup_directories(tmp_path, {
        "one": {
            GR_CONF_FILE_NAME: "type: test_resource\n",
        },
    })
    build_filesystem_test_repository(tmp_path)

    cli_manage(["repo-stats", "-R", str(tmp_path), "-j", "1"])

    db_path = tmp_path / GR_SQLITE_META_FILE_NAME
    assert db_path.exists()
    first_bytes = db_path.read_bytes()

    cli_manage(["repo-stats", "-R", str(tmp_path), "-j", "1"])

    assert db_path.read_bytes() == first_bytes


def test_contents_db_rebuilt_when_contents_change(
    tmp_path: pathlib.Path, register_test_implementation: None,
) -> None:
    setup_directories(tmp_path, {
        "one": {
            GR_CONF_FILE_NAME: "type: test_resource\n",
        },
    })
    build_filesystem_test_repository(tmp_path)

    cli_manage(["repo-stats", "-R", str(tmp_path), "-j", "1"])

    db_path = tmp_path / GR_SQLITE_META_FILE_NAME
    first_bytes = db_path.read_bytes()

    setup_directories(tmp_path, {
        "two": {
            GR_CONF_FILE_NAME: "type: test_resource\n",
        },
    })
    cli_manage(["repo-stats", "-R", str(tmp_path), "-j", "1"])

    assert db_path.read_bytes() != first_bytes


def test_stats_csi_indexed_position_score_matches_its_tbi_twin(
    tmp_path: pathlib.Path,
) -> None:
    """gain#430: a .csi-indexed tabix score statisticises like a .tbi one."""
    data = """
        chrom  pos_begin  pos_end  value
        chr1   10         15       0.2
        chr1   17         19       0.4
    """

    def a_score(*, csi: bool) -> PositionScoreBuilder:
        return (
            a_position_score()
            .with_tabix(csi=csi)
            .with_zero_based()
            .with_score("value", "float")
            .with_histogram({
                "type": "number", "number_of_bins": 4,
                "view_range": {"min": 0.0, "max": 1.0}})
            .with_data(data)
        )

    (
        a_grr()
        .with_resource("csi_score", a_score(csi=True))
        .with_resource("tbi_score", a_score(csi=False))
        .build_repo(tmp_path)
    )

    cli_manage(["repo-stats", "-R", str(tmp_path), "-j", "1"])

    csi_histogram = tmp_path / "csi_score/statistics/histogram_value.json"
    tbi_histogram = tmp_path / "tbi_score/statistics/histogram_value.json"
    assert csi_histogram.exists()
    assert csi_histogram.read_text() == tbi_histogram.read_text()
    assert NumberHistogram.deserialize(
        csi_histogram.read_text()).bars.sum() > 0
