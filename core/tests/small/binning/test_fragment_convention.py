# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""``fragment_score_binner`` resolved through the GRR label convention.

The convention (F7): a fragment resource labelled with
``CELL_META_RESOURCE_ID_LABEL`` names its cell-metadata ``data_frame``,
whose ``SAMPLE_ID_COLUMN`` rows equal to its ``SAMPLE_ID_LABEL`` map the
``BARCODE_COLUMN`` of its ``CELL_SCORE`` to a ``CLASS_COLUMN``.  The toy
fragments and the cell table are the conftest's ``S1_FRAGMENTS``,
``S2_FRAGMENTS`` and ``CELL_META``; bins are 10 wide.  Over chr1:1-40,
``S1_FRAGMENTS`` read through S1's rows counts the fragments starting in
each bin as

==========  =====  =====
bin         B      T
==========  =====  =====
1-10        1      1
11-20       0      1
21-30       0      0
31-40       0      0
==========  =====  =====

(CCC's empty class and DDD, absent from the table, reach no track).
"""
import io
import logging
import pathlib
from typing import Any

import numpy as np
import pandas as pd
import pytest
from gain.binning.binners import BinningJob, discover_binner_kinds
from gain.binning.fragment_binner import (
    BARCODE_COLUMN,
    CELL_META_RESOURCE_ID_LABEL,
    CELL_SCORE,
    CLASS_COLUMN,
    COUNT_SCORE,
    SAMPLE_ID_COLUMN,
    SAMPLE_ID_LABEL,
)
from gain.binning.run_definition import (
    RunDefinition,
    RunDefinitionError,
    parse_run_definition,
)
from gain.genomic_resources.reference_genome import ReferenceGenome
from gain.genomic_resources.repository import GenomicResourceRepo
from gain.genomic_resources.testing.builders import (
    a_fragment_score,
    a_grr,
)
from gain.genomic_resources.testing.data_frame_builder import a_data_frame
from gain.utils.regions import BedRegion

from tests.small.binning.conftest import (
    CELL_META,
    S1_FRAGMENTS,
    S2_FRAGMENTS,
)

BIN_SIZE = 10
KIND = "fragment_score_binner"
CHR1 = BedRegion("chr1", 1, 40)
S1_COUNT_BY_CLASS = [[1.0, 1.0], [0.0, 1.0], [0.0, 0.0], [0.0, 0.0]]


def without_column(data: str, name: str) -> str:
    """A whitespace fragment block with the column ``name`` left out."""
    lines = [line.split() for line in data.strip().splitlines()]
    index = lines[0].index(name)
    return "\n".join(
        " ".join(f for i, f in enumerate(line) if i != index)
        for line in lines)


def fragments(
    data: str = S1_FRAGMENTS, *, count_type: str | None = "int",
    cell: bool = True, **labels: Any,
) -> Any:
    """A fragment resource over ``data`` with the given scores and labels.

    ``count_type`` ``None`` leaves the ``count`` score out, and ``cell``
    false the cell score.
    """
    builder = a_fragment_score()
    if cell:
        builder = builder.with_score(CELL_SCORE, "str")
    else:
        data = without_column(data, CELL_SCORE)
    if count_type is not None:
        builder = builder.with_score(COUNT_SCORE, count_type)
    else:
        data = without_column(data, COUNT_SCORE)
    return builder.with_labels(**labels).with_tabix().with_data(data)


def labelled(sample: str, meta: str = "meta/cells", **labels: Any) -> Any:
    """A fragment resource of ``sample`` following the convention."""
    return fragments(
        S1_FRAGMENTS if sample == "S1" else S2_FRAGMENTS,
        **{SAMPLE_ID_LABEL: sample, CELL_META_RESOURCE_ID_LABEL: meta},
        **labels)


def build_repo(
    tmp_path: pathlib.Path, *resources: tuple[str, Any],
) -> GenomicResourceRepo:
    """A GRR of ``meta/cells`` and the ``(resource id, builder)`` pairs."""
    grr = a_grr().with_resource(
        "meta/cells", a_data_frame().with_raw_content(CELL_META))
    for resource_id, builder in resources:
        grr = grr.with_resource(resource_id, builder)
    return grr.build_repo(tmp_path / "conv")


def parse(
    entry: dict[str, Any], repo: GenomicResourceRepo,
    genome: ReferenceGenome, **kwargs: Any,
) -> RunDefinition:
    return parse_run_definition({
        "bins": {"bin_size": BIN_SIZE},
        "binners": [{KIND: entry}],
    }, repo, genome, **kwargs)


def bin_job(
    job: BinningJob, region: BedRegion, repo: GenomicResourceRepo,
) -> np.ndarray:
    with discover_binner_kinds()[job.binner].bind(job, repo) as bound:
        return bound.bin_region(region, BIN_SIZE)


def test_a_labelled_resource_alone_counts_fragments_by_class(
    tmp_path: pathlib.Path, genome: ReferenceGenome,
) -> None:
    repo = build_repo(tmp_path, ("frags/s1", labelled("S1")))

    run = parse({"resource_query": "frags/s1"}, repo, genome)

    assert [
        (t.name, t.group, t.score_id, t.aggregator) for t in run.tracks
    ] == [
        ("frags/s1:B", "B", "", "sum"),
        ("frags/s1:T", "T", "", "sum"),
    ]
    (job,) = run.jobs
    np.testing.assert_array_equal(
        bin_job(job, CHR1, repo), S1_COUNT_BY_CLASS)


# Read pairs per bin of S1_FRAGMENTS over chr1:1-40, and fragments.
S1_SUM_OF_COUNT = [7.0, 4.0, 0.0, 4.0]
S1_FRAGMENT_COUNT = [2.0, 2.0, 0.0, 1.0]


@pytest.mark.parametrize("count_type,cell", [
    ("int", True), ("int", False), ("float", True), (None, True),
])
def test_an_unlabelled_resource_is_one_all_track_counting_fragments(
    tmp_path: pathlib.Path, genome: ReferenceGenome,
    count_type: str | None, cell: bool,
) -> None:
    # Whatever its count score, the default counts fragments; S1's counts
    # are not all 1, so a sum of them would differ.  The sample label
    # alone does not group.
    repo = build_repo(tmp_path, ("frags/s1", fragments(
        count_type=count_type, cell=cell, **{SAMPLE_ID_LABEL: "S1"})))

    run = parse({"resource_query": "frags/s1"}, repo, genome)

    assert [(t.name, t.group, t.score_id, t.aggregator)
            for t in run.tracks] == [
        ("frags/s1:all", "all", "", "sum")]
    (job,) = run.jobs
    np.testing.assert_array_equal(
        bin_job(job, CHR1, repo)[:, 0], S1_FRAGMENT_COUNT)


#: CELL_META with a second grouping column; in S1 AAA is T1, BBB is B1.
SUB_META = """sample_id,barcode,class,subclass
S1,AAA,T,T1
S1,BBB,B,B1
S1,CCC,,
S2,AAA,B,B2
S2,EEE,T,T2
"""


def test_a_group_naming_one_column_keeps_the_other_defaults(
    tmp_path: pathlib.Path, genome: ReferenceGenome,
) -> None:
    repo = build_repo(
        tmp_path, ("meta/sub", a_data_frame().with_raw_content(SUB_META)),
        ("frags/s1", labelled("S1", meta="meta/sub")))

    run = parse({
        "resource_query": "frags/s1",
        "group": {"group_meta_column": "subclass"},
    }, repo, genome)

    assert [t.name for t in run.tracks] == ["frags/s1:B1", "frags/s1:T1"]
    (job,) = run.jobs
    np.testing.assert_array_equal(
        bin_job(job, CHR1, repo), S1_COUNT_BY_CLASS)


def test_an_empty_group_is_the_omitted_group_on_a_labelled_resource(
    tmp_path: pathlib.Path, genome: ReferenceGenome,
) -> None:
    repo = build_repo(tmp_path, ("frags/s1", labelled("S1")))
    omitted = parse({"resource_query": "frags/s1"}, repo, genome)

    empty = parse({"resource_query": "frags/s1", "group": {}}, repo, genome)

    assert empty.tracks == omitted.tracks
    np.testing.assert_array_equal(
        bin_job(empty.jobs[0], CHR1, repo),
        bin_job(omitted.jobs[0], CHR1, repo))


def test_a_constant_group_stays_constant_on_a_labelled_resource(
    tmp_path: pathlib.Path, genome: ReferenceGenome,
) -> None:
    repo = build_repo(tmp_path, ("frags/s1", labelled("S1")))

    run = parse({"resource_query": "frags/s1", "group": {"group": "bulk"}},
                repo, genome)

    assert [(t.name, t.score_id) for t in run.tracks] == [
        ("frags/s1:bulk", "")]


def test_meta_by_resource_label_resolves_the_table_the_label_names(
    tmp_path: pathlib.Path, genome: ReferenceGenome,
) -> None:
    # The convention label names a table without the S1 rows; the entry
    # reads the one its own label names instead.
    repo = build_repo(
        tmp_path, ("meta/sub", a_data_frame().with_raw_content(SUB_META)),
        ("meta/empty", a_data_frame().with_raw_content(
            "sample_id,barcode,class\nS9,ZZZ,Z\n")),
        ("frags/s1", labelled("S1", meta="meta/empty", atlas="meta/sub")))

    run = parse({
        "resource_query": "frags/s1",
        "meta": {"resource_label": "atlas", "filter": [
            {"column": SAMPLE_ID_COLUMN, "label": SAMPLE_ID_LABEL}]},
    }, repo, genome)

    assert [t.name for t in run.tracks] == ["frags/s1:B", "frags/s1:T"]
    np.testing.assert_array_equal(
        bin_job(run.jobs[0], CHR1, repo), S1_COUNT_BY_CLASS)


def test_an_explicit_meta_without_a_filter_selects_every_row(
    tmp_path: pathlib.Path, genome: ReferenceGenome,
) -> None:
    # Not the convention's sample filter: the S2-only row of EEE gives T
    # to a barcode frags/s1 never carries, and AAA's S1 row gives T.
    repo = build_repo(
        tmp_path, ("meta/flat", a_data_frame().with_raw_content(
            "barcode,class\nAAA,T\nBBB,B\nEEE,X\n")),
        ("frags/s1", labelled("S1")))

    run = parse({"resource_query": "frags/s1",
                 "meta": {"resource_id": "meta/flat"}}, repo, genome)

    assert [t.group for t in run.tracks] == ["B", "T", "X"]


def test_an_unpooled_entry_resolves_each_resource_on_its_own(
    tmp_path: pathlib.Path, genome: ReferenceGenome,
) -> None:
    # Different tiers, tables and count scores: each its own job.
    repo = build_repo(
        tmp_path, ("meta/sub", a_data_frame().with_raw_content(SUB_META)),
        ("frags/a", labelled("S1")),
        ("frags/b", labelled("S2", meta="meta/sub")),
        ("frags/c", fragments(count_type="float")))

    run = parse({"resource_query": "frags/*", "pool": False}, repo, genome)

    assert [(t.name, t.score_id) for t in run.tracks] == [
        ("frags/a:B", ""), ("frags/a:T", ""),
        ("frags/b:B", ""), ("frags/b:T", ""),
        ("frags/c:all", ""),
    ]


def refusal(
    entry: dict[str, Any], repo: GenomicResourceRepo,
    genome: ReferenceGenome,
) -> str:
    with pytest.raises(RunDefinitionError) as excinfo:
        parse(entry, repo, genome)
    message = str(excinfo.value)
    assert message.startswith("binners[0]")
    return message


@pytest.mark.parametrize("table,fragments_", [
    # The label names a table the repository lacks.
    (None, ["'frags/s1'", "'meta/none'", "does not have"]),
    # ... or a resource that is not a data_frame.
    ("frags/other", ["'frags/s1'", "'frags/other'", "not a data_frame"]),
])
def test_a_label_naming_no_data_frame_is_refused_naming_both(
    tmp_path: pathlib.Path, genome: ReferenceGenome,
    table: str | None, fragments_: list[str],
) -> None:
    repo = build_repo(
        tmp_path, ("frags/other", fragments()),
        ("frags/s1", labelled("S1", meta=table or "meta/none")))

    message = refusal({"resource_query": "frags/s1"}, repo, genome)

    for fragment in fragments_:
        assert fragment in message


@pytest.mark.parametrize("column", [
    SAMPLE_ID_COLUMN, BARCODE_COLUMN, CLASS_COLUMN])
def test_a_table_missing_a_convention_column_is_refused_naming_it(
    tmp_path: pathlib.Path, genome: ReferenceGenome, column: str,
) -> None:
    header, *rows = CELL_META.splitlines()
    index = header.split(",").index(column)
    without = "\n".join(
        ",".join(f for i, f in enumerate(line.split(",")) if i != index)
        for line in [header, *rows])
    repo = build_repo(
        tmp_path, ("meta/thin", a_data_frame().with_raw_content(without)),
        ("frags/s1", labelled("S1", meta="meta/thin")))

    message = refusal({"resource_query": "frags/s1"}, repo, genome)

    assert "'frags/s1'" in message
    assert "'meta/thin'" in message
    assert f"no column {column!r}" in message


def test_a_column_the_entry_names_missing_from_the_table_is_refused(
    tmp_path: pathlib.Path, genome: ReferenceGenome,
) -> None:
    repo = build_repo(tmp_path, ("frags/s1", labelled("S1")))

    message = refusal({"resource_query": "frags/s1",
                       "group": {"group_meta_column": "subclass"}},
                      repo, genome)

    assert "'frags/s1'" in message
    assert "'meta/cells'" in message
    assert "no column 'subclass'" in message


def test_a_barcode_twice_in_a_resource_s_rows_is_refused(
    tmp_path: pathlib.Path, genome: ReferenceGenome,
) -> None:
    # The second AAA row of S1 would otherwise silently win.  S2's AAA is
    # another sample's cell, which the filter keeps apart.
    repo = build_repo(
        tmp_path, ("meta/twice", a_data_frame().with_raw_content(
            CELL_META + "S1,AAA,B\n")),
        ("frags/s1", labelled("S1", meta="meta/twice")))

    message = refusal({"resource_query": "frags/s1"}, repo, genome)

    assert "'frags/s1'" in message
    assert "'meta/twice'" in message
    assert "'AAA'" in message
    assert "'BBB'" not in message


@pytest.mark.parametrize("labels,fragments_", [
    ({}, ["'frags/s1'", f"no label {SAMPLE_ID_LABEL!r}"]),
    ({SAMPLE_ID_LABEL: ["S1", "S2"]},
     ["'frags/s1'", f"label {SAMPLE_ID_LABEL!r} is a list"]),
])
def test_a_filter_label_absent_or_a_list_is_refused_naming_the_resource(
    tmp_path: pathlib.Path, genome: ReferenceGenome,
    labels: dict[str, Any], fragments_: list[str],
) -> None:
    repo = build_repo(tmp_path, ("frags/s1", fragments(
        **{CELL_META_RESOURCE_ID_LABEL: "meta/cells"}, **labels)))

    message = refusal({"resource_query": "frags/s1"}, repo, genome)

    for fragment in fragments_:
        assert fragment in message


def test_a_pool_across_label_tiers_is_refused_naming_each_side(
    tmp_path: pathlib.Path, genome: ReferenceGenome,
) -> None:
    repo = build_repo(
        tmp_path, ("frags/a", labelled("S1")), ("frags/b", labelled("S2")),
        ("frags/c", fragments(**{SAMPLE_ID_LABEL: "S1"})))

    message = refusal({"resource_query": "frags/*"}, repo, genome)

    assert "'frags/a', 'frags/b' carry it" in message
    assert "'frags/c' do not" in message


def test_a_pool_across_metadata_tables_is_refused_naming_each_table(
    tmp_path: pathlib.Path, genome: ReferenceGenome,
) -> None:
    repo = build_repo(
        tmp_path, ("meta/sub", a_data_frame().with_raw_content(SUB_META)),
        ("frags/a", labelled("S1")), ("frags/b", labelled("S2")),
        ("frags/c", labelled("S1", meta="meta/sub")))

    message = refusal({"resource_query": "frags/*"}, repo, genome)

    assert "'meta/cells' for 'frags/a', 'frags/b'" in message
    assert "'meta/sub' for 'frags/c'" in message
    assert "pool: false" in message
    assert "resource_id" in message


def test_a_pool_sharing_an_explicit_table_reads_it_once(
    tmp_path: pathlib.Path, genome: ReferenceGenome,
) -> None:
    # The resolution the table refusal offers: one shared table.
    repo = build_repo(
        tmp_path, ("meta/sub", a_data_frame().with_raw_content(SUB_META)),
        ("frags/a", labelled("S1")),
        ("frags/c", labelled("S2", meta="meta/sub")))

    run = parse({"resource_query": "frags/*", "meta": {
        "resource_id": "meta/cells", "filter": [
            {"column": SAMPLE_ID_COLUMN, "label": SAMPLE_ID_LABEL}]},
    }, repo, genome)

    assert [t.name for t in run.tracks] == ["frags/*:B", "frags/*:T"]


def test_a_pool_with_and_without_an_int_count_score_counts_fragments(
    tmp_path: pathlib.Path, genome: ReferenceGenome,
) -> None:
    # The default is the same for every resource, so they cannot
    # disagree on it: three copies of S1's fragments count three times.
    repo = build_repo(
        tmp_path, ("frags/a", fragments()),
        ("frags/b", fragments(count_type="float")),
        ("frags/c", fragments(count_type=None)))

    run = parse({"resource_query": "frags/*"}, repo, genome)

    assert [(t.name, t.score_id, t.aggregator) for t in run.tracks] == [
        ("frags/*:all", "", "sum")]
    (job,) = run.jobs
    np.testing.assert_array_equal(
        bin_job(job, CHR1, repo)[:, 0],
        [3 * count for count in S1_FRAGMENT_COUNT])


def test_dropped_fragments_are_counted_and_logged_per_resource_and_region(
    tmp_path: pathlib.Path, genome: ReferenceGenome,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # On chr1, CCC's row has an empty class and DDD has no row: one
    # fragment each.  chr2's one fragment is BBB's, of class B.
    repo = build_repo(tmp_path, ("frags/s1", labelled("S1")))
    (job,) = parse({"resource_query": "frags/s1"}, repo, genome).jobs
    chr2 = BedRegion("chr2", 1, 40)

    with caplog.at_level(logging.INFO, logger="gain.binning"), \
            discover_binner_kinds()[job.binner].bind(job, repo) as bound:
        bound.bin_region(CHR1, BIN_SIZE)
        bound.bin_region(chr2, BIN_SIZE)

    assert bound.dropped == {
        ("frags/s1", "chr1:1-40"): 2, ("frags/s1", "chr2:1-40"): 0}
    logged = [r.getMessage() for r in caplog.records
              if "dropped" in r.getMessage()]
    assert len(logged) == 2
    assert "frags/s1" in logged[0]
    assert "chr1:1-40" in logged[0]
    assert "2 fragments" in logged[0]


def test_a_fully_annotated_resource_drops_no_fragment(
    tmp_path: pathlib.Path, genome: ReferenceGenome,
) -> None:
    repo = build_repo(
        tmp_path, ("meta/full", a_data_frame().with_raw_content(
            "sample_id,barcode,class\n"
            "S1,AAA,T\nS1,BBB,B\nS1,CCC,T\nS1,DDD,B\n")),
        ("frags/s1", labelled("S1", meta="meta/full")))
    (job,) = parse({"resource_query": "frags/s1"}, repo, genome).jobs

    with discover_binner_kinds()[job.binner].bind(job, repo) as bound:
        bound.bin_region(CHR1, BIN_SIZE)

    assert bound.dropped == {("frags/s1", "chr1:1-40"): 0}


BY_SAMPLE = [{"column": SAMPLE_ID_COLUMN, "label": SAMPLE_ID_LABEL}]


def write_cell_meta(directory: pathlib.Path, file_format: str) -> str:
    """CELL_META as a local file of ``file_format``; its name."""
    directory.mkdir(parents=True, exist_ok=True)
    if file_format == "excel":
        frame = pd.read_csv(io.StringIO(CELL_META))
        frame.to_excel(directory / "cells.xlsx", index=False)
        return "cells.xlsx"
    if file_format == "tsv":
        (directory / "cells.tsv").write_text(CELL_META.replace(",", "\t"))
        return "cells.tsv"
    (directory / "cells.csv").write_text(CELL_META)
    return "cells.csv"


@pytest.mark.parametrize("file_format", ["csv", "tsv", "excel"])
def test_a_local_table_bins_as_the_same_data_frame_resource(
    tmp_path: pathlib.Path, genome: ReferenceGenome, file_format: str,
) -> None:
    repo = build_repo(tmp_path, ("frags/s1", fragments(
        **{SAMPLE_ID_LABEL: "S1"})))
    resource = parse({"resource_query": "frags/s1", "group": {}, "meta": {
        "resource_id": "meta/cells", "filter": BY_SAMPLE}}, repo, genome)
    file_name = write_cell_meta(tmp_path / "local", file_format)

    local = parse({"resource_query": "frags/s1", "group": {}, "meta": {
        "file_name": file_name, "file_format": file_format,
        "filter": BY_SAMPLE}}, repo, genome,
        base_dir=str(tmp_path / "local"))

    assert [(t.name, t.score_id) for t in local.tracks] == [
        (t.name, t.score_id) for t in resource.tracks]
    np.testing.assert_array_equal(
        bin_job(local.jobs[0], CHR1, repo),
        bin_job(resource.jobs[0], CHR1, repo))
    assert local.jobs[0].local_files == (
        str(tmp_path / "local" / file_name),)


def test_a_local_table_s_separator_can_be_given(
    tmp_path: pathlib.Path, genome: ReferenceGenome,
) -> None:
    repo = build_repo(tmp_path, ("frags/s1", fragments(
        **{SAMPLE_ID_LABEL: "S1"})))
    (tmp_path / "cells.txt").write_text(CELL_META.replace(",", ";"))

    run = parse({"resource_query": "frags/s1", "group": {}, "meta": {
        "file_name": "cells.txt", "file_separator": ";",
        "filter": BY_SAMPLE}}, repo, genome, base_dir=str(tmp_path))

    assert [t.group for t in run.tracks] == ["B", "T"]


def test_an_unreadable_local_table_is_refused_naming_the_file(
    tmp_path: pathlib.Path, genome: ReferenceGenome,
) -> None:
    repo = build_repo(tmp_path, ("frags/s1", fragments(
        **{SAMPLE_ID_LABEL: "S1"})))

    with pytest.raises(RunDefinitionError) as excinfo:
        parse({"resource_query": "frags/s1", "group": {}, "meta": {
            "file_name": "absent.csv", "filter": BY_SAMPLE}},
            repo, genome, base_dir=str(tmp_path))

    message = str(excinfo.value)
    assert repr(str(tmp_path / "absent.csv")) in message
    assert "'frags/s1'" in message


def test_a_legacy_xls_table_is_refused_naming_the_file(
    tmp_path: pathlib.Path, genome: ReferenceGenome,
) -> None:
    # A legacy (OLE2) .xls needs a reader gain does not depend on.
    repo = build_repo(tmp_path, ("frags/s1", fragments(
        **{SAMPLE_ID_LABEL: "S1"})))
    legacy = tmp_path / "cells.xls"
    legacy.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 504)

    with pytest.raises(RunDefinitionError) as excinfo:
        parse({"resource_query": "frags/s1", "group": {}, "meta": {
            "file_name": "cells.xls", "filter": BY_SAMPLE}},
            repo, genome, base_dir=str(tmp_path))

    message = str(excinfo.value)
    assert repr(str(legacy)) in message
    assert "'frags/s1'" in message


def test_a_file_format_that_is_not_text_is_refused_naming_the_entry(
    tmp_path: pathlib.Path, genome: ReferenceGenome,
) -> None:
    repo = build_repo(tmp_path, ("frags/s1", fragments(
        **{SAMPLE_ID_LABEL: "S1"})))
    write_cell_meta(tmp_path, "csv")

    with pytest.raises(RunDefinitionError) as excinfo:
        parse({"resource_query": "frags/s1", "group": {}, "meta": {
            "file_name": "cells.csv", "file_format": ["csv"],
            "filter": BY_SAMPLE}}, repo, genome, base_dir=str(tmp_path))

    message = str(excinfo.value)
    assert message.startswith("binners[0]")
    assert "file_format" in message
