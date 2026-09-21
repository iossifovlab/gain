# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""The schema check's reading of a declared file (gain#1586)."""
import json
import pathlib

import pytest
from gain.genomic_resources.statistics.schema import (
    StaleStatisticsFile,
    StatisticsFile,
    stale_statistics_files,
)
from gain.genomic_resources.testing.builders import a_position_score

DECLARED = StatisticsFile("statistics/coverage.json", 2)


def _a_resource_holding(tmp_path: pathlib.Path, content: str | None):
    resource = a_position_score().build_resource(tmp_path)
    if content is not None:
        statistics = tmp_path / "statistics"
        statistics.mkdir()
        (statistics / "coverage.json").write_text(content)
    return resource


@pytest.mark.parametrize(("content", "expected"), [
    pytest.param(None, [StaleStatisticsFile(DECLARED.path, None, 2)],
                 id="missing"),
    pytest.param(json.dumps({"chromosomes": {}}),
                 [StaleStatisticsFile(DECLARED.path, 0, 2)],
                 id="no version reads as 0"),
    pytest.param(json.dumps({"format_version": 1}),
                 [StaleStatisticsFile(DECLARED.path, 1, 2)],
                 id="behind"),
    pytest.param(json.dumps({"format_version": 2}), [], id="current"),
    pytest.param(json.dumps({"format_version": 3}), [],
                 id="ahead is not stale"),
    pytest.param("{not json", [], id="unparseable is not this check's"),
    pytest.param(json.dumps([1, 2]), [], id="not an object is not either"),
    pytest.param(json.dumps({"format_version": "2"}), [],
                 id="nor a non-integer version"),
])
def test_what_a_declared_file_reads_as(
    tmp_path: pathlib.Path,
    content: str | None,
    expected: list[StaleStatisticsFile],
) -> None:
    resource = _a_resource_holding(tmp_path, content)

    assert stale_statistics_files(resource, [DECLARED]) == expected


def test_the_line_names_missing_and_the_versions() -> None:
    assert StaleStatisticsFile("statistics/x.json", None, 2).describe() \
        == "statistics/x.json missing -> 2"
    assert StaleStatisticsFile("statistics/x.json", 1, 2).describe() \
        == "statistics/x.json 1 -> 2"
