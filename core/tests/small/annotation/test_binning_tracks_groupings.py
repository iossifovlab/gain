# pylint: disable=redefined-outer-name,C0114,C0116
"""The groupings of ``binning_tool`` in ``binning_tracks_annotator``.

The GRR holds two samples, ``frags/s1`` and ``frags/s2``, and their cell
metadata table ``meta/cells``.  Each sample carries its ``sample_id``
label and the label that names ``meta/cells``.  The statistics of the
GRR are built, so each ``cell`` score has its categorical histogram.

The fragments on ``chr1``, each with its barcode and the ``class`` of
that barcode in the rows of its sample:

- ``frags/s1``: AAA at 3 (T), BBB at 10 (B), CCC at 11 (empty class),
  AAA at 15 (T) and DDD at 33 (no row).
- ``frags/s2``: AAA at 4 (B), EEE at 12, 16 and 35 (T).

The annotatable ``chr1:1-40`` is one bin of the grid of 40, and each
fragment starts in it.  So ``frags/s1`` counts B 1 and T 2, and
``frags/s2`` counts B 1 and T 3.
"""

import logging
import pathlib
from typing import Any

import pytest
import yaml
from gain.annotation.annotatable import Region
from gain.annotation.annotation_config import AnnotationConfigurationError
from gain.annotation.annotation_factory import load_pipeline_from_yaml
from gain.annotation.annotation_pipeline import AnnotationPipeline
from gain.binning.fragment_binner import (
    CELL_META_RESOURCE_ID_LABEL,
    SAMPLE_ID_LABEL,
)
from gain.genomic_resources.cli import cli_manage
from gain.genomic_resources.repository import GenomicResourceRepo
from gain.genomic_resources.testing import build_filesystem_test_repository
from gain.genomic_resources.testing.builders import a_grr
from gain.genomic_resources.testing.data_frame_builder import a_data_frame
from gain.genomic_resources.testing.table_score_builder import a_fragment_score

CHR1 = Region("chr1", 1, 40)
S1_FRAGMENTS = """
    chrom  pos_begin  pos_end  cell
    chr1   3          12       AAA
    chr1   10         25       BBB
    chr1   11         14       CCC
    chr1   15         40       AAA
    chr1   33         34       DDD
"""
S2_FRAGMENTS = """
    chrom  pos_begin  pos_end  cell
    chr1   4          8        AAA
    chr1   12         20       EEE
    chr1   16         22       EEE
    chr1   35         50       EEE
"""
#: Written verbatim: a whitespace block has no spelling for an empty cell.
CELL_META = """sample_id,barcode,class
S1,AAA,T
S1,BBB,B
S1,CCC,
S2,AAA,B
S2,EEE,T
"""
BY_CLASS_GROUP = {
    "cell_score_id": "cell", "cell_meta_column": "barcode",
    "group_meta_column": "class"}
BY_SAMPLE_META = {
    "resource_id": "meta/cells",
    "filter": [{"column": "sample_id", "label": "sample_id"}]}
#: One track for each cell of ``frags/many``.
EACH_CELL = {
    "resource_query": "frags/many", "pool": False,
    "group": {"group_score_id": "cell"}}


def labelled(sample_id: str) -> dict[str, Any]:
    return {SAMPLE_ID_LABEL: sample_id,
            CELL_META_RESOURCE_ID_LABEL: "meta/cells"}


@pytest.fixture
def repo(tmp_path: pathlib.Path) -> GenomicResourceRepo:
    grr_dir = tmp_path / "grr"
    (
        a_grr()
        .with_resource("frags/s1", a_fragment_score()
                       .with_score("cell", "str")
                       .with_labels(**labelled("S1"))
                       .with_data(S1_FRAGMENTS))
        .with_resource("frags/s2", a_fragment_score()
                       .with_score("cell", "str")
                       .with_labels(**labelled("S2"))
                       .with_data(S2_FRAGMENTS))
        .with_resource("meta/cells", a_data_frame()
                       .with_raw_content(CELL_META))
        .build_repo(grr_dir)
    )
    cli_manage(["repo-stats", "-R", str(grr_dir), "-j", "1"])
    return build_filesystem_test_repository(grr_dir)


def tracks_pipeline(
    repo: GenomicResourceRepo, *entries: dict[str, Any],
) -> AnnotationPipeline:
    config = yaml.safe_dump([{"binning_tracks_annotator": {
        "binners": [{"fragment_score_binner": entry}
                    for entry in entries]}}])
    return load_pipeline_from_yaml(config, repo)


def described_values(
    pipeline: AnnotationPipeline,
) -> list[tuple[str, str, Any]]:
    """The name, the description and the value of each track over CHR1."""
    with pipeline.open() as opened:
        result = opened.annotate(CHR1)
    descriptions = {
        attribute.name: attribute.spec.description
        for attribute in pipeline.get_attributes()
        if attribute.spec is not None}
    return [
        (name, descriptions[name], value)
        for name, value in result.items()]


def test_a_cell_grouping_reads_the_metadata_of_a_data_frame(
    repo: GenomicResourceRepo,
) -> None:
    pipeline = tracks_pipeline(repo, {
        "resource_query": "frags/s1", "group": BY_CLASS_GROUP,
        "meta": BY_SAMPLE_META})

    assert described_values(pipeline) == [
        ("track_0_0", "frags/s1:B:sum", 1.0),
        ("track_0_1", "frags/s1:T:sum", 2.0),
    ]


def test_a_value_grouping_reads_the_values_of_the_built_statistics(
    repo: GenomicResourceRepo,
) -> None:
    pipeline = tracks_pipeline(repo, {
        "resource_query": "frags/s1", "group": {"group_score_id": "cell"}})

    # A pooled entry prefixes each value with the sample of the resource.
    assert described_values(pipeline) == [
        ("track_0_0", "frags/s1:S1:AAA:sum", 2.0),
        ("track_0_1", "frags/s1:S1:BBB:sum", 1.0),
        ("track_0_2", "frags/s1:S1:CCC:sum", 1.0),
        ("track_0_3", "frags/s1:S1:DDD:sum", 1.0),
    ]


def test_an_entry_without_a_group_reads_the_labelled_metadata(
    repo: GenomicResourceRepo,
) -> None:
    pipeline = tracks_pipeline(repo, {"resource_query": "frags/s1"})

    assert described_values(pipeline) == [
        ("track_0_0", "frags/s1:B:sum", 1.0),
        ("track_0_1", "frags/s1:T:sum", 2.0),
    ]


def test_an_unpooled_entry_gives_the_groups_of_each_resource(
    repo: GenomicResourceRepo,
) -> None:
    pipeline = tracks_pipeline(
        repo, {"resource_query": "frags/*", "pool": False})

    assert described_values(pipeline) == [
        ("track_0_0", "frags/s1:B:sum", 1.0),
        ("track_0_1", "frags/s1:T:sum", 2.0),
        ("track_0_2", "frags/s2:B:sum", 1.0),
        ("track_0_3", "frags/s2:T:sum", 3.0),
    ]


def test_the_resources_hold_each_read_resource_once(
    repo: GenomicResourceRepo,
) -> None:
    # Both entries read frags/s1 and the metadata meta/cells.
    entry = {"resource_query": "frags/s1", "group": BY_CLASS_GROUP,
             "meta": BY_SAMPLE_META}
    pipeline = tracks_pipeline(repo, entry, entry | {"name": "again"})

    assert [resource.get_id() for annotator in pipeline.annotators
            for resource in annotator.resources] == [
        "frags/s1", "meta/cells"]
    assert pipeline.get_resource_ids() == {"frags/s1", "meta/cells"}


def test_an_annotatable_logs_no_line_about_dropped_fragments(
    repo: GenomicResourceRepo, caplog: pytest.LogCaptureFixture,
) -> None:
    # frags/s1 drops the fragments of CCC and DDD over CHR1.
    pipeline = tracks_pipeline(repo, {"resource_query": "frags/s1"})

    with caplog.at_level(logging.INFO, logger="gain.binning"), \
            pipeline.open() as opened:
        opened.annotate(CHR1)

    assert [r.getMessage() for r in caplog.records
            if "dropped" in r.getMessage()] == []


def many_cells_repo(
    grr_dir: pathlib.Path, cells: int,
) -> GenomicResourceRepo:
    """A GRR of ``frags/many``: one fragment for each of ``cells`` barcodes.

    The ``cell`` score declares a categorical histogram, which keeps
    every value.  The default categorical histogram keeps only 100
    values, and its refusal comes before the refusal of the annotator.
    """
    rows = "\n".join(
        f"chr1  {start}  {start}  C{start:05d}"
        for start in range(1, cells + 1))
    (
        a_grr()
        .with_resource("frags/many", a_fragment_score()
                       .with_score("cell", "str")
                       .with_histogram({"type": "categorical"})
                       .with_data(
                           f"chrom  pos_begin  pos_end  cell\n{rows}"))
        .build_repo(grr_dir)
    )
    cli_manage(["repo-stats", "-R", str(grr_dir), "-j", "1"])
    return build_filesystem_test_repository(grr_dir)


def test_more_than_2000_tracks_are_refused_naming_the_number(
    tmp_path: pathlib.Path,
) -> None:
    repo = many_cells_repo(tmp_path / "grr", 2001)

    with pytest.raises(AnnotationConfigurationError) as excinfo:
        tracks_pipeline(repo, EACH_CELL)

    assert "2001" in str(excinfo.value)


def test_2000_tracks_are_accepted(tmp_path: pathlib.Path) -> None:
    repo = many_cells_repo(tmp_path / "grr", 2000)

    pipeline = tracks_pipeline(repo, EACH_CELL)

    assert len(pipeline.get_attributes()) == 2000


def test_the_limit_counts_the_tracks_of_all_entries_together(
    tmp_path: pathlib.Path,
) -> None:
    # Each entry gives 1001 tracks, under the limit, and the two give 2002.
    repo = many_cells_repo(tmp_path / "grr", 1001)

    with pytest.raises(AnnotationConfigurationError) as excinfo:
        tracks_pipeline(repo, EACH_CELL, EACH_CELL | {"name": "again"})

    assert "2002" in str(excinfo.value)
