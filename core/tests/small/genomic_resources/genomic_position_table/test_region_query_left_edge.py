"""A forward region query that starts in a gap reads the buffer (gain#340).

The tabix backend serves a forward query from its warm :class:`LineBuffer`.
The buffer holds every record that overlaps any position at or after the start
of the previous query, so a later query on the same contig can be answered
from it as long as it starts no further right than the buffer's right edge.

The left edge of the buffer does not matter.  A region query that starts in a
gap, before the first buffered record, and ends inside the buffered window
used to fall through to a fresh tabix fetch: correct answers, wasted reads.
The tests below count those fetches through the table's ``stats``.
"""
# pylint: disable=W0621,C0116
# ruff: file-ignore[suspicious-non-cryptographic-random-usage]
# S311 (no `random` for cryptography) does not apply: `random` builds fixture
# rows here, seeded so a failure replays exactly.
import pathlib
import random
from typing import Any

import pytest
from gain.genomic_resources.genomic_position_table import (
    build_genomic_position_table,
)
from gain.genomic_resources.genomic_position_table.record import (
    PAYLOAD,
    POS_BEGIN,
    POS_END,
)
from gain.genomic_resources.genomic_position_table.table import (
    GenomicPositionTable,
)
from gain.genomic_resources.repository import GenomicResource
from gain.genomic_resources.testing.builders import (
    a_grr,
    a_position_score,
)

CHROM = "chr1"

Interval = tuple[int, int]
TablePair = tuple[GenomicPositionTable, GenomicPositionTable]


def _content(rows: list[Interval]) -> str:
    lines = ["chrom pos_begin pos_end c2"]
    lines.extend(
        f"{CHROM} {beg} {end} {serial}"
        for serial, (beg, end) in enumerate(rows)
    )
    return "\n".join(lines)


def _open_table(resource: GenomicResource) -> GenomicPositionTable:
    assert resource.config is not None
    table = build_genomic_position_table(resource, resource.config["table"])
    table.open()
    return table


@pytest.fixture
def build_tables(tmp_path: pathlib.Path) -> Any:
    """Return a builder of a (tabix, in-memory) pair over identical rows."""
    def builder(rows: list[Interval]) -> TablePair:
        base = (
            a_position_score()
            .with_score("c2", "int")
            .with_data(_content(rows))
        )
        repo = (
            a_grr()
            .with_resource("tabix", base.with_tabix())
            .with_resource("mem", base)
            .build_repo(tmp_path)
        )
        return (
            _open_table(repo.get_resource("tabix")),
            _open_table(repo.get_resource("mem")),
        )
    return builder


def _intervals(records: Any) -> list[Interval]:
    return [(record[POS_BEGIN], record[POS_END]) for record in records]


def test_a_query_that_starts_in_a_gap_reads_the_buffer(
    build_tables: Any,
) -> None:
    tabix_table, _ = build_tables([(100, 200), (300, 400)])
    list(tabix_table.get_records_in_region(CHROM, 210, 250))

    answer = _intervals(tabix_table.get_records_in_region(CHROM, 260, 310))

    assert answer == [(300, 400)]
    assert tabix_table.stats["tabix fetch"] == 1


def test_a_backward_query_fetches_again(build_tables: Any) -> None:
    tabix_table, _ = build_tables([(100, 200), (300, 400)])
    list(tabix_table.get_records_in_region(CHROM, 260, 310))

    answer = _intervals(tabix_table.get_records_in_region(CHROM, 150, 260))

    assert answer == [(100, 200)]
    assert tabix_table.stats["tabix fetch"] == 2


SHAPES = ["overlapping", "points", "disjoint"]


def _make_rows(shape: str, seed: int) -> list[Interval]:
    """Build 1500 sorted rows of the named shape.

    ``overlapping`` imitates a fragments table: the widths have a heavy tail
    capped at 2 kb, and about three records in four overlap a predecessor.
    The other records begin in a gap, which is where a region query can
    start before the first buffered record.

    In every shape, the begins of two neighbouring records are at most 200 bp
    apart, so no gap comes near ``jump_threshold``.
    """
    rnd = random.Random(seed)
    rows: list[Interval] = []
    pos = 1_000
    for _ in range(1_500):
        if shape == "overlapping":
            width = min(int(rnd.paretovariate(1.5) * 60), 2_000)
            rows.append((pos, pos + width))
            pos += rnd.randint(1, 200)
        elif shape == "points":
            rows.append((pos, pos))
            pos += rnd.randint(1, 30)
        elif shape == "disjoint":
            end = pos + rnd.randint(0, 20)
            rows.append((pos, end))
            pos = end + rnd.randint(1, 60)
        else:
            raise ValueError(f"unknown shape {shape}")
    return rows


def _answer(records: Any) -> list[tuple[str, ...]]:
    return sorted(tuple(record[PAYLOAD]) for record in records)


def _scan_and_compare(
    tabix_table: GenomicPositionTable,
    mem_table: GenomicPositionTable,
    rows: list[Interval],
    step: int,
) -> None:
    """Scan ``[p, p + step - 1]`` forward and compare with the oracle.

    The scan starts before the first record and stops at the begin of the
    last record, so no query starts past the last record of the contig.
    """
    first_begin = rows[0][0]
    last_begin = rows[-1][0]
    for pos in range(first_begin - step, last_begin + 1, step):
        end = pos + step - 1
        tabix_answer = _answer(
            tabix_table.get_records_in_region(CHROM, pos, end))
        mem_answer = _answer(mem_table.get_records_in_region(CHROM, pos, end))
        assert tabix_answer == mem_answer, \
            f"region ({pos}, {end}): tabix != in-memory"


@pytest.mark.parametrize("step", [10, 50, 100])
@pytest.mark.parametrize("seed", [1, 2, 3])
@pytest.mark.parametrize("shape", SHAPES)
def test_a_forward_region_scan_matches_inmemory_and_fetches_once(
    build_tables: Any, shape: str, step: int, seed: int,
) -> None:
    rows = _make_rows(shape, seed)
    tabix_table, mem_table = build_tables(rows)

    _scan_and_compare(tabix_table, mem_table, rows, step)

    assert tabix_table.stats["tabix fetch"] == 1
