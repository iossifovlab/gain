# pylint: disable=W0621,C0114,C0116,W0212,W0613,too-many-lines
import contextlib
import pathlib
import textwrap
from collections.abc import Iterator
from typing import Any

import pytest
import pytest_mock
from gain.genomic_resources.genomic_position_table.record import (
    ALT,
    CHROM,
    PAYLOAD,
    POS_BEGIN,
    POS_END,
    RECORD_SLOTS,
    REF,
)
from gain.genomic_resources.genomic_position_table.table_bigwig import (
    DEFAULT_FETCH_TARGET_RECORDS,
    BigWigTable,
    build_bigwig_parser,
)
from gain.genomic_resources.genomic_position_table.utils import (
    build_genomic_position_table,
)
from gain.genomic_resources.genomic_scores import (
    PositionScore,
    RecordScoreLine,
)
from gain.genomic_resources.repository import GenomicResourceRepo
from gain.genomic_resources.testing import (
    build_filesystem_test_repository,
    setup_bigwig,
    setup_directories,
)
from gain.genomic_resources.testing.builders import (
    a_bigwig_score,
    a_grr,
)


@pytest.fixture(scope="module")
def test_grr(tmp_path_factory: pytest.TempPathFactory) -> GenomicResourceRepo:
    root_path = tmp_path_factory.mktemp("bigwig_testdir")
    setup_directories(
        root_path,
        {
            "grr.yaml": textwrap.dedent(f"""
                id: test_grr
                type: directory
                directory: {root_path!s}
            """),
            "test_score": {
                "genomic_resource.yaml": textwrap.dedent("""
                        type: position_score
                        table:
                            filename: data.bw
                        scores:
                        - id: score_one
                          type: float
                          index: 3
                """),
            },
        },
    )
    data = textwrap.dedent("""
        chr1   0          10       0.01
        chr1   10         20       0.02
        chr1   20         30       0.03
        chr2   30         40       0.04
        chr2   40         50       0.05
        chr2   50         70       0.06
        chr3   70         80       0.07
        chr3   80         90       0.08
        chr3   90         120      0.09
    """)
    setup_bigwig(
        root_path / "test_score" / "data.bw", data,
        {"chr1": 1000,
         "chr2": 2000,
         "chr3": 3000},
    )
    return build_filesystem_test_repository(root_path)


@pytest.fixture(scope="module")
def bigwig_table(test_grr: GenomicResourceRepo) -> BigWigTable:
    table = BigWigTable(
        test_grr.get_resource("test_score"),
        {"filename": "data.bw", "format": "bigWig"},
    )
    assert table is not None
    return table


def test_get_chromosomes(bigwig_table: BigWigTable) -> None:
    with bigwig_table:
        assert bigwig_table.get_chromosomes() == ["chr1", "chr2", "chr3"]


def test_get_chromosome_length(bigwig_table: BigWigTable) -> None:
    with bigwig_table:
        assert bigwig_table.get_chromosome_length("chr1") == 1000
        assert bigwig_table.get_chromosome_length("chr2") == 2000
        assert bigwig_table.get_chromosome_length("chr3") == 3000


def test_get_chromosome_length_missing(bigwig_table: BigWigTable) -> None:
    with bigwig_table, pytest.raises(
            ValueError,
            match="contig chrX not present in the table's contigs"):
        bigwig_table.get_chromosome_length("chrX")


def test_get_all_records(bigwig_table: BigWigTable) -> None:
    with bigwig_table:
        vs = list(bigwig_table.get_all_records())
        assert len(vs) == 9
        line = vs[0]
        assert line[CHROM] == "chr1"
        assert line[POS_BEGIN] == 1
        assert line[POS_END] == 10
        assert line[PAYLOAD][3] == pytest.approx(0.01)


def test_bigwig_yields_plain_records(bigwig_table: BigWigTable) -> None:
    # The bigWig backend is on the record contract: every line it yields is a
    # plain record tuple (exact type, not an adapter), whose PAYLOAD is the
    # four-element interval ``(chrom, pos_begin, pos_end, value)`` -- so the
    # value column stays addressable at index 3, the way it was through the
    # retired BigWigLine adapter.
    with bigwig_table:
        first = next(iter(bigwig_table.get_all_records()))

        assert type(first) is tuple
        assert len(first) == RECORD_SLOTS
        assert first[CHROM] == "chr1"
        assert first[POS_BEGIN] == 1
        assert first[POS_END] == 10
        assert first[REF] is None
        assert first[ALT] is None

        payload = first[PAYLOAD]
        assert payload == ("chr1", 1, 10, pytest.approx(0.01))
        assert payload[3] == pytest.approx(0.01)


def test_bigwig_parser_converts_zero_based_half_open_to_closed_one_based(
) -> None:
    # The subtle correctness point of the migration: a bigWig interval is
    # 0-based half-open in the file, and the record must carry it as the
    # contract's closed one-based interval -- byte-identical to what the
    # BigWigLine adapter produced.  The ``+1`` on the begin lives in the fetch
    # methods (left untouched), so by the time the parser sees an interval it
    # is already ``(pos_begin_1based, pos_end, value)``; the parser assembles
    # the record around it, and the PAYLOAD repeats the interval so the value
    # stays at index 3.  A file interval ``[0, 10)`` for chr1 reaches the
    # parser as ``(1, 10, 0.11)`` and must become this exact record.
    parser = build_bigwig_parser()
    assert parser("chr1", (1, 10, 0.11)) == (
        "chr1", 1, 10, None, None, ("chr1", 1, 10, 0.11))
    # A mapped/reference contig threaded in from the query is carried on both
    # the record's CHROM slot and inside the payload -- mapping-on-result.
    assert parser("2", (6, 10, 0.4)) == (
        "2", 6, 10, None, None, ("2", 6, 10, 0.4))


def test_bigwig_score_reads_value_at_index_3_through_record_score_line(
    tmp_path: pathlib.Path,
) -> None:
    # A bigWig score is configured at column ``index: 3`` (the value column of
    # the four-element interval payload).  Now that the backend yields records,
    # GenomicScore.open must route it to RecordScoreLine, whose by-index read
    # of the payload resolves that score -- the record-path equivalent of the
    # retired ``BigWigLine.get(3)``.
    builder = (
        a_bigwig_score()
        .with_score("bw", "float")
        .with_data("chr1  0  10  0.11")
        .with_chrom_lens({"chr1": 1000})
    )
    repo = a_grr().with_resource("bw", builder).build_repo(tmp_path)
    score = PositionScore(repo.get_resource("bw")).open()
    with score:
        assert score._score_line_class is RecordScoreLine
        line = next(iter(score.fetch_lines("chr1", 5, 5)))
        assert type(line) is RecordScoreLine
        assert line.get_score("bw") == pytest.approx(0.11)


def test_get_records_in_region(bigwig_table: BigWigTable) -> None:
    with bigwig_table:
        vs = list(bigwig_table.get_records_in_region("chr1"))
        assert len(vs) == 3


def test_get_records_in_region_with_position(bigwig_table: BigWigTable) -> None:
    with bigwig_table:
        vs = list(bigwig_table.get_records_in_region("chr1", 1, 9))
        assert len(vs) == 1


def test_get_records_begin_pos_out_of_bounds(bigwig_table: BigWigTable) -> None:
    with bigwig_table:
        vs = list(bigwig_table.get_records_in_region("chr1", -1))
        assert len(vs) == 3


def test_get_records_end_pos_out_of_bounds(bigwig_table: BigWigTable) -> None:
    with bigwig_table:
        vs = list(bigwig_table.get_records_in_region("chr1", 1, 999_999_999))
        assert len(vs) == 3


def test_get_records_in_region_with_position_single(
    bigwig_table: BigWigTable,
) -> None:
    with bigwig_table:
        vs = list(bigwig_table.get_records_in_region("chr1", 5, 5))
        assert len(vs) == 1


def test_get_records_in_region_left_only(bigwig_table: BigWigTable) -> None:
    with bigwig_table:
        vs = list(bigwig_table.get_records_in_region("chr1", pos_begin=21))
        assert len(vs) == 1


def test_get_records_in_region_right_only(bigwig_table: BigWigTable) -> None:
    with bigwig_table:
        vs = list(bigwig_table.get_records_in_region("chr1", pos_end=20))
        assert len(vs) == 2


def test_get_records_in_region_missing_chrom(bigwig_table: BigWigTable) -> None:
    with bigwig_table, pytest.raises(KeyError):
        list(bigwig_table.get_records_in_region("chrX"))


def test_get_records_in_region_without_chrom(bigwig_table: BigWigTable) -> None:
    with bigwig_table:
        vs = list(bigwig_table.get_records_in_region())
        assert len(vs) == 9


def test_build_genomic_position_table_bigwig(
    test_grr: GenomicResourceRepo,
) -> None:
    res = test_grr.get_resource("test_score")

    table = build_genomic_position_table(
        res, {"filename": "data.bw", "format": "bigWig"},
    )
    assert isinstance(table, BigWigTable)

    table = build_genomic_position_table(
        res, {"filename": "data.bw"},
    )
    assert isinstance(table, BigWigTable)


def test_bigwig_genomic_position_table_chrom_mapping_works(
    tmp_path: pathlib.Path,
) -> None:
    root_path = tmp_path
    setup_directories(
        root_path,
        {
            "grr.yaml": textwrap.dedent(f"""
                id: test_grr
                type: directory
                directory: {root_path!s}
            """),
            "test_score": {
                "genomic_resource.yaml": textwrap.dedent("""
                        type: position_score
                        table:
                            filename: data.bw
                            chrom_mapping:
                                del_prefix: chr
                        scores:
                        - id: score_one
                          type: float
                          index: 3
                """),
            },
        },
    )
    data = textwrap.dedent("""
        chr1   0          10       0.01
        chr2   10         20       0.02
        chr3   20         30       0.03
    """)
    setup_bigwig(
        root_path / "test_score" / "data.bw", data,
        {"chr1": 1000,
         "chr2": 2000,
         "chr3": 3000},
    )
    grr = build_filesystem_test_repository(root_path)
    assert grr is not None
    res = grr.get_resource("test_score")
    with BigWigTable(res, res.get_config()["table"]) as bigwig_table:
        assert bigwig_table.get_chromosomes() == ["1", "2", "3"]
        vs = list(bigwig_table.get_all_records())
        assert len(vs) == 3
        assert vs[0][CHROM] == "1"
        assert vs[1][CHROM] == "2"
        assert vs[2][CHROM] == "3"

        vs = list(bigwig_table.get_records_in_region("1"))
        assert len(vs) == 1
        assert vs[0][CHROM] == "1"


def test_bigwig_correct_fetching_of_intervals(
    tmp_path: pathlib.Path,
) -> None:
    # Make sure there are no duplicated entries returned:
    # This was happening with naive incrementation of the start/stop fetch
    # positions, as it was possible to land in the middle of a long entry
    # spanning many bases, which would be returned more than once by the
    # intervals fetching method

    root_path = tmp_path
    setup_directories(
        root_path,
        {
            "grr.yaml": textwrap.dedent(f"""
                id: test_grr
                type: directory
                directory: {root_path!s}
            """),
            "test_score": {
                "genomic_resource.yaml": textwrap.dedent("""
                        type: position_score
                        table:
                            filename: data.bw
                            format: bigWig
                        scores:
                        - id: score_one
                          type: float
                          index: 3
                """),
            },
        },
    )
    data = textwrap.dedent("""
        chr1   0          10000       0.01
        chr1   10000      20000       0.02
        chr1   20000      30000       0.03
        chr1   30000      40000       0.04
        chr1   40000      50000       0.05
        chr1   50000      60000       0.06
    """)
    setup_bigwig(
        root_path / "test_score" / "data.bw", data,
        {"chr1": 100_000},
    )
    grr = build_filesystem_test_repository(root_path)
    assert grr is not None
    res = grr.get_resource("test_score")
    table_definition = res.get_config()["table"]
    with BigWigTable(res, table_definition) as bigwig_table:
        vs = list(bigwig_table.get_all_records())
        assert len(vs) == 6
        vs = list(bigwig_table.get_records_in_region("chr1", 1))
        assert len(vs) == 6
        vs = list(bigwig_table.get_records_in_region("chr1", 0, 30000))
        assert len(vs) == 3


def test_no_repeating_in_buffered(
    tmp_path: pathlib.Path,
) -> None:
    root_path = tmp_path
    setup_directories(
        root_path,
        {
            "grr.yaml": textwrap.dedent(f"""
                id: test_grr
                type: directory
                directory: {root_path!s}
            """),
            "test_score": {
                "genomic_resource.yaml": textwrap.dedent("""
                        type: position_score
                        table:
                            filename: data.bw
                            format: bigWig
                        scores:
                        - id: score_one
                          type: float
                          index: 3
                """),
            },
        },
    )
    data = textwrap.dedent("""
        chr1   0         1000       0.01
        chr1   1000      1001       0.02
        chr1   1001      1002       0.03
        chr1   1002      1003       0.04
        chr1   1003      1004       0.05
    """)
    setup_bigwig(
        root_path / "test_score" / "data.bw", data,
        {"chr1": 5000},
    )
    grr = build_filesystem_test_repository(root_path)
    assert grr is not None
    res = grr.get_resource("test_score")
    table_definition = res.get_config()["table"]
    with BigWigTable(res, table_definition) as bigwig_table:
        vs = list(bigwig_table.get_records_in_region("chr1", 1001, 1001))
        assert len(vs) == 1
        assert vs[0][CHROM] == "chr1"
        assert vs[0][POS_BEGIN] == 1001
        assert vs[0][POS_END] == 1001
        vs = list(bigwig_table.get_records_in_region("chr1", 1002, 1002))
        assert len(vs) == 1
        assert vs[0][CHROM] == "chr1"
        assert vs[0][POS_BEGIN] == 1002
        assert vs[0][POS_END] == 1002
        vs = list(bigwig_table.get_records_in_region("chr1", 1003, 1003))
        assert len(vs) == 1
        assert vs[0][CHROM] == "chr1"
        assert vs[0][POS_BEGIN] == 1003
        assert vs[0][POS_END] == 1003


def test_no_repeating_in_buffered_alt_case(
    tmp_path: pathlib.Path,
) -> None:
    root_path = tmp_path
    setup_directories(
        root_path,
        {
            "grr.yaml": textwrap.dedent(f"""
                id: test_grr
                type: directory
                directory: {root_path!s}
            """),
            "test_score": {
                "genomic_resource.yaml": textwrap.dedent("""
                        type: position_score
                        table:
                            filename: data.bw
                            format: bigWig
                        scores:
                        - id: score_one
                          type: float
                          index: 3
                """),
            },
        },
    )
    data = textwrap.dedent("""
        chr1   0         1000       0.01
        chr1   1000      1001       0.02
        chr1   1001      1002       0.03
        chr1   1002      1003       0.04
    """)
    setup_bigwig(
        root_path / "test_score" / "data.bw", data,
        {"chr1": 5000},
    )
    grr = build_filesystem_test_repository(root_path)
    assert grr is not None
    res = grr.get_resource("test_score")
    table_definition = res.get_config()["table"]
    table_definition["buffer_fetch_size"] = 1  # important for bug reproduction!
    with BigWigTable(res, table_definition) as bigwig_table:
        vs = list(bigwig_table.get_records_in_region("chr1", 1, 1000))
        assert len(vs) == 1
        assert vs[0][CHROM] == "chr1"
        assert vs[0][POS_BEGIN] == 1
        assert vs[0][POS_END] == 1000
        vs = list(bigwig_table.get_records_in_region("chr1", 1001, 1001))
        assert len(vs) == 1
        assert vs[0][CHROM] == "chr1"
        assert vs[0][POS_BEGIN] == 1001
        assert vs[0][POS_END] == 1001
        vs = list(bigwig_table.get_records_in_region("chr1", 1002, 1002))
        assert len(vs) == 1
        assert vs[0][CHROM] == "chr1"
        assert vs[0][POS_BEGIN] == 1002
        assert vs[0][POS_END] == 1002


def test_buffered_correctly_checks_if_query_is_in_buffer(
    tmp_path: pathlib.Path,
) -> None:
    root_path = tmp_path
    setup_directories(
        root_path,
        {
            "grr.yaml": textwrap.dedent(f"""
                id: test_grr
                type: directory
                directory: {root_path!s}
            """),
            "test_score": {
                "genomic_resource.yaml": textwrap.dedent("""
                        type: position_score
                        table:
                            filename: data.bw
                            format: bigWig
                        scores:
                        - id: score_one
                          type: float
                          index: 3
                """),
            },
        },
    )
    data = textwrap.dedent("""
        chr1   1000      1001       0.02
        chr1   1001      1002       0.03
        chr1   1002      1003       0.04
        chr1   1003      1004       0.05
    """)
    setup_bigwig(
        root_path / "test_score" / "data.bw", data,
        {"chr1": 5000},
    )
    grr = build_filesystem_test_repository(root_path)
    assert grr is not None
    res = grr.get_resource("test_score")
    table_definition = res.get_config()["table"]
    table_definition["buffer_fetch_size"] = 2  # important for bug reproduction!
    with BigWigTable(res, table_definition) as bigwig_table:
        vs = list(bigwig_table.get_records_in_region("chr1", 1001, 1001))
        assert len(vs) == 1
        assert vs[0][CHROM] == "chr1"
        assert vs[0][POS_BEGIN] == 1001
        assert vs[0][POS_END] == 1001
        vs = list(bigwig_table.get_records_in_region("chr1", 1002, 1002))
        assert len(vs) == 1
        assert vs[0][CHROM] == "chr1"
        assert vs[0][POS_BEGIN] == 1002
        assert vs[0][POS_END] == 1002
        vs = list(bigwig_table.get_records_in_region("chr1", 1004, 1004))
        assert len(vs) == 1
        assert vs[0][CHROM] == "chr1"
        assert vs[0][POS_BEGIN] == 1004
        assert vs[0][POS_END] == 1004


def test_buffering_correctly_fetches_next_buffer(
    tmp_path: pathlib.Path,
) -> None:
    root_path = tmp_path
    setup_directories(
        root_path,
        {
            "grr.yaml": textwrap.dedent(f"""
                id: test_grr
                type: directory
                directory: {root_path!s}
            """),
            "test_score": {
                "genomic_resource.yaml": textwrap.dedent("""
                        type: position_score
                        table:
                            filename: data.bw
                            format: bigWig
                        scores:
                        - id: score_one
                          type: float
                          index: 3
                """),
            },
        },
    )
    data = textwrap.dedent("""
        chr1   1000      1001       0.02
        chr1   1001      1002       0.03
        chr1   1002      1003       0.04
        chr1   1003      1004       0.05
        chr1   1004      1005       0.06
        chr1   1005      1006       0.07
    """)
    setup_bigwig(
        root_path / "test_score" / "data.bw", data,
        {"chr1": 5000},
    )
    grr = build_filesystem_test_repository(root_path)
    assert grr is not None
    res = grr.get_resource("test_score")
    table_definition = res.get_config()["table"]
    table_definition["buffer_fetch_size"] = 3  # important for bug reproduction!
    with BigWigTable(res, table_definition) as bigwig_table:
        vs = list(bigwig_table.get_records_in_region("chr1", 1001, 1001))
        assert len(vs) == 1
        assert vs[0][CHROM] == "chr1"
        assert vs[0][POS_BEGIN] == 1001
        assert vs[0][POS_END] == 1001
        vs = list(bigwig_table.get_records_in_region("chr1", 1002, 1002))
        assert len(vs) == 1
        assert vs[0][CHROM] == "chr1"
        assert vs[0][POS_BEGIN] == 1002
        assert vs[0][POS_END] == 1002
        vs = list(bigwig_table.get_records_in_region("chr1", 1003, 1005))
        assert len(vs) == 3
        assert vs[0][CHROM] == "chr1"
        assert vs[0][POS_BEGIN] == 1003
        assert vs[0][POS_END] == 1003
        assert vs[1][CHROM] == "chr1"
        assert vs[1][POS_BEGIN] == 1004
        assert vs[1][POS_END] == 1004
        assert vs[2][CHROM] == "chr1"
        assert vs[2][POS_BEGIN] == 1005
        assert vs[2][POS_END] == 1005


def test_bigwig_buffering_switching(
    tmp_path: pathlib.Path, mocker: pytest_mock.MockerFixture,
) -> None:
    root_path = tmp_path
    setup_directories(
        root_path,
        {
            "grr.yaml": textwrap.dedent(f"""
                id: test_grr
                type: directory
                directory: {root_path!s}
            """),
            "test_score": {
                "genomic_resource.yaml": textwrap.dedent("""
                        type: position_score
                        table:
                            filename: data.bw
                            format: bigWig
                        scores:
                        - id: score_one
                          type: float
                          index: 3
                """),
            },
        },
    )
    data = textwrap.dedent("""
        chr1   0        100       0.01
        chr1   100      500       0.02
        chr1   500      1500      0.03
    """)
    setup_bigwig(
        root_path / "test_score" / "data.bw", data,
        {"chr1": 100_000},
    )
    grr = build_filesystem_test_repository(root_path)
    assert grr is not None
    res = grr.get_resource("test_score")
    table_definition = res.get_config()["table"]

    mocker.spy(BigWigTable, "_fetch_direct")
    mocker.spy(BigWigTable, "_fetch_buffered")

    with BigWigTable(res, table_definition) as bigwig_table:
        assert BigWigTable._fetch_direct.call_count == 0  # type: ignore
        assert BigWigTable._fetch_buffered.call_count == 0  # type: ignore

        list(bigwig_table.get_records_in_region("chr1", 0, 200))
        assert BigWigTable._fetch_direct.call_count == 1  # type: ignore
        assert BigWigTable._fetch_buffered.call_count == 0  # type: ignore

        list(bigwig_table.get_records_in_region("chr1", 200, 500))
        assert BigWigTable._fetch_direct.call_count == 1  # type: ignore
        assert BigWigTable._fetch_buffered.call_count == 1  # type: ignore

        list(bigwig_table.get_records_in_region("chr1", 1000, 1000))
        assert BigWigTable._fetch_direct.call_count == 2  # type: ignore
        assert BigWigTable._fetch_buffered.call_count == 1  # type: ignore


def test_buffered_pos_begin_to_the_left_of_buffer_start(
    tmp_path: pathlib.Path,
) -> None:
    root_path = tmp_path
    setup_directories(
        root_path,
        {
            "grr.yaml": textwrap.dedent(f"""
                id: test_grr
                type: directory
                directory: {root_path!s}
            """),
            "test_score": {
                "genomic_resource.yaml": textwrap.dedent("""
                        type: position_score
                        table:
                            filename: data.bw
                            format: bigWig
                        scores:
                        - id: score_one
                          type: float
                          index: 3
                """),
            },
        },
    )
    data = textwrap.dedent("""
        chr1   1000      1001       0.02
        chr1   1001      1002       0.03
        chr1   1002      1003       0.04
        chr1   1003      1004       0.04
        chr1   1004      1005       0.04
    """)
    setup_bigwig(
        root_path / "test_score" / "data.bw", data,
        {"chr1": 5000},
    )
    grr = build_filesystem_test_repository(root_path)
    assert grr is not None
    res = grr.get_resource("test_score")
    table_definition = res.get_config()["table"]
    with BigWigTable(res, table_definition) as bigwig_table:
        vs = list(bigwig_table.get_records_in_region("chr1", 1002, 1002))
        assert len(vs) == 1
        assert vs[0][CHROM] == "chr1"
        assert vs[0][POS_BEGIN] == 1002
        assert vs[0][POS_END] == 1002
        vs = list(bigwig_table.get_records_in_region("chr1", 1003, 1003))
        assert len(vs) == 1
        assert vs[0][CHROM] == "chr1"
        assert vs[0][POS_BEGIN] == 1003
        assert vs[0][POS_END] == 1003
        vs = list(bigwig_table.get_records_in_region("chr1", 1001, 1005))
        assert len(vs) == 5


def test_mini_grr_example(tmp_path: pathlib.Path) -> None:
    root_path = tmp_path
    setup_directories(
        root_path,
        {
            "grr.yaml": textwrap.dedent(f"""
                id: test_grr
                type: directory
                directory: {root_path!s}
            """),
            "test_score": {
                "genomic_resource.yaml": textwrap.dedent("""
                        type: position_score
                        table:
                            filename: data.bw
                            format: bigWig
                        scores:
                        - id: score_one
                          type: float
                          index: 3
                """),
            },
        },
    )
    data = textwrap.dedent("""
        chr1   0    5  0.1
        chr1   5   10  0.2
        chr2   0    5  0.3
        chr2   5   10  0.4
    """)
    setup_bigwig(
        root_path / "test_score" / "data.bw", data,
        {"chr1": 10, "chr2": 20},
    )
    grr = build_filesystem_test_repository(root_path)
    assert grr is not None
    res = grr.get_resource("test_score")
    table_definition = res.get_config()["table"]
    with BigWigTable(res, table_definition) as bigwig_table:
        vs = list(bigwig_table.get_records_in_region("chr1", 5, 5))
        assert len(vs) == 1
        assert vs[0][CHROM] == "chr1"
        assert vs[0][POS_BEGIN] == 1
        assert vs[0][POS_END] == 5
        vs = list(bigwig_table.get_records_in_region("chr1", 6, 6))
        assert len(vs) == 1
        assert vs[0][CHROM] == "chr1"
        assert vs[0][POS_BEGIN] == 6
        assert vs[0][POS_END] == 10


class _IntervalCallRecorder:
    """An open bigWig handle that records every ``intervals()`` call made.

    The chunking strategy is not observable through the records a fetch
    yields -- by design, since the records must stay identical -- so it is
    observed where it actually shows up: in the range queries the backend
    issues against the file.  Each entry is ``(start, stop, n_intervals)``.
    Everything other than ``intervals`` delegates to the real handle.
    """

    def __init__(self, bw_file: Any) -> None:
        self._bw_file = bw_file
        self.calls: list[tuple[int, int, int]] = []

    def intervals(self, chrom: str, start: int, stop: int) -> Any:
        res = self._bw_file.intervals(chrom, start, stop)
        self.calls.append((start, stop, len(res) if res else 0))
        return res

    def __getattr__(self, name: str) -> Any:
        return getattr(self._bw_file, name)

    @property
    def raw(self) -> Any:
        """The unwrapped handle, for computing an unchunked ground truth."""
        return self._bw_file

    @property
    def widest_call(self) -> int:
        """The most intervals any single ``intervals()`` call materialised."""
        return max((n for _, _, n in self.calls), default=0)


@contextlib.contextmanager
def _recorded_bigwig(
    tmp_path: pathlib.Path,
    data: str,
    chrom_lens: dict[str, int],
    **definition: Any,
) -> Iterator[tuple[BigWigTable, _IntervalCallRecorder]]:
    """Open a bigWig table over ``data`` with its range queries recorded."""
    builder = (
        a_bigwig_score()
        .with_score("bw", "float")
        .with_data(data)
        .with_chrom_lens(chrom_lens)
    )
    repo = a_grr().with_resource("bw", builder).build_repo(tmp_path)
    res = repo.get_resource("bw")
    table = BigWigTable(res, {**res.get_config()["table"], **definition}).open()
    recorder = _IntervalCallRecorder(table._bw_file)
    table._bw_file = recorder
    try:
        yield table, recorder
    finally:
        table.close()


def _bedgraph(
    intervals: list[tuple[int, int]], chrom: str = "chr1",
) -> str:
    """Render ``(start, stop)`` pairs as bedGraph rows with distinct values."""
    return "\n".join(
        f"{chrom}  {start}  {stop}  {i % 97 / 100.0 + 0.01:.2f}"
        for i, (start, stop) in enumerate(intervals)
    )


# A dense fixture: back-to-back 50 bp runs over 100 kb.  Under the old fixed
# 50 bp chunking this is exactly one range query per interval.
_DENSE_WIDTH = 50
_DENSE_COUNT = 2000
_DENSE_LEN = _DENSE_WIDTH * _DENSE_COUNT


def _dense_bedgraph() -> str:
    return _bedgraph([
        (i * _DENSE_WIDTH, (i + 1) * _DENSE_WIDTH)
        for i in range(_DENSE_COUNT)
    ])


def test_dense_region_fetch_scales_with_records_not_region_length(
    tmp_path: pathlib.Path,
) -> None:
    # A dense 100 kb region carrying 2000 records must be served in a number
    # of range queries proportional to its 2000 records, not to its length in
    # 50 bp chunks (which is also 2000 -- the two are told apart by the
    # bound: a records-proportional fetch needs an order of magnitude fewer).
    with _recorded_bigwig(
        tmp_path, _dense_bedgraph(), {"chr1": _DENSE_LEN},
    ) as (table, recorder):
        records = list(table.get_records_in_region("chr1", 1, _DENSE_LEN))

    assert len(records) == _DENSE_COUNT
    assert len(recorder.calls) < _DENSE_COUNT // 20


# A sparse fixture: two scored 50 bp runs separated by a ~1 Mb unscored gap.
# Under the old fixed chunking, crossing that gap cost one *empty* range query
# per 50 bp of it.
_SPARSE_LEN = 1_000_000


def _sparse_bedgraph() -> str:
    return _bedgraph([(0, 50), (_SPARSE_LEN - 50, _SPARSE_LEN)])


def test_sparse_region_fetch_crosses_an_unscored_gap_in_few_queries(
    tmp_path: pathlib.Path,
) -> None:
    # An ``intervals()`` call that comes back empty proves its whole window
    # holds no records, so the window may grow -- a ~1 Mb gap is crossed in a
    # handful of widening strides rather than 20,000 fixed 50 bp probes.
    with _recorded_bigwig(
        tmp_path, _sparse_bedgraph(), {"chr1": _SPARSE_LEN},
    ) as (table, recorder):
        records = list(table.get_records_in_region("chr1", 1, _SPARSE_LEN))

    assert len(records) == 2
    assert len(recorder.calls) <= 10


def test_buffered_region_fetch_crosses_an_unscored_gap_in_few_queries(
    tmp_path: pathlib.Path, mocker: pytest_mock.MockerFixture,
) -> None:
    # The same gap, crossed by the *buffered* strategy -- the one a sequential
    # annotation run takes, since every query after the first lands within
    # ``use_buffered_threshold`` of the last.  The buffer is refilled by its own
    # walk, so it needs its own budget: filling across a ~1 Mb gap must take a
    # handful of widening strides, not one fixed probe per 500 bp of it.
    with _recorded_bigwig(
        tmp_path, _sparse_bedgraph(), {"chr1": _SPARSE_LEN},
    ) as (table, recorder):
        # The first fetch of a fresh table takes the direct strategy; it is
        # only here to bring ``_last_pos`` next to the second fetch's start.
        primer = list(table.get_records_in_region("chr1", 1, 60))
        calls_after_primer = len(recorder.calls)

        buffered_fetch = mocker.spy(BigWigTable, "_fetch_buffered")
        records = list(table.get_records_in_region("chr1", 1, _SPARSE_LEN))
        buffered_calls = len(recorder.calls) - calls_after_primer

    assert buffered_fetch.call_count == 1
    assert len(primer) == 1
    assert len(records) == 2
    assert buffered_calls <= 10


# A gapped fixture: three short scored runs with wide unscored gaps between
# them, so that a buffer filled at one of them spans -- and reaches well past --
# positions the track does not cover.
def _gapped_bedgraph() -> str:
    return _bedgraph([(300, 350), (3100, 3150), (6700, 6750)])


def test_buffered_fetch_yields_nothing_at_a_position_inside_a_gap(
    tmp_path: pathlib.Path, mocker: pytest_mock.MockerFixture,
) -> None:
    # A buffer spans as far as its fill window reached, so a later query can
    # land in an unscored gap *inside* the buffered range.  The buffer's binary
    # search finds no record there and must say so -- it must not fall back to
    # the nearest record on its left, which would emit a score at a position
    # the track does not cover.  Positions inside a scored run are queried in
    # the same sweep, so the answer is pinned in both directions.
    positions = [1, 201, 320, 401, 601, 1001]

    buffered_fetch = mocker.spy(BigWigTable, "_fetch_buffered")
    with _recorded_bigwig(
        tmp_path, _gapped_bedgraph(), {"chr1": _SPARSE_LEN},
    ) as (table, recorder):
        expected = {
            pos: [
                ("chr1", start + 1, stop, None, None,
                 ("chr1", start + 1, stop, value))
                for start, stop, value in (
                    recorder.raw.intervals("chr1", pos - 1, pos) or [])
            ]
            for pos in positions
        }
        # Queried in ascending order and closer together than
        # ``use_buffered_threshold``, so every query but the first is served
        # from the buffer.
        found = {
            pos: list(table.get_records_in_region("chr1", pos, pos))
            for pos in positions
        }

    assert buffered_fetch.call_count == len(positions) - 1
    assert found == expected


# A per-base fixture -- the shape of the canonical position scores (phyloP,
# phastCons) and the one that makes the chunking load bearing: one interval per
# base, so a single unchunked whole-chromosome call would materialise one
# Python interval per base of the contig.
_PER_BASE_LEN = 100_000

# ``pyBigWig.intervals()`` materialises its range as a list of Python tuples,
# measured at roughly this many bytes per interval.
_BYTES_PER_INTERVAL = 160


def _per_base_bedgraph() -> str:
    return _bedgraph([(i, i + 1) for i in range(_PER_BASE_LEN)])


def test_whole_chromosome_scan_of_per_base_bigwig_stays_memory_bounded(
    tmp_path: pathlib.Path,
) -> None:
    # ``get_all_records`` delegates to an unbounded region fetch, which expands
    # to the whole contig -- the path the resource-statistics and histogram
    # scans take.  On a per-base track a single call over that range would be
    # one interval per base (~40 GB on a real chr1), so what is asserted here
    # is the *widest single call*: no ``intervals()`` call may materialise more
    # than a few times the records-per-call budget, however long the contig is.
    with _recorded_bigwig(
        tmp_path, _per_base_bedgraph(), {"chr1": _PER_BASE_LEN},
    ) as (table, recorder):
        count = sum(1 for _ in table.get_all_records())

    assert count == _PER_BASE_LEN
    assert recorder.widest_call <= 4 * DEFAULT_FETCH_TARGET_RECORDS
    # Restated as the bound that actually matters: live intervals per call.
    assert recorder.widest_call * _BYTES_PER_INTERVAL < 8 * 1024 * 1024


@pytest.mark.parametrize("shape", ["dense", "sparse"])
@pytest.mark.parametrize("budget", [1, 7, 500, 5_000, 10_000_000])
def test_chunking_is_invisible_in_the_records_yielded(
    tmp_path: pathlib.Path, shape: str, budget: int,
) -> None:
    # The chunking exists only to bound memory, so it must not be observable in
    # the result: whatever the records-per-call budget, and whichever fetch
    # strategy runs, a region fetch must yield exactly the records of a single
    # unchunked ``intervals()`` call over the same range -- same values, same
    # order, same closed one-based coordinates.
    data, chrom_len = (
        (_dense_bedgraph(), _DENSE_LEN) if shape == "dense"
        else (_sparse_bedgraph(), _SPARSE_LEN)
    )
    with _recorded_bigwig(
        tmp_path, data, {"chr1": chrom_len},
        direct_fetch_size=budget, buffer_fetch_size=budget,
    ) as (table, recorder):
        expected = [
            ("chr1", start + 1, stop, None, None,
             ("chr1", start + 1, stop, value))
            for start, stop, value in recorder.raw.intervals(
                "chr1", 0, chrom_len)
        ]
        # The first fetch of a fresh table takes the direct strategy; a second
        # fetch of the same region is within ``use_buffered_threshold`` of the
        # first, so it takes the buffered one.  Both are checked.
        direct = list(table.get_records_in_region("chr1", 1, chrom_len))
        buffered = list(table.get_records_in_region("chr1", 1, chrom_len))

    assert direct == expected
    assert buffered == expected


def test_table_schema_accepts_the_fetch_budget_keys(
    tmp_path: pathlib.Path,
) -> None:
    # The fetch budgets are read off the table definition, so they must be
    # spellable in a ``genomic_resource.yaml``: a knob the code reads and the
    # schema rejects is not a knob.  Configuring all three must validate, and
    # the configured values must reach the table.
    builder = (
        a_bigwig_score()
        .with_score("score_one", "float")
        .with_data("chr1  0  10  0.11")
        .with_chrom_lens({"chr1": 1000})
        .with_fetch_budgets(
            direct_fetch_size=1000,
            buffer_fetch_size=2000,
            use_buffered_threshold=100,
        )
    )
    grr = a_grr().with_resource("test_score", builder).build_repo(tmp_path)

    score = PositionScore(grr.get_resource("test_score"))

    table = score.table
    assert isinstance(table, BigWigTable)
    assert table.direct_fetch_size == 1000
    assert table.buffer_fetch_size == 2000
    assert table.use_buffered_threshold == 100
