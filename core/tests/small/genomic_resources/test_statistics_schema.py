# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""The schema check's reading of a declared file (gain#1586)."""
import json
import os
import pathlib

import pytest
from gain.genomic_resources.repository import GenomicResource
from gain.genomic_resources.statistics.schema import (
    StaleStatisticsFile,
    StatisticsFile,
    stale_statistics_files,
)
from gain.genomic_resources.testing.builders import a_position_score

DECLARED = StatisticsFile("statistics/coverage.json", 2)


def _a_resource_holding(
    tmp_path: pathlib.Path, content: str | None,
) -> GenomicResource:
    builder = a_position_score()
    if content is not None:
        builder = builder.with_file(DECLARED.path, content)
    return builder.build_resource(tmp_path)


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
    pytest.param(json.dumps({"format_version": True}), [],
                 id="nor a boolean one"),
])
def test_what_a_declared_file_reads_as(
    tmp_path: pathlib.Path,
    content: str | None,
    expected: list[StaleStatisticsFile],
) -> None:
    resource = _a_resource_holding(tmp_path, content)

    assert stale_statistics_files(resource, [DECLARED]) == expected


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads any mode")
def test_a_file_that_cannot_be_read_is_not_this_checks_either(
    tmp_path: pathlib.Path,
) -> None:
    """Broken, not stale -- and so never a reason for a dry run to exit."""
    resource = _a_resource_holding(
        tmp_path, json.dumps({"format_version": 1}))
    (tmp_path / "statistics" / "coverage.json").chmod(0)

    assert stale_statistics_files(resource, [DECLARED]) == []


def test_the_line_names_missing_and_the_versions() -> None:
    assert StaleStatisticsFile("statistics/x.json", None, 2).describe() \
        == "statistics/x.json missing -> 2"
    assert StaleStatisticsFile("statistics/x.json", 1, 2).describe() \
        == "statistics/x.json 1 -> 2"
