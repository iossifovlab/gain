# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""A full categorical histogram past the limit is stored as ``.json.gz``.

gain#1733: the full file stays the complete record, only its encoding
changes; the plain truncated sidecar decides which encoding a reader loads.
"""
import gzip
import json
import pathlib
import time

import pytest
from gain.genomic_resources.cli import cli_manage
from gain.genomic_resources.genomic_scores import build_score_from_resource
from gain.genomic_resources.histogram import (
    CategoricalHistogram,
    CategoricalHistogramConfig,
)
from gain.genomic_resources.score_resource import ScoreResource
from gain.genomic_resources.testing import build_filesystem_test_repository

from .test_cli_stats import (
    A_NUMBER_HISTOGRAM,
    CATEGORIES_PAST_LIMIT,
    CATEGORIES_WITHIN_LIMIT,
    a_categorical_score,
    a_categorical_score_past_limit,
    a_float_score,
    drop_everything_but_statistics,
)

GZIP_MAGIC = b"\x1f\x8b"


def statistics_of(tmp_path: pathlib.Path) -> pathlib.Path:
    return tmp_path / "statistics"


def test_past_limit_build_writes_a_gzipped_full_histogram(
        tmp_path: pathlib.Path) -> None:
    a_categorical_score_past_limit(tmp_path)

    cli_manage(["repo-stats", "-R", str(tmp_path), "-j", "1"])

    content = (
        statistics_of(tmp_path) / "histogram_cell.json.gz").read_bytes()
    assert content[:2] == GZIP_MAGIC
    expected = CategoricalHistogram(
        CategoricalHistogramConfig(value_order=[]),
        {f"v{i:03d}": 1 for i in range(CATEGORIES_PAST_LIMIT)},
    )
    assert json.loads(gzip.decompress(content)) == expected.to_dict()


def test_past_limit_build_keeps_the_plain_sidecar_and_no_plain_full_file(
        tmp_path: pathlib.Path) -> None:
    a_categorical_score_past_limit(tmp_path)

    cli_manage(["repo-stats", "-R", str(tmp_path), "-j", "1"])

    sidecar = json.loads(
        (statistics_of(tmp_path) / "truncated" / "histogram_cell.json")
        .read_text())
    assert sidecar["truncated"] is True
    assert sidecar["unique_values"] == CATEGORIES_PAST_LIMIT
    assert not (statistics_of(tmp_path) / "histogram_cell.json").exists()
    assert not (
        statistics_of(tmp_path) / "truncated" / "histogram_cell.json.gz"
    ).exists()


def histogram_files_of(tmp_path: pathlib.Path) -> set[str]:
    return {
        str(path.relative_to(statistics_of(tmp_path)))
        for path in statistics_of(tmp_path).rglob("histogram_*")}


def test_within_limit_build_writes_the_plain_full_histogram_only(
        tmp_path: pathlib.Path) -> None:
    a_categorical_score(tmp_path, CATEGORIES_WITHIN_LIMIT)

    cli_manage(["repo-stats", "-R", str(tmp_path), "-j", "1"])

    assert histogram_files_of(tmp_path) == {
        "histogram_cell.json", "histogram_cell.png"}
    expected = CategoricalHistogram(
        CategoricalHistogramConfig(value_order=[]),
        {f"v{i:03d}": 1 for i in range(CATEGORIES_WITHIN_LIMIT)},
    )
    assert (statistics_of(tmp_path) / "histogram_cell.json").read_text() \
        == json.dumps(expected.to_dict(), indent=2)


def test_number_build_writes_the_plain_full_histogram_only(
        tmp_path: pathlib.Path) -> None:
    a_float_score(tmp_path, A_NUMBER_HISTOGRAM)

    cli_manage(["repo-stats", "-R", str(tmp_path), "-j", "1"])

    assert histogram_files_of(tmp_path) == {
        "histogram_score.json", "histogram_score.png"}
    content = (statistics_of(tmp_path) / "histogram_score.json").read_text()
    assert content == json.dumps(json.loads(content), indent=2)
    assert json.loads(content)["bars"] == [1, 0, 1, 1]


def an_over_limit_histogram() -> CategoricalHistogram:
    return CategoricalHistogram(
        CategoricalHistogramConfig(value_order=[]),
        {f"v{i:03d}": i + 1 for i in range(CATEGORIES_PAST_LIMIT)},
    )


def test_gzipped_serialisation_is_byte_identical_across_a_clock_advance(
        monkeypatch: pytest.MonkeyPatch) -> None:
    histogram = an_over_limit_histogram()
    monkeypatch.setattr(time, "time", lambda: 1_000_000_000.0)
    first = histogram.serialize_gzipped()
    monkeypatch.setattr(time, "time", lambda: 1_700_000_000.0)

    second = histogram.serialize_gzipped()

    assert first == second
    assert json.loads(gzip.decompress(first)) == histogram.to_dict()


def a_score_resource(tmp_path: pathlib.Path) -> ScoreResource:
    return build_score_from_resource(
        build_filesystem_test_repository(tmp_path).get_resource(""))


def test_get_score_histogram_loads_the_full_histogram_from_a_gzipped_build(
        tmp_path: pathlib.Path) -> None:
    a_categorical_score_past_limit(tmp_path)
    cli_manage(["repo-stats", "-R", str(tmp_path), "-j", "1"])

    hist = a_score_resource(tmp_path).get_score_histogram("cell")

    assert isinstance(hist, CategoricalHistogram)
    assert not hist.truncated
    assert hist.raw_values == {
        f"v{i:03d}": 1 for i in range(CATEGORIES_PAST_LIMIT)}


def test_get_score_histogram_truncated_loads_the_sidecar_of_a_gzipped_build(
        tmp_path: pathlib.Path) -> None:
    a_categorical_score_past_limit(tmp_path)
    cli_manage(["repo-stats", "-R", str(tmp_path), "-j", "1"])

    hist = a_score_resource(tmp_path).get_score_histogram(
        "cell", truncated=True)

    assert isinstance(hist, CategoricalHistogram)
    assert hist.truncated
    assert hist.unique_values == CATEGORIES_PAST_LIMIT
    assert len(hist.raw_values) == 20


def test_get_score_histogram_loads_a_pre_switch_plain_full_histogram(
        tmp_path: pathlib.Path) -> None:
    """A GRR built before the switch: plain full file plus sidecar."""
    a_categorical_score_past_limit(tmp_path)
    histogram = an_over_limit_histogram()
    (statistics_of(tmp_path) / "truncated").mkdir(parents=True)
    (statistics_of(tmp_path) / "histogram_cell.json").write_text(
        histogram.serialize())
    (statistics_of(tmp_path) / "truncated" / "histogram_cell.json") \
        .write_text(histogram.serialize_truncated())
    score = a_score_resource(tmp_path)
    assert "statistics/histogram_cell.json.gz" not in \
        score.resource.get_manifest()

    hist = score.get_score_histogram("cell")

    assert isinstance(hist, CategoricalHistogram)
    assert not hist.truncated
    assert hist.raw_values == histogram.raw_values


def test_get_score_histogram_without_a_sidecar_ignores_a_leftover_gzip(
        tmp_path: pathlib.Path) -> None:
    """The sidecar decides: a rebuild within the limit drops the sidecar
    and writes the plain file, so the plain file is what loads even while
    the earlier ``.json.gz`` is still present and manifested."""
    a_categorical_score(tmp_path, CATEGORIES_PAST_LIMIT)
    cli_manage(["repo-stats", "-R", str(tmp_path), "-j", "1"])
    drop_everything_but_statistics(tmp_path)
    a_categorical_score(tmp_path, CATEGORIES_WITHIN_LIMIT)
    cli_manage(["repo-stats", "-R", str(tmp_path), "-j", "1"])
    score = a_score_resource(tmp_path)
    assert "statistics/histogram_cell.json.gz" in \
        score.resource.get_manifest()

    hist = score.get_score_histogram("cell")

    assert isinstance(hist, CategoricalHistogram)
    assert len(hist.raw_values) == CATEGORIES_WITHIN_LIMIT


def test_get_score_histogram_with_a_sidecar_ignores_a_leftover_plain_file(
        tmp_path: pathlib.Path) -> None:
    """The sidecar decides: a rebuild past the limit writes the sidecar and
    the ``.json.gz``, so the ``.json.gz`` is what loads even while the
    earlier within-limit plain file is still present and manifested."""
    a_categorical_score(tmp_path, CATEGORIES_WITHIN_LIMIT)
    cli_manage(["repo-stats", "-R", str(tmp_path), "-j", "1"])
    drop_everything_but_statistics(tmp_path)
    a_categorical_score(tmp_path, CATEGORIES_PAST_LIMIT)
    cli_manage(["repo-stats", "-R", str(tmp_path), "-j", "1"])
    score = a_score_resource(tmp_path)
    assert "statistics/histogram_cell.json" in score.resource.get_manifest()

    hist = score.get_score_histogram("cell")

    assert isinstance(hist, CategoricalHistogram)
    assert not hist.truncated
    assert hist.raw_values == {
        f"v{i:03d}": 1 for i in range(CATEGORIES_PAST_LIMIT)}
