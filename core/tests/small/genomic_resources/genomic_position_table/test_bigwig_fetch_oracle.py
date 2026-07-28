# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""``BigWigTable`` fetch results, checked against the file itself.

A property test rather than an example test: the records a region fetch
yields must equal what an unchunked ``pyBigWig.intervals()`` call over the
same range returns, for any track geometry and any query pattern.  The
expectation is never written down by hand -- it is computed from the raw
handle -- so this pins the fetch path's *output* without encoding anything
about how the fetch is chunked or strided.

That independence is the point.  The chunking is an implementation detail
that has changed more than once (a fixed 50 bp window, then the adaptive
record-count budget), and each change had to be argued as
records-preserving.  Here that argument is a test.
"""
from __future__ import annotations

# ruff: noqa: S311
# S311 (no `random` for cryptography) does not apply: `random` builds the
# track geometry and the query order here, seeded so a failure replays
# exactly.
import contextlib
import pathlib
import random
from collections.abc import Iterator
from typing import Any

import pytest
from gain.genomic_resources.genomic_position_table.record import Record
from gain.genomic_resources.genomic_position_table.table_bigwig import (
    BigWigTable,
)
from gain.genomic_resources.testing.builders import a_bigwig_score, a_grr

# Force one fetch strategy or the other.  ``buffered`` sets the threshold
# past any distance two queries can be apart, so the routing in
# ``get_records_in_region`` always picks the buffered walk; ``direct`` binds
# the buffered entry point to the direct one on the instance, so the direct
# code runs whatever the routing decides.  Both arms are checked against the
# same oracle, which is what makes them checked against each other.
STRATEGIES = ("direct", "buffered")


@contextlib.contextmanager
def _table(
    tmp_path: pathlib.Path,
    data: str,
    chrom_lens: dict[str, int],
    strategy: str,
) -> Iterator[tuple[BigWigTable, Any]]:
    builder = (
        a_bigwig_score()
        .with_score("bw", "float")
        .with_data(data)
        .with_chrom_lens(chrom_lens)
    )
    repo = a_grr().with_resource("bw", builder).build_repo(tmp_path)
    res = repo.get_resource("bw")
    definition = dict(res.get_config()["table"])
    if strategy == "buffered":
        definition["use_buffered_threshold"] = 10**18
    table = BigWigTable(res, definition).open()
    if strategy == "direct":
        table._fetch_buffered = table._fetch_direct  # type: ignore[method-assign]
    try:
        yield table, table._bw_file
    finally:
        table.close()


def _oracle(
    raw: Any, chrom: str, pos_begin: int, pos_end: int, chrom_len: int,
) -> list[Record]:
    """What the file says is in ``[pos_begin, pos_end]``, closed 1-based.

    One unchunked query, converted to the record contract's coordinates.
    ``pos_end`` is clamped to the contig, as the fetch path clamps it.
    """
    stop = min(pos_end, chrom_len)
    start = max(0, pos_begin - 1)
    if stop <= start:
        return []
    return [
        (chrom, begin + 1, end, None, None, value)
        for begin, end, value in (raw.intervals(chrom, start, stop) or [])
    ]


def _random_track(rng: random.Random) -> tuple[str, int]:
    """A random non-overlapping bedGraph, plus its contig length.

    Widths and gaps are drawn from scales that have each broken something
    before: single-base intervals, back-to-back runs (gap 0), and gaps far
    wider than any one fetch window.
    """
    widths = (1, 2, 5, 37, 500)
    gaps = (0, 0, 1, 13, 997, 60_013)
    rows, pos = [], rng.randrange(0, 500)
    for _ in range(rng.randrange(8, 40)):
        width = rng.choice(widths)
        rows.append((pos, pos + width))
        pos += width + rng.choice(gaps)
    data = "\n".join(
        f"chr1  {begin}  {end}  {(i % 89) / 100.0 + 0.01:.2f}"
        for i, (begin, end) in enumerate(rows)
    )
    return data, pos + rng.randrange(1, 5_000)


def _random_queries(
    rng: random.Random, chrom_len: int,
) -> list[tuple[int, int]]:
    """Points and spans, in an order chosen to defeat any cursor assumption."""
    queries = []
    for _ in range(rng.randrange(10, 30)):
        begin = rng.randrange(1, chrom_len + 1)
        if rng.random() < 0.5:
            queries.append((begin, begin))
        else:
            queries.append((begin, min(begin + rng.choice(
                (1, 7, 250, 9_000)), chrom_len)))
    order = rng.choice(("ascending", "descending", "shuffled"))
    if order == "ascending":
        queries.sort()
    elif order == "descending":
        queries.sort(reverse=True)
    else:
        rng.shuffle(queries)
    # A repeat, so a query served entirely from retained state is covered.
    if queries:
        queries.append(queries[rng.randrange(len(queries))])
    return queries


@pytest.mark.parametrize("strategy", STRATEGIES)
@pytest.mark.parametrize("seed", range(12))
def test_a_region_fetch_yields_exactly_what_the_file_holds(
    tmp_path: pathlib.Path, seed: int, strategy: str,
) -> None:
    rng = random.Random(seed)
    data, chrom_len = _random_track(rng)
    queries = _random_queries(rng, chrom_len)

    with _table(tmp_path, data, {"chr1": chrom_len}, strategy) as (table, raw):
        for pos_begin, pos_end in queries:
            found = list(
                table.get_records_in_region("chr1", pos_begin, pos_end))
            expected = _oracle(
                raw.raw if hasattr(raw, "raw") else raw,
                "chr1", pos_begin, pos_end, chrom_len)
            assert found == expected, (
                f"seed={seed} strategy={strategy} "
                f"query=({pos_begin}, {pos_end})")


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_a_whole_contig_scan_yields_every_interval_once(
    tmp_path: pathlib.Path, strategy: str,
) -> None:
    # The scan a statistics run makes, held to the same oracle: no interval
    # dropped at a chunk boundary, and none yielded twice by a window that
    # an interval straddled.
    rng = random.Random(101)
    data, chrom_len = _random_track(rng)

    with _table(tmp_path, data, {"chr1": chrom_len}, strategy) as (table, raw):
        found = list(table.get_records_in_region("chr1", 1, chrom_len))
        expected = _oracle(
            raw.raw if hasattr(raw, "raw") else raw,
            "chr1", 1, chrom_len, chrom_len)

    assert found == expected
    assert len(found) == len({(r[1], r[2]) for r in found})
