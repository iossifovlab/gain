# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""``fragment_score_binner`` on resources without a ``sample_id`` label.

A merged multi-sample fragment file has no single sample.  Without a
``meta``, a cell grouping of such a resource reads every row of its cell
metadata table, and a pooled raw-value grouping gives bare groups.  A
pool whose resources disagree on the label is refused.  ``STUDY_META``
is a study-wide table whose barcodes are unique across its samples.
"""
import json
import pathlib
from typing import Any

import pytest
from gain.binning.fragment_binner import (
    CELL_META_RESOURCE_ID_LABEL,
    SAMPLE_ID_LABEL,
    FragmentBinningJob,
    job_track_maps,
)
from gain.genomic_resources.reference_genome import ReferenceGenome
from gain.genomic_resources.testing.data_frame_builder import a_data_frame

from tests.small.binning.test_fragment_convention import (
    S1_LABELS,
    build_repo,
    fragments,
    parse,
    refusal,
)

#: Two samples and a row with an empty ``sample_id`` cell; each barcode
#: is on one row.
STUDY_META = """sample_id,barcode,class
S1,AAA,T
S2,BBB,B
,CCC,X
S2,EEE,T
"""
STUDY_LABELS: dict[str, Any] = {CELL_META_RESOURCE_ID_LABEL: "meta/study"}
EVERY_ROW = {"AAA": "T", "BBB": "B", "CCC": "X", "EEE": "T"}


def no_sample_warning(resource_id: str) -> str:
    return (
        f"resource {resource_id!r} has no {SAMPLE_ID_LABEL!r} label; its "
        f"fragments are grouped by every row of the cell metadata table")


def track_groups(job: FragmentBinningJob, repo: Any) -> list[dict[str, str]]:
    """Per resource, each barcode's group, read through ``job_track_maps``."""
    maps = job_track_maps(job, repo)
    assert maps is not None
    return [
        {barcode: job.tracks[index].group for barcode, index in m.items()}
        for m in maps]


def test_a_resource_without_sample_id_groups_by_every_row_of_its_table(
    tmp_path: pathlib.Path, genome: ReferenceGenome,
) -> None:
    repo = build_repo(
        tmp_path, ("meta/study", a_data_frame().with_raw_content(STUDY_META)),
        ("frags/merged", fragments(**STUDY_LABELS)))

    run = parse({"resource_query": "frags/merged"}, repo, genome)

    assert [t.group for t in run.tracks] == ["B", "T", "X"]
    (job,) = run.jobs
    assert isinstance(job, FragmentBinningJob)
    assert job.warnings == (no_sample_warning("frags/merged"),)
    assert track_groups(job, repo) == [EVERY_ROW]
    assert json.loads(run.tracks[0].parameters)["filter"] == []


def test_the_fallback_needs_no_sample_id_column(
    tmp_path: pathlib.Path, genome: ReferenceGenome,
) -> None:
    repo = build_repo(
        tmp_path, ("meta/study", a_data_frame().with_raw_content(
            "barcode,class\nAAA,T\nBBB,B\n")),
        ("frags/merged", fragments(**STUDY_LABELS)))

    (job,) = parse({"resource_query": "frags/merged"}, repo, genome).jobs

    assert isinstance(job, FragmentBinningJob)
    assert track_groups(job, repo) == [{"AAA": "T", "BBB": "B"}]


def test_an_explicit_filter_by_a_missing_label_is_still_refused(
    tmp_path: pathlib.Path, genome: ReferenceGenome,
) -> None:
    repo = build_repo(
        tmp_path, ("meta/study", a_data_frame().with_raw_content(STUDY_META)),
        ("frags/merged", fragments(**STUDY_LABELS)))

    message = refusal({"resource_query": "frags/merged", "meta": {
        "resource_label": CELL_META_RESOURCE_ID_LABEL,
        "filter": [{"column": "sample_id", "label": SAMPLE_ID_LABEL}]}},
        repo, genome)

    assert "'frags/merged'" in message
    assert f"no label {SAMPLE_ID_LABEL!r}" in message


def test_a_barcode_repeated_in_the_whole_table_is_refused(
    tmp_path: pathlib.Path, genome: ReferenceGenome,
) -> None:
    # CELL_META repeats AAA across S1 and S2; the fallback reads both.
    labels: dict[str, Any] = {CELL_META_RESOURCE_ID_LABEL: "meta/cells"}
    repo = build_repo(tmp_path, ("frags/merged", fragments(**labels)))

    message = refusal({"resource_query": "frags/merged"}, repo, genome)

    assert "'frags/merged'" in message
    assert "'AAA'" in message
    assert "more than one" in message


@pytest.mark.parametrize("other", [
    {SAMPLE_ID_LABEL: "S1"}, {SAMPLE_ID_LABEL: ["S1", "S2"]}])
def test_a_pool_that_disagrees_on_sample_id_is_refused_naming_each_side(
    tmp_path: pathlib.Path, genome: ReferenceGenome, other: dict[str, Any],
) -> None:
    repo = build_repo(
        tmp_path, ("meta/study", a_data_frame().with_raw_content(STUDY_META)),
        ("frags/a", fragments(**STUDY_LABELS, **other)),
        ("frags/b", fragments(**STUDY_LABELS)))

    message = refusal({"resource_query": "frags/*"}, repo, genome)

    assert f"{SAMPLE_ID_LABEL!r}" in message
    assert "'frags/a' carry it" in message
    assert "'frags/b' do not" in message


def test_a_mixed_pool_with_an_explicit_meta_reads_the_label_nowhere(
    tmp_path: pathlib.Path, genome: ReferenceGenome,
) -> None:
    repo = build_repo(
        tmp_path, ("meta/study", a_data_frame().with_raw_content(STUDY_META)),
        ("frags/a", fragments(**STUDY_LABELS, **S1_LABELS)),
        ("frags/b", fragments(**STUDY_LABELS)))

    (job,) = parse({"resource_query": "frags/*",
                    "meta": {"resource_id": "meta/study"}}, repo, genome).jobs

    assert isinstance(job, FragmentBinningJob)
    assert job.warnings == ()
    assert track_groups(job, repo) == [EVERY_ROW, EVERY_ROW]


def test_a_pool_without_sample_id_groups_each_by_every_row(
    tmp_path: pathlib.Path, genome: ReferenceGenome,
) -> None:
    repo = build_repo(
        tmp_path, ("meta/study", a_data_frame().with_raw_content(STUDY_META)),
        ("frags/a", fragments(**STUDY_LABELS)),
        ("frags/b", fragments(**STUDY_LABELS)))

    run = parse({"resource_query": "frags/*"}, repo, genome)

    assert [t.group for t in run.tracks] == ["B", "T", "X"]
    (job,) = run.jobs
    assert isinstance(job, FragmentBinningJob)
    assert job.warnings == (
        no_sample_warning("frags/a"), no_sample_warning("frags/b"))
    assert track_groups(job, repo) == [EVERY_ROW, EVERY_ROW]
