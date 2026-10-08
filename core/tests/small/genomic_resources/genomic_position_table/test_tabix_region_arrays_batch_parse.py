# pylint: disable=C0116,W0621
"""The tabix bulk read parses each batch of raw rows as one CSV block.

These tests write the tab-separated text byte for byte, so that a table can
carry empty cells, ragged rows and cells that start with ``#`` or ``"`` --
shapes the whitespace-separated test builders cannot express.
"""
import pathlib
import textwrap
from collections.abc import Callable, Generator

import numpy as np
import pysam
import pytest
from gain.genomic_resources.genomic_position_table import (
    TabixGenomicPositionTable,
    build_genomic_position_table,
)
from gain.genomic_resources.genomic_position_table.record import (
    PAYLOAD,
    POS_BEGIN,
    POS_END,
)
from gain.genomic_resources.testing import (
    build_filesystem_test_resource,
    setup_directories,
)


def _tabix_table(
    tmp_path: pathlib.Path, lines: list[str], *, zero_based: bool = False,
) -> Generator[TabixGenomicPositionTable, None, None]:
    """Open a tabix table whose rows are ``lines``, written verbatim."""
    zero_based_line = f"    zero_based: {str(zero_based).lower()}\n"
    setup_directories(tmp_path, {
        "genomic_resource.yaml": textwrap.dedent("""\
            table:
                format: tabix
                filename: data.txt.gz
            """) + zero_based_line,
    })
    raw = tmp_path / "data.txt"
    raw.write_text("".join(f"{line}\n" for line in lines), encoding="utf8")
    gz_name = str(tmp_path / "data.txt.gz")
    # pylint: disable=no-member
    pysam.tabix_compress(str(raw), gz_name)
    pysam.tabix_index(
        gz_name, seq_col=0, start_col=1, end_col=2,
        zerobased=zero_based)
    raw.unlink()
    res = build_filesystem_test_resource(tmp_path)
    assert res.config is not None
    with build_genomic_position_table(res, res.config["table"]) as table:
        assert isinstance(table, TabixGenomicPositionTable)
        yield table


MakeTable = Callable[..., TabixGenomicPositionTable]


@pytest.fixture
def make_table(
    tmp_path: pathlib.Path,
) -> Generator[MakeTable, None, None]:
    gens: list[Generator[TabixGenomicPositionTable, None, None]] = []

    def make(
        lines: list[str], *, zero_based: bool = False,
    ) -> TabixGenomicPositionTable:
        gen = _tabix_table(tmp_path, lines, zero_based=zero_based)
        gens.append(gen)
        return next(gen)

    yield make
    for gen in gens:
        gen.close()


HEADER = "#chrom\tpos_begin\tpos_end\ta\tb\tc"


def test_a_shorter_row_in_a_batch_is_an_error(
    make_table: MakeTable,
) -> None:
    table = make_table([
        HEADER,
        "chr1\t1\t1\ta1\tb1\tc1",
        "chr1\t2\t2\ta2\tb2",
        "chr1\t3\t3\ta3\tb3\tc3",
    ])

    with pytest.raises(ValueError, match=r"chr1.*1.*10"):
        list(table.get_region_value_arrays("chr1", 1, 10, [3], 100))


def test_a_longer_row_in_a_batch_is_an_error(
    make_table: MakeTable,
) -> None:
    table = make_table([
        HEADER,
        "chr1\t1\t1\ta1\tb1\tc1",
        "chr1\t2\t2\ta2\tb2\tc2\textra",
        "chr1\t3\t3\ta3\tb3\tc3",
    ])

    with pytest.raises(ValueError, match=r"chr1.*1.*10"):
        list(table.get_region_value_arrays("chr1", 1, 10, [3], 100))


def _concat(batches: list, col: int) -> list[str]:
    return list(np.concatenate([b[2][col] for b in batches]))


def test_a_subset_of_many_columns_out_of_file_order(
    make_table: MakeTable,
) -> None:
    names = "\t".join(f"v{i}" for i in range(3, 12))
    table = make_table([
        f"#chrom\tpos_begin\tpos_end\t{names}",
        "\t".join(["chr1", "1", "1"] + [f"r1c{i}" for i in range(3, 12)]),
        "\t".join(["chr1", "2", "2"] + [f"r2c{i}" for i in range(3, 12)]),
    ])

    batches = list(table.get_region_value_arrays("chr1", 1, 10, [9, 4, 6], 100))

    assert len(batches) == 1
    _pos_begin, _pos_end, cols = batches[0]
    assert set(cols) == {9, 4, 6}
    assert list(cols[9]) == ["r1c9", "r2c9"]
    assert list(cols[4]) == ["r1c4", "r2c4"]
    assert list(cols[6]) == ["r1c6", "r2c6"]


def test_value_columns_are_object_arrays_of_str(
    make_table: MakeTable,
) -> None:
    table = make_table([HEADER, "chr1\t1\t1\t0.5\t7\tx"])

    (pos_begin, pos_end, cols), = table.get_region_value_arrays(
        "chr1", 1, 10, [3, 4], 100)

    assert pos_begin.dtype == np.int64
    assert pos_end.dtype == np.int64
    assert cols[3].dtype == object
    assert cols[4].dtype == object
    assert [type(v) for v in (*cols[3], *cols[4])] == [str, str]


def test_empty_cells_stay_empty_strings(
    make_table: MakeTable,
) -> None:
    table = make_table([
        HEADER,
        "chr1\t1\t1\t\tb1\t",
        "chr1\t2\t2\ta2\t\tc2",
    ])

    batches = list(table.get_region_value_arrays(
        "chr1", 1, 10, [3, 4, 5], 100))

    assert _concat(batches, 3) == ["", "a2"]
    assert _concat(batches, 4) == ["b1", ""]
    assert _concat(batches, 5) == ["", "c2"]


def test_na_sentinels_come_back_as_raw_text(
    make_table: MakeTable,
) -> None:
    table = make_table([
        HEADER,
        "chr1\t1\t1\t.\tNA\tnan",
        "chr1\t2\t2\tNA\t.\tNULL",
    ])

    batches = list(table.get_region_value_arrays(
        "chr1", 1, 10, [3, 4, 5], 100))

    assert _concat(batches, 3) == [".", "NA"]
    assert _concat(batches, 4) == ["NA", "."]
    assert _concat(batches, 5) == ["nan", "NULL"]


def test_cells_starting_with_hash_or_quote_stay_raw(
    make_table: MakeTable,
) -> None:
    table = make_table([
        HEADER,
        'chr1\t1\t1\t#a\t"b\tc"',
        'chr1\t2\t2\t"a b"\t#\t"',
    ])

    batches = list(table.get_region_value_arrays(
        "chr1", 1, 10, [3, 4, 5], 100))

    assert _concat(batches, 3) == ["#a", '"a b"']
    assert _concat(batches, 4) == ['"b', "#"]
    assert _concat(batches, 5) == ['c"', '"']


def test_a_zero_based_single_base_row(
    make_table: MakeTable,
) -> None:
    table = make_table([
        HEADER,
        "chr1\t4\t4\tsingle\tb\tc",
        "chr1\t5\t8\tspan\tb\tc",
    ], zero_based=True)

    batches = list(table.get_region_value_arrays("chr1", 1, 20, [3], 100))
    records = list(table.get_records_in_region("chr1", 1, 20))

    (pos_begin, pos_end, cols), = batches
    assert list(zip(pos_begin, pos_end, strict=True)) == [(5, 5), (6, 8)]
    assert list(zip(pos_begin, pos_end, strict=True)) == [
        (rec[POS_BEGIN], rec[POS_END]) for rec in records]
    assert list(cols[3]) == ["single", "span"]


FIVE_ROWS = [
    HEADER,
    *(f"chr1\t{i}\t{i}\ta{i}\tb{i}\tc{i}" for i in range(1, 6)),
]


def test_query_end_cuts_the_rows_inside_a_batch(
    make_table: MakeTable,
) -> None:
    table = make_table(FIVE_ROWS)

    batches = list(table.get_region_value_arrays("chr1", 1, 2, [5], 4))

    assert [len(b[0]) for b in batches] == [2]
    assert list(batches[0][0]) == [1, 2]
    assert list(batches[0][1]) == [1, 2]
    assert list(batches[0][2][5]) == ["c1", "c2"]


def test_query_end_cuts_the_rows_at_a_batch_boundary(
    make_table: MakeTable,
) -> None:
    table = make_table(FIVE_ROWS)

    batches = list(table.get_region_value_arrays("chr1", 1, 4, [5], 2))

    # The second batch is full, so the read pulls a third one and stops on
    # its first row: that batch comes back empty.
    assert [len(b[0]) for b in batches] == [2, 2, 0]
    assert [len(b[2][5]) for b in batches] == [2, 2, 0]
    assert list(np.concatenate([b[0] for b in batches])) == [1, 2, 3, 4]
    assert _concat(batches, 5) == ["c1", "c2", "c3", "c4"]


CELLS = ["0.25", ".", "NA", "", "#x", '"q', "1e-3", " pad "]


@pytest.mark.parametrize("zero_based", [False, True])
@pytest.mark.parametrize(("start", "end"), [(1, 500), (37, 211)])
def test_arrays_match_the_per_record_read(
    make_table: MakeTable, zero_based: bool, start: int, end: int,
) -> None:
    lines = [HEADER]
    for i in range(60):
        pos = 1 + 4 * i
        span = i % 3
        cells = [CELLS[(i + k) % len(CELLS)] for k in range(3)]
        lines.append("\t".join(["chr1", str(pos), str(pos + span), *cells]))
    table = make_table(lines, zero_based=zero_based)
    columns = [5, 3]

    batches = list(table.get_region_value_arrays(
        "chr1", start, end, columns, 7))
    records = list(table.get_records_in_region("chr1", start, end))

    assert len(batches) > 2
    pos_begin = np.concatenate([b[0] for b in batches])
    pos_end = np.concatenate([b[1] for b in batches])
    # The bulk read leaves the clip of rows that end before ``start`` to its
    # caller; apply it here the way the per-record read does.
    kept = pos_end >= start
    assert list(zip(pos_begin[kept], pos_end[kept], strict=True)) == [
        (rec[POS_BEGIN], rec[POS_END]) for rec in records]
    for col in columns:
        column = np.concatenate([b[2][col] for b in batches])[kept]
        assert list(column) == [rec[PAYLOAD][col] for rec in records]


def test_a_negative_column_index_counts_from_the_last_field(
    make_table: MakeTable,
) -> None:
    table = make_table([
        HEADER,
        "chr1\t1\t1\ta1\tb1\tc1",
        "chr1\t2\t2\ta2\tb2\tc2",
    ])

    batches = list(table.get_region_value_arrays("chr1", 1, 10, [-1, 3], 100))
    records = list(table.get_records_in_region("chr1", 1, 10))

    assert set(batches[0][2]) == {-1, 3}
    assert _concat(batches, -1) == [rec[PAYLOAD][-1] for rec in records]
    assert _concat(batches, -1) == ["c1", "c2"]
    assert _concat(batches, 3) == ["a1", "a2"]


@pytest.mark.parametrize("zero_based", [False, True])
def test_a_position_column_requested_as_a_value_column_stays_raw_text(
    make_table: MakeTable, zero_based: bool,
) -> None:
    table = make_table([
        HEADER,
        "chr1\t10\t12\tx\ty\tz",
        "chr1\t11\t13\tx\ty\tz",
    ], zero_based=zero_based)

    batches = list(table.get_region_value_arrays("chr1", 1, 100, [1, 2, 0], 10))
    records = list(table.get_records_in_region("chr1", 1, 100))

    assert _concat(batches, 1) == ["10", "11"]
    assert _concat(batches, 2) == ["12", "13"]
    for col in (0, 1, 2):
        assert _concat(batches, col) == [rec[PAYLOAD][col] for rec in records]
    pos_begin = np.concatenate([b[0] for b in batches])
    pos_end = np.concatenate([b[1] for b in batches])
    assert pos_begin.dtype == np.int64
    assert pos_end.dtype == np.int64
    assert list(zip(pos_begin, pos_end, strict=True)) == [
        (rec[POS_BEGIN], rec[POS_END]) for rec in records]
