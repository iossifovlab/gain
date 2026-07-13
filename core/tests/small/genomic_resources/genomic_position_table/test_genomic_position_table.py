# pylint: disable=W0621,C0114,C0116,W0212,W0613,too-many-lines
import pathlib
import textwrap
from typing import cast

import pysam
import pytest
import pytest_mock
from gain.genomic_resources.genomic_position_table import (
    TabixGenomicPositionTable,
    VCFGenomicPositionTable,
    build_genomic_position_table,
    table_tabix,
)
from gain.genomic_resources.genomic_position_table.record import (
    ALT,
    CHROM,
    PAYLOAD,
    POS_BEGIN,
    POS_END,
    RECORD_SLOTS,
    REF,
    sort_key,
)
from gain.genomic_resources.genomic_position_table.table import (
    GenomicPositionTable,
)
from gain.genomic_resources.genomic_position_table.table_vcf import (
    ALLELE_INDEX,
    VARIANT,
)
from gain.genomic_resources.repository import GenomicResource
from gain.genomic_resources.testing import (
    build_filesystem_test_resource,
    build_inmemory_test_resource,
    convert_to_tab_separated,
    setup_directories,
    setup_tabix,
    setup_vcf,
)


@pytest.fixture
def vcf_res(tmp_path: pathlib.Path) -> GenomicResource:
    setup_directories(
        tmp_path, {
            "genomic_resource.yaml": textwrap.dedent("""
                tabix_table:
                    filename: data.vcf.gz
                    format: vcf_info
            """),
        })
    setup_vcf(
        tmp_path / "data.vcf.gz",
        textwrap.dedent("""
##fileformat=VCFv4.1
##INFO=<ID=A,Number=1,Type=Integer,Description="Score A">
##INFO=<ID=B,Number=1,Type=Integer,Description="Score B">
##INFO=<ID=C,Number=.,Type=String,Description="Score C">
##INFO=<ID=D,Number=.,Type=String,Description="Score D">
##contig=<ID=chr1>
##contig=<ID=chr2>
##contig=<ID=chr3>
#CHROM POS ID REF ALT QUAL FILTER INFO
chr1   5   .  A   T   .    .      A=1;C=c11,c12;D=d11
chr1   15  .  A   T   .    .      A=2;B=21;C=c21;D=d21,d22
chr1   30  .  A   T   .    .      A=3;B=31;C=c21;D=d31,d32
    """),
    )
    return build_filesystem_test_resource(tmp_path)


@pytest.fixture
def vcf_res_autodetect_format(tmp_path: pathlib.Path) -> GenomicResource:
    setup_directories(
        tmp_path, {
            "genomic_resource.yaml": textwrap.dedent("""
                tabix_table:
                    filename: data.vcf.gz
            """),
        })
    setup_vcf(
        tmp_path / "data.vcf.gz",
        textwrap.dedent("""
##fileformat=VCFv4.1
##INFO=<ID=A,Number=1,Type=Integer,Description="Score A">
#CHROM POS ID REF ALT QUAL FILTER  INFO
chr1   5   .  A   T   .    .       A=1
    """),
    )
    return build_filesystem_test_resource(tmp_path)


@pytest.fixture
def vcf_res_chrom_mapping(tmp_path: pathlib.Path) -> GenomicResource:
    setup_directories(
        tmp_path, {
            "genomic_resource.yaml": textwrap.dedent("""
                tabix_table:
                    filename: data.vcf.gz
                    format: vcf_info
                    chrom_mapping:
                        del_prefix: chr
            """),
        })
    setup_vcf(
        tmp_path / "data.vcf.gz",
        textwrap.dedent("""
##fileformat=VCFv4.1
##INFO=<ID=A,Number=1,Type=Integer,Description="Score A">
##contig=<ID=chr1>
#CHROM POS ID REF ALT QUAL FILTER INFO
chr1   5   .  A   T   .    .      A=1
    """),
    )
    return build_filesystem_test_resource(tmp_path)


@pytest.fixture
def vcf_res_multiallelic(tmp_path: pathlib.Path) -> GenomicResource:
    setup_directories(
        tmp_path, {
            "genomic_resource.yaml": textwrap.dedent("""
                tabix_table:
                    filename: data.vcf.gz
                    format: vcf_info
            """),
        })
    setup_vcf(
        tmp_path / "data.vcf.gz",
        textwrap.dedent("""
##fileformat=VCFv4.1
##INFO=<ID=A,Number=1,Type=Integer,Description="Score A">
##INFO=<ID=B,Number=.,Type=Integer,Description="Score B">
##INFO=<ID=C,Number=R,Type=String,Description="Score C">
##INFO=<ID=D,Number=A,Type=String,Description="Score D">
#CHROM POS ID REF ALT QUAL FILTER  INFO
chr1   2   .  A   .   .    .       A=0;B=01,02,03;C=c01
chr1   5   .  A   T   .    .       A=1;B=11,12,13;C=c11,c12;D=d11
chr1   15   .  A   T,G   .    .       A=2;B=21,22;C=c21,c22,c23;D=d21,d22
chr1   30   .  A   T,G,C   .    .     A=3;B=31;C=c31,c32,c33,c34;D=d31,d32,d33
    """),
    )
    return build_filesystem_test_resource(tmp_path)


@pytest.fixture
def vcf_res_repeated_alt(tmp_path: pathlib.Path) -> GenomicResource:
    # A variant whose two ALT alleles are *the same string*, with a per-allele
    # (Number=A) INFO field that scores them differently.  The two records the
    # table emits for it agree in all five decoded slots and share one variant
    # record -- so they are the case that shows why the payload has to carry the
    # allele index: it is the only thing that says which allele a record is.
    setup_directories(
        tmp_path, {
            "genomic_resource.yaml": textwrap.dedent("""
                tabix_table:
                    filename: data.vcf.gz
                    format: vcf_info
            """),
        })
    setup_vcf(
        tmp_path / "data.vcf.gz",
        textwrap.dedent("""
##fileformat=VCFv4.1
##INFO=<ID=A,Number=A,Type=Integer,Description="Score A">
##contig=<ID=chr1>
#CHROM POS ID REF ALT QUAL FILTER  INFO
chr1   5   .  A   T,T   .    .       A=1,2
    """),
    )
    return build_filesystem_test_resource(tmp_path)


@pytest.fixture
def vcf_res_same_locus(tmp_path: pathlib.Path) -> GenomicResource:
    # Two *variant records* at one locus, with the same REF and the same
    # (missing) ALT and different INFO -- so the two records the table emits
    # agree in all five decoded slots and differ only in their PAYLOAD, the one
    # slot that cannot be ordered.  This is the case that tells whether records
    # can be sorted as bare tuples (they cannot -- use record.sort_key).
    setup_directories(
        tmp_path, {
            "genomic_resource.yaml": textwrap.dedent("""
                tabix_table:
                    filename: data.vcf.gz
                    format: vcf_info
            """),
        })
    setup_vcf(
        tmp_path / "data.vcf.gz",
        textwrap.dedent("""
##fileformat=VCFv4.1
##INFO=<ID=A,Number=1,Type=Integer,Description="Score A">
##contig=<ID=chr1>
#CHROM POS ID REF ALT QUAL FILTER  INFO
chr1   5   .  A   .   .    .       A=1
chr1   5   .  A   .   .    .       A=2
chr1   9   .  A   T   .    .       A=3
    """),
    )
    return build_filesystem_test_resource(tmp_path)


def test_regions() -> None:
    res = build_inmemory_test_resource({
        "genomic_resource.yaml": """
            table:
                filename: data.mem
            scores:
            - id: c2
              name: c2
              type: float""",
        "data.mem": convert_to_tab_separated("""
            chrom pos_begin pos_end  c2
            1     10        12       3.14
            1     15        20       4.14
            1     21        30       5.14""")})

    assert res.config is not None

    with build_genomic_position_table(res, res.config["table"]) as mem_tab:
        # The in-memory backend yields records; its payload slot carries the
        # raw row.
        assert [r[PAYLOAD] for r in mem_tab.get_all_records()] == [
            ("1", "10", "12", "3.14"),
            ("1", "15", "20", "4.14"),
            ("1", "21", "30", "5.14"),
        ]

        assert [
            r[PAYLOAD] for r in mem_tab.get_records_in_region("1", 11, 11)
        ] == [
            ("1", "10", "12", "3.14"),
        ]

        assert not list(mem_tab.get_records_in_region("1", 13, 14))

        assert [
            r[PAYLOAD] for r in mem_tab.get_records_in_region("1", 18, 21)
        ] == [
            ("1", "15", "20", "4.14"),
            ("1", "21", "30", "5.14"),
        ]


@pytest.mark.parametrize("jump_threshold", [
    0,
    1,
    2,
    1500,
])
def test_regions_in_tabix(
        tmp_path: pathlib.Path, jump_threshold: int) -> None:
    setup_directories(
        tmp_path, {
            "genomic_resource.yaml": textwrap.dedent("""
                table:
                    format: tabix
                    filename: data.txt.gz
                scores:
                - id: c2
                  name: c2
                  type: float"""),
        })
    setup_tabix(
        tmp_path / "data.txt.gz",
        """
        #chrom pos_begin pos_end  c2
        1     10        12       3.14
        1     15        20       4.14
        1     21        30       5.14
        """, seq_col=0, start_col=1, end_col=2)
    res = build_filesystem_test_resource(tmp_path)
    assert res.config is not None

    with build_genomic_position_table(res, res.config["table"]) as tab:
        assert tab
        cast(TabixGenomicPositionTable, tab).jump_threshold = jump_threshold
        assert [tuple(r[PAYLOAD]) for r in tab.get_all_records()] == [
            ("1", "10", "12", "3.14"),
            ("1", "15", "20", "4.14"),
            ("1", "21", "30", "5.14"),
        ]
        assert [
            tuple(r[PAYLOAD])
            for r in tab.get_records_in_region("1", 11, 11)
        ] == [
            ("1", "10", "12", "3.14"),
        ]
        assert not list(tab.get_records_in_region("1", 13, 14))
        assert [
            tuple(r[PAYLOAD])
            for r in tab.get_records_in_region("1", 18, 21)
        ] == [
            ("1", "15", "20", "4.14"),
            ("1", "21", "30", "5.14"),
        ]


@pytest.fixture
def scores_tabix_res(tmp_path: pathlib.Path) -> GenomicResource:
    setup_directories(
        tmp_path, {
            "genomic_resource.yaml": textwrap.dedent("""
                table:
                    format: tabix
                    filename: data.txt.gz
                scores:
                - id: c2
                  name: c2
                  type: float"""),
        })
    setup_tabix(
        tmp_path / "data.txt.gz",
        """
        #chrom pos_begin pos_end  c2
        1     10        12       3.14
        1     15        20       4.14
        1     21        30       5.14
        """, seq_col=0, start_col=1, end_col=2)
    return build_filesystem_test_resource(tmp_path)


def test_tabix_table_yields_records_with_a_lazy_payload(
    scores_tabix_res: GenomicResource,
) -> None:
    # The tabix backend is on the record contract: it yields six-slot tuples,
    # and the payload is the raw pysam row *itself* -- not a materialised
    # tuple of its columns.  Keeping the row lazy is the whole point: a wide
    # resource decodes only the columns a caller asks for.
    assert scores_tabix_res.config is not None

    with build_genomic_position_table(
        scores_tabix_res, scores_tabix_res.config["table"],
    ) as tab:
        assert tab.yields_records

        records = list(tab.get_all_records())
        assert [(r[CHROM], r[POS_BEGIN], r[POS_END]) for r in records] == [
            ("1", 10, 12),
            ("1", 15, 20),
            ("1", 21, 30),
        ]

        # ``pysam.TupleProxy`` is the lazy row ``pysam.asTuple()`` hands out;
        # it is re-exported from pysam's top level (it is in ``pysam.__all__``)
        # so this asserts laziness through the public API, without importing
        # the ``pysam.libctabixproxies`` module path that defines it.
        payload = records[0][PAYLOAD]
        assert isinstance(payload, pysam.TupleProxy)
        assert payload[3] == "3.14"


def test_tabix_records_are_immutable_tuples(
    scores_tabix_res: GenomicResource,
) -> None:
    # Records are plain tuples, so the chrom-mapping / zero-based transforms
    # can no longer rewrite a record that the LineBuffer is holding at the
    # same time -- the adapter-era in-place mutation is gone by construction.
    assert scores_tabix_res.config is not None

    with build_genomic_position_table(
        scores_tabix_res, scores_tabix_res.config["table"],
    ) as tab:
        record = next(iter(tab.get_records_in_region("1", 11, 11)))
        assert isinstance(record, tuple)
        with pytest.raises(TypeError):
            record[POS_BEGIN] = 42  # type: ignore[index]


def test_tabix_record_payload_survives_the_iterator_advancing(
    scores_tabix_res: GenomicResource,
) -> None:
    # The record's PAYLOAD is the raw pysam row, held by reference and kept
    # lazy -- and both the caller and the LineBuffer retain it while the read
    # goes on.  That is only sound because ``pysam.asTuple()`` hands out a
    # fresh, buffer-owning row per line; were it to reuse one row object (as
    # some htslib iterators do), every retained record would silently start
    # reading the *latest* line's columns.  Pin it: a record read out of the
    # first query still decodes its own columns after the iterator has moved
    # on, and so does the copy of it the buffer is holding.
    assert scores_tabix_res.config is not None

    with build_genomic_position_table(
        scores_tabix_res, scores_tabix_res.config["table"],
    ) as tab:
        assert isinstance(tab, TabixGenomicPositionTable)

        retained = next(iter(tab.get_records_in_region("1", 11, 11)))
        assert tuple(retained[PAYLOAD]) == ("1", "10", "12", "3.14")

        # advance the read well past that record: two further queries, the
        # second of which re-fetches from the file
        assert [r[POS_BEGIN] for r in tab.get_records_in_region("1", 15, 20)] \
            == [15]
        assert [r[POS_BEGIN] for r in tab.get_records_in_region("1", 21, 30)] \
            == [21]

        # the retained record still reads its own row, not the latest one
        assert retained[CHROM] == "1"
        assert retained[POS_BEGIN] == 10
        assert retained[POS_END] == 12
        assert tuple(retained[PAYLOAD]) == ("1", "10", "12", "3.14")

        # ...and so does every record the buffer still holds
        assert [tuple(r[PAYLOAD]) for r in tab.buffer.deque] == [
            ("1", "21", "30", "5.14"),
        ]


def test_tabix_parser_is_built_once_at_open_not_per_line(
    scores_tabix_res: GenomicResource,
    mocker: pytest_mock.MockerFixture,
) -> None:
    # The row->record parser is a function of the resolved column keys, the
    # chromosome map and the zero-based flag -- all fixed for the life of the
    # table.  It is therefore built exactly once, when the table is opened,
    # and never per line.
    assert scores_tabix_res.config is not None

    spy = mocker.spy(table_tabix, "build_tabular_parser")

    with build_genomic_position_table(
        scores_tabix_res, scores_tabix_res.config["table"],
    ) as tab:
        assert spy.call_count == 1
        assert len(list(tab.get_all_records())) == 3
        assert len(list(tab.get_records_in_region("1", 11, 11))) == 1
        assert spy.call_count == 1


def test_tabix_buffers_the_terminating_record_before_the_end_check(
    scores_tabix_res: GenomicResource,
) -> None:
    # The generator that reads from tabix appends every record it pulls to the
    # buffer BEFORE it decides whether that record has run past the end of the
    # query.  The record that terminates the read is therefore buffered even
    # though it is never yielded -- and the next call's buffer window depends
    # on it being there.
    assert scores_tabix_res.config is not None

    with build_genomic_position_table(
        scores_tabix_res, scores_tabix_res.config["table"],
    ) as tab:
        assert isinstance(tab, TabixGenomicPositionTable)

        fetched = list(tab.get_records_in_region("1", 11, 11))
        assert [r[POS_BEGIN] for r in fetched] == [10]

        # 15 is past the end of the query, so it was not yielded -- but it was
        # buffered on the way out.
        assert [r[POS_BEGIN] for r in tab.buffer.deque] == [10, 15]

        # ...and that record is what stretches the buffered window over the
        # 13-14 gap, so the next call is answered from the buffer alone: no
        # second tabix fetch and no sequential seek.
        assert not list(tab.get_records_in_region("1", 13, 14))
        assert dict(tab.stats) == {
            "calls": 2,
            "with buffering": 2,
            "tabix fetch": 1,
            "yield from tabix": 1,
        }


@pytest.fixture
def gapped_tabix_res(tmp_path: pathlib.Path) -> GenomicResource:
    """A table with a wide gap between its second and third record."""
    setup_directories(
        tmp_path, {
            "genomic_resource.yaml": textwrap.dedent("""
                table:
                    format: tabix
                    filename: data.txt.gz
                scores:
                - id: c2
                  name: c2
                  type: float"""),
        })
    setup_tabix(
        tmp_path / "data.txt.gz",
        """
        #chrom pos_begin pos_end  c2
        1     10        12       3.14
        1     15        20       4.14
        1     100       110      5.14
        """, seq_col=0, start_col=1, end_col=2)
    return build_filesystem_test_resource(tmp_path)


def test_tabix_read_cascade_stats_name_the_same_paths(
    gapped_tabix_res: GenomicResource,
) -> None:
    # Walk every branch of the read cascade in one table and pin both what it
    # yields and the counters that name the branch that served it.  The
    # counters are the only window onto which branch ran, so their names -- and
    # the branch each one attributes a query to -- must not drift.
    assert gapped_tabix_res.config is not None

    with build_genomic_position_table(
        gapped_tabix_res, gapped_tabix_res.config["table"],
    ) as tab:
        assert isinstance(tab, TabixGenomicPositionTable)

        # a fresh fetch, buffered; it also buffers the terminating record (15)
        assert [
            r[POS_BEGIN] for r in tab.get_records_in_region("1", 11, 11)
        ] == [10]
        assert [r[POS_BEGIN] for r in tab.buffer.deque] == [10, 15]

        # inside the buffered window, but between two records: empty, no fetch
        assert not list(tab.get_records_in_region("1", 13, 14))

        # a buffer hit
        assert [
            r[POS_BEGIN] for r in tab.get_records_in_region("1", 16, 18)
        ] == [15]

        # past the buffer but within jump_threshold: seek forward sequentially
        # rather than re-seek the file
        assert not list(tab.get_records_in_region("1", 21, 30))
        assert [r[POS_BEGIN] for r in tab.buffer.deque] == [100]

        # the provably-empty gap: the query starts after the previous query's
        # end and ends before the first buffered record, so it is empty without
        # touching the file
        assert not list(tab.get_records_in_region("1", 40, 50))

        # a region wider than the buffer: served unbuffered, fresh from tabix
        tab.BUFFER_MAXSIZE = 1
        assert [
            r[POS_BEGIN] for r in tab.get_records_in_region("1", 1, 110)
        ] == [10, 15, 100]

        assert dict(tab.stats) == {
            "calls": 6,
            "with buffering": 5,
            "without buffering": 1,
            "tabix fetch": 2,
            "yield from tabix": 4,
            "yield from buffer": 1,
            "yield from buffer and tabix": 1,
            "sequential seek forward": 1,
            "not found": 1,
        }


def test_last_call_is_updated(tmp_path: pathlib.Path) -> None:
    setup_directories(
        tmp_path, {
            "genomic_resource.yaml": textwrap.dedent("""
                table:
                    filename: data.txt.gz
                    format: tabix
                scores:
                - id: c2
                  name: c2
                  type: float"""),
        })
    setup_tabix(
        tmp_path / "data.txt.gz",
        """
        #chrom pos_begin pos_end  c2
        1     10        12       3.14
        1     15        20       4.14
        1     21        30       5.14
        """, seq_col=0, start_col=1, end_col=2)
    res = build_filesystem_test_resource(tmp_path)
    assert res.config is not None

    with build_genomic_position_table(res, res.config["table"]) as tab_tab:
        assert isinstance(tab_tab, TabixGenomicPositionTable)
        # pylint: disable=no-member
        assert tab_tab._last_call == ("", -1, -1)
        assert [
            tuple(r[PAYLOAD])
            for r in tab_tab.get_records_in_region("1", 11, 11)
        ] == [
            ("1", "10", "12", "3.14"),
        ]
        assert tab_tab._last_call == ("1", 11, 11)
        assert not list(tab_tab.get_records_in_region("1", 13, 14))
        assert tab_tab._last_call == ("1", 13, 14)
        assert [
            tuple(r[PAYLOAD])
            for r in tab_tab.get_records_in_region("1", 18, 21)
        ] == [
            ("1", "15", "20", "4.14"),
            ("1", "21", "30", "5.14"),
        ]
        assert tab_tab._last_call == ("1", 18, 21)


def test_chr_add_pref() -> None:
    res = build_inmemory_test_resource({
        "genomic_resource.yaml": """
            table:
                filename: data.mem
                chrom_mapping:
                    add_prefix: chr
            scores:
            - id: c2
              name: c2
              type: float""",
        "data.mem": convert_to_tab_separated(
            """
            chrom pos_begin pos2  c2
            1     10        12    3.14
            X     11        11    4.14
            11    12        10    5.14
            """)})
    assert res.config is not None

    with build_genomic_position_table(res, res.config["table"]) as tab:
        assert tab.get_chromosomes() == ["chr1", "chr11", "chrX"]


def test_chr_add_pref_records_carry_the_mapped_chrom() -> None:
    # The file contigs are '1'/'X'/'11'; every record must come out under the
    # PREFIXED reference contig in its CHROM slot -- the add_prefix reverse map
    # is derived from the observed file contigs, which is the sole reason the
    # in-memory open() buffers the raw rows before building the parser.
    res = build_inmemory_test_resource({
        "genomic_resource.yaml": """
            table:
                filename: data.mem
                chrom_mapping:
                    add_prefix: chr
            scores:
            - id: c2
              name: c2
              type: float""",
        "data.mem": convert_to_tab_separated(
            """
            chrom pos_begin pos2  c2
            1     10        12    3.14
            X     11        11    4.14
            11    12        10    5.14
            """)})
    assert res.config is not None

    with build_genomic_position_table(res, res.config["table"]) as tab:
        records = list(tab.get_all_records())
        assert [record[CHROM] for record in records] == \
            ["chr1", "chr11", "chrX"]
        assert [record[POS_BEGIN] for record in records] == [10, 12, 11]

        # a region fetch is keyed by the mapped contig too
        fetched = list(tab.get_records_in_region("chrX"))
        assert len(fetched) == 1
        assert fetched[0][CHROM] == "chrX"
        assert fetched[0][POS_BEGIN] == 11

        # ...and the unmapped file contig is not a contig of the table
        with pytest.raises(ValueError, match="chromosome X"):
            list(tab.get_records_in_region("X"))


def test_chr_del_pref_records_carry_the_mapped_chrom() -> None:
    # Mirror of the add_prefix case: file contigs are 'chr1'/'chr22'/'chrX',
    # records must come out under the DE-prefixed reference contig.
    res = build_inmemory_test_resource({
        "genomic_resource.yaml": """
            table:
                filename: data.mem
                chrom_mapping:
                    del_prefix: chr
            scores:
            - id: c2
              name: c2
              type: float""",
        "data.mem": """
            chrom    pos_begin pos2  c2
            chr1     10        12    3.14
            chr22    11        11    4.14
            chrX     12        10    5.14"""})
    assert res.config is not None

    with build_genomic_position_table(res, res.config["table"]) as tab:
        records = list(tab.get_all_records())
        assert [record[CHROM] for record in records] == ["1", "22", "X"]
        assert [record[POS_BEGIN] for record in records] == [10, 11, 12]

        fetched = list(tab.get_records_in_region("22"))
        assert len(fetched) == 1
        assert fetched[0][CHROM] == "22"
        assert fetched[0][POS_BEGIN] == 11

        with pytest.raises(ValueError, match="chromosome chr22"):
            list(tab.get_records_in_region("chr22"))


def test_chr_del_pref() -> None:
    res = build_inmemory_test_resource({
        "genomic_resource.yaml": """
            table:
                filename: data.mem
                chrom_mapping:
                    del_prefix: chr
            scores:
            - id: c2
              name: c2
              type: float""",
        "data.mem": """
            chrom    pos_begin pos2  c2
            chr1     10        12    3.14
            chr22    11        11    4.14
            chrX     12        10    5.14"""})
    assert res.config is not None

    with build_genomic_position_table(res, res.config["table"]) as tab:
        assert tab.get_chromosomes() == ["1", "22", "X"]


def test_chrom_mapping_file() -> None:
    res = build_inmemory_test_resource({
        "genomic_resource.yaml": """
            table:
                filename: data.mem
                chrom_mapping:
                    filename: chrom_map.txt
                pos_end:
                    name: pos2
            scores:
            - id: c2
              name: c2
              type: float""",
        "data.mem": convert_to_tab_separated("""
            chrom    pos_begin pos2  c2
            chr1     10        12    3.14
            chr22    11        11    4.14
            chrX     12        10    5.14"""),
        "chrom_map.txt": convert_to_tab_separated("""
            chrom   file_chrom
            gosho   chr1
            pesho   chr22
        """)})
    assert res.config is not None

    with build_genomic_position_table(res, res.config["table"]) as tab:
        assert tab.get_chromosomes() == ["gosho", "pesho"]
        # Chromosome mapping now lands in the record's core CHROM slot; the
        # payload keeps the raw (file) contig, so assert the core fields.
        assert [
            (r[CHROM], r[POS_BEGIN], r[POS_END]) for r in tab.get_all_records()
        ] == [
            ("gosho", 10, 12),
            ("pesho", 11, 11),
        ]
        assert [
            (r[CHROM], r[POS_BEGIN], r[POS_END])
            for r in tab.get_records_in_region("pesho")
        ] == [
            ("pesho", 11, 11),
        ]


def test_chrom_mapping_file_with_tabix(tmp_path: pathlib.Path) -> None:
    setup_directories(
        tmp_path, {
            "genomic_resource.yaml": """
                table:
                  filename: data.txt.gz
                  format: tabix
                  chrom_mapping:
                    filename: chrom_map.txt
                  pos_end:
                    name: pos2
                scores:
                - id: c2
                  name: c2
                  type: float""",
            "chrom_map.txt": convert_to_tab_separated("""
                chrom   file_chrom
                gosho   chr1
                pesho   chr22
            """)})

    setup_tabix(
        tmp_path / "data.txt.gz",
        """
        #chrom   pos_begin  pos2   c2
        chr1     10         12     3.14
        chr22    11         11     4.14
        chrX     12         14     5.14
        """,
        seq_col=0, start_col=1, end_col=2)
    res = build_filesystem_test_resource(tmp_path)
    assert res.config is not None

    with build_genomic_position_table(res, res.config["table"]) as tab:
        assert tab.get_chromosomes() == ["gosho", "pesho"]
        assert [line[CHROM] for line in tab.get_all_records()] == \
            ["gosho", "pesho"]
        assert [line[CHROM] for line in tab.get_records_in_region("pesho")] == \
            ["pesho"]


def test_an_empty_chrom_mapping_file_maps_nothing_and_so_drops_every_record(
    tmp_path: pathlib.Path,
) -> None:
    """A chrom_mapping file with only its header maps NO contig -- so no record.

    This is the degenerate mapping: a well-formed ``chrom_mapping.filename``
    whose body is empty.  It maps nothing, and a table configured with it
    therefore *has* no chromosomes -- ``get_chromosomes()`` is empty, because
    the mapping file is the source of the table's contigs.

    A record whose file contig is absent from a configured map is dropped, and
    with an empty map that is every record.  The alternative -- treating the
    empty map as "no mapping at all" and passing the file contigs through --
    would make the table yield records on contigs it says it does not have, and
    ``get_records_in_region`` would then raise for the very contig
    ``get_all_records`` had just handed back.

    Every record backend answers this the same way: the map's *presence* is
    what selects the mapping path, not its emptiness.
    """
    mapping = convert_to_tab_separated("""
        chrom   file_chrom
    """)

    inmemory_res = build_inmemory_test_resource({
        "genomic_resource.yaml": """
            table:
                filename: data.mem
                chrom_mapping:
                    filename: chrom_map.txt
            scores:
            - id: c2
              name: c2
              type: float""",
        "data.mem": convert_to_tab_separated("""
            chrom    pos_begin pos_end  c2
            chr1     10        10       3.14"""),
        "chrom_map.txt": mapping,
    })

    setup_directories(
        tmp_path, {
            "genomic_resource.yaml": textwrap.dedent("""
                table:
                    filename: data.vcf.gz
                    format: vcf_info
                    chrom_mapping:
                        filename: chrom_map.txt
            """),
            "chrom_map.txt": mapping,
        })
    setup_vcf(
        tmp_path / "data.vcf.gz",
        textwrap.dedent("""
##fileformat=VCFv4.1
##INFO=<ID=A,Number=1,Type=Integer,Description="Score A">
#CHROM POS ID REF ALT QUAL FILTER INFO
chr1   10  .  A   T   .    .      A=1
    """),
    )
    vcf_res = build_filesystem_test_resource(tmp_path)

    for res in (inmemory_res, vcf_res):
        assert res.config is not None
        with build_genomic_position_table(res, res.config["table"]) as tab:
            assert tab.get_chromosomes() == []
            assert list(tab.get_all_records()) == []
            with pytest.raises(ValueError, match="chr1"):
                list(tab.get_records_in_region("chr1"))


def test_invalid_chrom_mapping_file_with_tabix(tmp_path: pathlib.Path) -> None:
    setup_directories(
        tmp_path, {
            "genomic_resource.yaml": """
                table:
                    filename: data.txt.gz
                    format: tabix
                    chrom_mapping:
                        filename: chrom_map.txt""",
            "chrom_map.txt": convert_to_tab_separated("""
                    something   else
                    gosho       chr1
                    pesho       chr22
            """)})
    setup_tabix(
        tmp_path / "data.txt.gz",
        """
        #chrom   pos_begin  pos2   c2
        chr1     10         12     3.14
        chr22    11         11     4.14
        chrX     12         14     5.14
        """,
        seq_col=0, start_col=1, end_col=2)
    res = build_filesystem_test_resource(tmp_path)
    assert res.config is not None

    with pytest.raises(ValueError, match="The chromosome") as exception:
        build_genomic_position_table(res, res.config["table"]).open()

    assert str(exception.value) == (
        "The chromosome mapping file chrom_map.txt in resource  "
        "is expected to have the two columns 'chrom' and 'file_chrom'"
    )


def test_column_with_name() -> None:
    res = build_inmemory_test_resource({
        "genomic_resource.yaml": """
            table:
                filename: data.mem
                pos_begin:
                    name: pos2
            scores:
            - id: c2
              name: c2
              type: float""",
        "data.mem": convert_to_tab_separated(
            """
            chrom pos pos2 c2
            1     10  12   3.14
            1     11  11   4.14
            1     12  14   5.14
            """),
    })
    assert res.config is not None

    with build_genomic_position_table(res, res.config["table"]) as tab:
        assert [
            r[PAYLOAD] for r in tab.get_records_in_region("1", 12, 12)
        ] == [
            ("1", "10", "12", "3.14"),
        ]


def test_column_with_index() -> None:
    res = build_inmemory_test_resource({
        "genomic_resource.yaml": """
            table:
                filename: data.mem
                pos_begin:
                    index: 2
            scores:
            - id: c2
              name: c2
              type: float""",
        "data.mem": convert_to_tab_separated("""
            chrom pos pos2  c2
            1     10  12    3.14
            1     11  11    4.14
            1     12  14    5.14"""),
    })
    assert res.config is not None

    with build_genomic_position_table(res, res.config["table"]) as tab:
        assert [
            r[PAYLOAD] for r in tab.get_records_in_region("1", 12, 12)
        ] == [
            ("1", "10", "12", "3.14"),
        ]


def test_no_header() -> None:
    res = build_inmemory_test_resource({
        "genomic_resource.yaml": """
            table:
                header_mode: none
                filename: data.mem
                chrom:
                    index: 0
                pos_begin:
                    index: 2
            scores:
            - id: c2
              index: 3
              type: float""",
        "data.mem": convert_to_tab_separated("""
            1   10  12  3.14
            1   11  11  4.14
            1   12  14  5.14
            """),
    })
    assert res.config is not None

    with build_genomic_position_table(res, res.config["table"]) as tab:
        assert [
            r[PAYLOAD] for r in tab.get_records_in_region("1", 12, 12)
        ] == [
            ("1", "10", "12", "3.14"),
        ]


def test_header_in_config() -> None:
    res = build_inmemory_test_resource({
        "genomic_resource.yaml": """
            table:
                header_mode: list
                header: ["chrom", "pos", "pos2", "score"]
                filename: data.mem
                pos_begin:
                    name: pos2
            scores:
            - id: c2
              name: score
              type: float""",
        "data.mem": convert_to_tab_separated("""
            1   10  12  3.14
            1   11  11  4.14
            1   12  10  5.14""")})
    assert res.config is not None

    with build_genomic_position_table(res, res.config["table"]) as tab:
        assert [
            r[PAYLOAD] for r in tab.get_records_in_region("1", 12, 12)
        ] == [
            ("1", "10", "12", "3.14"),
        ]


def test_space_in_mem_table() -> None:
    res = build_inmemory_test_resource({
        "genomic_resource.yaml": """
            table:
                filename: data.mem
            scores:
            - id: c2
              name: c2
              type: float""",
        "data.mem": convert_to_tab_separated("""
            chrom pos_begin pos2   c2
            1     10        12     3.14
            1     11        EMPTY  4.14
            1     12        10     5.14""")})
    assert res.config is not None

    with build_genomic_position_table(res, res.config["table"]) as tab:
        assert [
            r[PAYLOAD] for r in tab.get_records_in_region("1", 11, 11)
        ] == [
            ("1", "11", ".", "4.14"),
        ]


def test_text_table() -> None:
    res = build_inmemory_test_resource(
        content={
            "genomic_resource.yaml": """
                table:
                    filename: data.mem
                scores:
                - id: c2
                  name: c2
                  type: float""",
            "data.mem": convert_to_tab_separated("""
                chrom pos_begin c1     c2
                1     3         3.14   aa
                1     4         4.14   bb
                1     4         5.14   cc
                1     5         6.14   dd
                1     8         7.14   ee
                2     3         8.14   ff
                """),
        })
    assert res.config is not None

    with build_genomic_position_table(res, res.config["table"]) as table:
        assert [r[PAYLOAD] for r in table.get_all_records()] == [
            ("1", "3", "3.14", "aa"),
            ("1", "4", "4.14", "bb"),
            ("1", "4", "5.14", "cc"),
            ("1", "5", "6.14", "dd"),
            ("1", "8", "7.14", "ee"),
            ("2", "3", "8.14", "ff"),
        ]
        assert [
            r[PAYLOAD] for r in table.get_records_in_region("1", 4, 5)] == [
            ("1", "4", "4.14", "bb"),
            ("1", "4", "5.14", "cc"),
            ("1", "5", "6.14", "dd"),
        ]
        assert [
            r[PAYLOAD] for r in table.get_records_in_region("1", 4, None)] == [
            ("1", "4", "4.14", "bb"),
            ("1", "4", "5.14", "cc"),
            ("1", "5", "6.14", "dd"),
            ("1", "8", "7.14", "ee"),
        ]
        assert [
            r[PAYLOAD] for r in table.get_records_in_region("1", None, 4)] == [
            ("1", "3", "3.14", "aa"),
            ("1", "4", "4.14", "bb"),
            ("1", "4", "5.14", "cc"),
        ]
        assert not list(table.get_records_in_region("1", 20, 25))
        assert [
            r[PAYLOAD] for r in table.get_records_in_region("2", None, None)
        ] == [
            ("2", "3", "8.14", "ff"),
        ]
        with pytest.raises(ValueError, match="The chromosome 3"):
            list(table.get_records_in_region("3"))


@pytest.mark.parametrize("jump_threshold", [
    0,
    1,
    2,
    1500,
])
def test_tabix_table(tmp_path: pathlib.Path, jump_threshold: int) -> None:
    setup_directories(
        tmp_path, {
            "genomic_resource.yaml": textwrap.dedent("""
                table:
                  filename: data.txt.gz
                  format: tabix
                scores:
                - id: c1
                  name: c1
                  type: float
                - id: c2
                  name: c2
                  type: str"""),
        })

    setup_tabix(
        tmp_path / "data.txt.gz",
        """
        #chrom pos_begin c1     c2
        1      3         3.14   aa
        1      4         4.14   bb
        1      4         5.14   cc
        1      5         6.14   dd
        1      8         7.14   ee
        2      3         8.14   ff
        """, seq_col=0, start_col=1, end_col=1)
    res = build_filesystem_test_resource(tmp_path)
    assert res.config is not None

    with build_genomic_position_table(res, res.config["table"]) as table:
        cast(TabixGenomicPositionTable, table).jump_threshold = jump_threshold
        assert [tuple(r[PAYLOAD]) for r in table.get_all_records()] == [
            ("1", "3", "3.14", "aa"),
            ("1", "4", "4.14", "bb"),
            ("1", "4", "5.14", "cc"),
            ("1", "5", "6.14", "dd"),
            ("1", "8", "7.14", "ee"),
            ("2", "3", "8.14", "ff"),
        ]
        assert [
            tuple(r[PAYLOAD])
            for r in table.get_records_in_region("1", 4, 5)
        ] == [
            ("1", "4", "4.14", "bb"),
            ("1", "4", "5.14", "cc"),
            ("1", "5", "6.14", "dd"),
        ]
        assert [
            tuple(r[PAYLOAD])
            for r in table.get_records_in_region("1", 4, None)] == [
            ("1", "4", "4.14", "bb"),
            ("1", "4", "5.14", "cc"),
            ("1", "5", "6.14", "dd"),
            ("1", "8", "7.14", "ee"),
        ]
        assert [
            tuple(r[PAYLOAD])
            for r in table.get_records_in_region("1", None, 4)] == [
            ("1", "3", "3.14", "aa"),
            ("1", "4", "4.14", "bb"),
            ("1", "4", "5.14", "cc"),
        ]
        assert not list(table.get_records_in_region("1", 20, 25))
        assert [
            tuple(r[PAYLOAD])
            for r in table.get_records_in_region("2", None, None)
        ] == [
            ("2", "3", "8.14", "ff"),
        ]
        with pytest.raises(ValueError, match="The chromosome 3"):
            list(table.get_records_in_region("3"))


@pytest.fixture
def tabix_table(tmp_path: pathlib.Path) -> GenomicPositionTable:
    setup_directories(
        tmp_path, {
            "genomic_resource.yaml": """
                tabix_table:
                    filename: data.txt.gz
                    format: tabix
                scores:
                - id: c1
                  name: c1
                  type: int""",
        })
    setup_tabix(
        tmp_path / "data.txt.gz",
        """
        #chrom pos_begin c1
        1      1         1
        1      2         2
        1      3         3
        1      4         4
        1      5         5
        1      6         6
        1      7         7
        1      8         8
        1      9         9
        1      10        10
        1      11        11
        1      12        12
        """, seq_col=0, start_col=1, end_col=1)
    res = build_filesystem_test_resource(tmp_path)
    assert res.config is not None

    table = build_genomic_position_table(res, res.config["tabix_table"])
    table.open()
    return table


@pytest.fixture
def regions_tabix_table(tmp_path: pathlib.Path) -> GenomicPositionTable:
    setup_directories(
        tmp_path, {
            "genomic_resource.yaml": """
                tabix_table:
                    filename: data.txt.gz
                    format: tabix
                scores:
                - id: c1
                  name: c1
                  type: int""",
            "data.mem": """
            """,
        })
    setup_tabix(
        tmp_path / "data.txt.gz",
        """
        #chrom pos_begin pos_end  c1
        1      1         5        1
        1      6         10       2
        1      11        15       3
        1      16        20       4
        1      21        25       5
        1      26        30       6
        """, seq_col=0, start_col=1, end_col=2)
    res = build_filesystem_test_resource(tmp_path)
    assert res.config is not None

    table = build_genomic_position_table(res, res.config["tabix_table"])
    table.open()
    return table


def test_tabix_table_should_use_sequential_seek_forward(
        tabix_table: GenomicPositionTable) -> None:
    table = cast(TabixGenomicPositionTable, tabix_table)

    assert not table._should_use_sequential_seek_forward("1", 1)
    for row in table.get_records_in_region("1", 1, 1):
        print(row)

    assert not table._should_use_sequential_seek_forward("1", 1)

    assert table._should_use_sequential_seek_forward("1", 2)
    assert table._should_use_sequential_seek_forward("1", 3)

    table.jump_threshold = 0
    assert not table._should_use_sequential_seek_forward("1", 3)


def test_regions_tabix_table_should_use_sequential_seek_forward(
    regions_tabix_table: TabixGenomicPositionTable,
) -> None:
    table = regions_tabix_table

    assert not table._should_use_sequential_seek_forward("1", 1)
    for row in table.get_records_in_region("1", 2, 2):
        print(row)

    assert not table._should_use_sequential_seek_forward("1", 1)
    assert not table._should_use_sequential_seek_forward("1", 6)
    assert table._should_use_sequential_seek_forward("1", 11)
    assert table._should_use_sequential_seek_forward("1", 21)

    table.jump_threshold = 0
    assert not table._should_use_sequential_seek_forward("1", 6)
    assert not table._should_use_sequential_seek_forward("1", 11)
    assert not table._should_use_sequential_seek_forward("1", 21)


def test_tabix_table_jumper_current_position(
        tabix_table: TabixGenomicPositionTable) -> None:
    table = tabix_table

    for rec in table.get_records_in_region("1", 1):
        assert rec[CHROM] == "1"
        assert rec[POS_BEGIN] == 1
        break

    for rec in table.get_records_in_region("1", 6):
        assert rec[CHROM] == "1", rec
        assert rec[POS_BEGIN] == 6, rec
        break


@pytest.fixture
def tabix_table_multiline(tmp_path: pathlib.Path) -> GenomicPositionTable:
    setup_directories(
        tmp_path, {
            "genomic_resource.yaml": """
                tabix_table:
                    filename: data.txt.gz
                    format: tabix
                scores:
                - id: c1
                  name: c1
                  type: float""",
        })
    setup_tabix(
        tmp_path / "data.txt.gz",
        """
        #chrom pos_begin c1
        1      1         1
        1      2         2
        1      2         3
        1      3         4
        1      3         5
        1      4         6
        1      4         7
        """, seq_col=0, start_col=1, end_col=1)
    res = build_filesystem_test_resource(tmp_path)
    assert res.config is not None

    table = build_genomic_position_table(res, res.config["tabix_table"])
    table.open()
    return table


@pytest.mark.parametrize("pos_beg,pos_end,expected", [
    (1, 1, [("1", "1", "1")]),
    (2, 2, [("1", "2", "2"), ("1", "2", "3")]),
    (3, 3, [("1", "3", "4"), ("1", "3", "5")]),
    (4, 4, [("1", "4", "6"), ("1", "4", "7")]),
    (3, 4, [("1", "3", "4"), ("1", "3", "5"),
            ("1", "4", "6"), ("1", "4", "7")]),
])
def test_tabix_table_multi_get_regions(
        tabix_table_multiline: TabixGenomicPositionTable,
        pos_beg: int, pos_end: int, expected: list[tuple[str, ...]]) -> None:
    table = tabix_table_multiline
    assert not table._should_use_sequential_seek_forward("1", 1)
    assert [
        tuple(r[PAYLOAD])
        for r in table.get_records_in_region("1", pos_beg, pos_end)
    ] == expected


def test_tabix_table_multi_get_regions_partial(
        tabix_table_multiline: TabixGenomicPositionTable) -> None:
    table = tabix_table_multiline

    assert not table._should_use_sequential_seek_forward("1", 1)
    for row in table.get_records_in_region("1", 1, 1):
        print(row)

    for index, row in enumerate(table.get_records_in_region("1", 3, 3)):
        print(row)
        if index == 1:
            break
    assert [
        tuple(r[PAYLOAD]) for r in table.get_records_in_region("1", 3, 3)
    ] == [
        ("1", "3", "4"), ("1", "3", "5"),
    ]


def test_tabix_middle_optimization(tmp_path: pathlib.Path) -> None:
    setup_directories(
        tmp_path, {
            "genomic_resource.yaml": """
                tabix_table:
                    filename: data.txt.gz
                    format: tabix
                scores:
                - id: c1
                  name: c1
                  type: int""",
            "data.mem": """
            """,
        })
    setup_tabix(
        tmp_path / "data.txt.gz",
        """
        #chrom pos_begin c1
        1      1         1
        1      4         2
        1      4         3
        1      8         4
        1      8         5
        1      12        6
        1      12        7
        """, seq_col=0, start_col=1, end_col=1)
    res = build_filesystem_test_resource(tmp_path)
    assert res.config is not None

    with build_genomic_position_table(res, res.config["tabix_table"]) as table:

        row = None
        for row in table.get_records_in_region("1", 1, 1):
            assert tuple(row[PAYLOAD]) == ("1", "1", "1")
            break
        assert row is not None
        assert tuple(row[PAYLOAD]) == ("1", "1", "1")

        row = None
        for row in table.get_records_in_region("1", 1, 1):
            assert tuple(row[PAYLOAD]) == ("1", "1", "1")
        assert row is not None
        assert tuple(row[PAYLOAD]) == ("1", "1", "1")


def test_tabix_middle_optimization_regions(tmp_path: pathlib.Path) -> None:
    setup_directories(
        tmp_path, {
            "genomic_resource.yaml": """
                tabix_table:
                    filename: data.txt.gz
                    format: tabix
                scores:
                - id: c1
                  name: c1
                  type: int""",
        })

    setup_tabix(
        tmp_path / "data.txt.gz",
        """
        #chrom pos_begin pos_end  c1
        1      1         1        1
        1      4         8        2
        1      9         12       3
        """, seq_col=0, start_col=1, end_col=2)
    res = build_filesystem_test_resource(tmp_path)
    assert res.config is not None

    with build_genomic_position_table(res, res.config["tabix_table"]) as table:
        row = None
        for row in table.get_records_in_region("1", 1, 1):
            assert tuple(row[PAYLOAD]) == ("1", "1", "1", "1")
            break

        row = None
        for row in table.get_records_in_region("1", 1, 1):
            assert tuple(row[PAYLOAD]) == ("1", "1", "1", "1")

        row = None
        for row in table.get_records_in_region("1", 4, 4):  # noqa: B007
            pass
        assert row is not None
        assert tuple(row[PAYLOAD]) == ("1", "4", "8", "2")

        row = None
        for row in table.get_records_in_region("1", 4, 4):  # noqa: B007
            break
        assert row is not None
        assert tuple(row[PAYLOAD]) == ("1", "4", "8", "2")

        row = None
        for row in table.get_records_in_region("1", 5, 5):  # noqa: B007
            break
        assert row is not None
        assert tuple(row[PAYLOAD]) == ("1", "4", "8", "2")


def test_tabix_middle_optimization_regions_buggy_1(
    tmp_path: pathlib.Path,
    mocker: pytest_mock.MockerFixture,
) -> None:
    setup_directories(
        tmp_path, {
            "genomic_resource.yaml": """
                tabix_table:
                    filename: data.txt.gz
                    format: tabix
                    chrom_mapping:
                        add_prefix: chr
                scores:
                - id: c1
                  name: c1
                  type: float
            """,
        })

    setup_tabix(
        tmp_path / "data.txt.gz",
        """
        #chrom pos_begin pos_end  c1
        1      505636    505636   0.006
        1      505637    505637   0.009
        1      505638    505638   0.011
        1      505639    505639   0.013
        1      505640    505641   0.014
        1      505642    505642   0.013
        1      505643    505643   0.012
        1      505644    505645   0.006
        1      505646    505646   0.005
        1      505755    505757   0.004
        1      505758    505758   0.003
        1      505759    505761   0.001
        1      505762    505764   0.002
        """, seq_col=0, start_col=1, end_col=2)
    res = build_filesystem_test_resource(tmp_path)
    assert res.config is not None

    with build_genomic_position_table(res, res.config["tabix_table"]) as table:
        # We need to spy on _gen_from_buffer_and_tabix to assert that buffering
        # and sequential seeking are working correctly when contigs are
        # remapped
        mocker.spy(TabixGenomicPositionTable, "_gen_from_buffer_and_tabix")
        assert [
            tuple(r[PAYLOAD])
            for r in table.get_records_in_region("chr1", 505637, 505637)
        ] == [
            ("1", "505637", "505637", "0.009"),
        ]
        assert TabixGenomicPositionTable \
            ._gen_from_buffer_and_tabix.call_count == 0  # type: ignore

        assert [
            tuple(r[PAYLOAD])
            for r in table.get_records_in_region("chr1", 505643, 505646)
        ] == [
            ("1", "505643", "505643", "0.012"),
            ("1", "505644", "505645", "0.006"),
            ("1", "505646", "505646", "0.005"),
        ]
        assert TabixGenomicPositionTable \
            ._gen_from_buffer_and_tabix.call_count == 1  # type: ignore

        assert [
            tuple(r[PAYLOAD])
            for r in table.get_records_in_region("chr1", 505762, 505762)
        ] == [
            ("1", "505762", "505764", "0.002"),
        ]
        assert TabixGenomicPositionTable \
            ._gen_from_buffer_and_tabix.call_count == 2  # type: ignore


def test_buggy_fitcons_e67(tmp_path: pathlib.Path) -> None:
    setup_directories(
        tmp_path, {
            "genomic_resource.yaml": """
                tabix_table:
                    filename: data.txt.gz
                    format: tabix
                scores:
                - id: c1
                  name: c1
                  type: float
            """,
        })
    setup_tabix(
        tmp_path / "data.txt.gz",
        """
        #chrom  pos_begin  pos_end    c1
        5       180739426  180742735  0.065122
        5       180742736  180742736  0.156342
        5       180742737  180742813  0.327393
        """, seq_col=0, start_col=1, end_col=2)
    res = build_filesystem_test_resource(tmp_path)
    assert res.config is not None

    with build_genomic_position_table(res, res.config["tabix_table"]) as table:
        assert [
            tuple(r[PAYLOAD])
            for r in table.get_records_in_region("5", 180740299, 180740300)
        ] == [("5", "180739426", "180742735", "0.065122")]

        assert [
            tuple(r[PAYLOAD])
            for r in table.get_records_in_region("5", 180740301, 180740301)
        ] == [("5", "180739426", "180742735", "0.065122")]


@pytest.mark.parametrize("jump_threshold,expected", [
    ("none", 0),
    ("1", 1),
    ("1500", 1500),
])
def test_tabix_jump_config(
        tmp_path: pathlib.Path, jump_threshold: str, expected: int) -> None:
    setup_directories(
        tmp_path, {
            "genomic_resource.yaml": f"""
                tabix_table:
                    filename: data.txt.gz
                    format: tabix
                    jump_threshold: {jump_threshold}
                scores:
                - id: c1
                  name: c1
                  type: float
            """,
        })
    setup_tabix(
        tmp_path / "data.txt.gz",
        """
        #chrom  pos_begin  pos_end    c1
        5       180739426  180742735  0.065122
        5       180742736  180742736  0.156342
        5       180742737  180742813  0.327393
        """, seq_col=0, start_col=1, end_col=2)
    res = build_filesystem_test_resource(tmp_path)
    assert res.config is not None

    with build_genomic_position_table(res, res.config["tabix_table"]) as table:
        assert cast(TabixGenomicPositionTable, table).jump_threshold == \
            expected
        assert [
            tuple(r[PAYLOAD])
            for r in table.get_records_in_region("5", 180740299, 180740300)
        ] == [("5", "180739426", "180742735", "0.065122")]
        assert [
            tuple(r[PAYLOAD])
            for r in table.get_records_in_region("5", 180740301, 180740301)
        ] == [("5", "180739426", "180742735", "0.065122")]


@pytest.mark.parametrize("buffer_maxsize,jump_threshold", [
    (1, 0),
    (2, 1),
    (8, 4),
    (10_000, 2_500),
    (20_000, 2_500),
])
def test_tabix_max_buffer(
        tmp_path: pathlib.Path,
        buffer_maxsize: int, jump_threshold: int) -> None:
    setup_directories(
        tmp_path, {
            "genomic_resource.yaml": f"""
                tabix_table:
                    filename: data.txt.gz
                    format: tabix
                    jump_threshold: {jump_threshold}
                scores:
                - id: c1
                  name: c1
                  type: float
            """,
        })

    setup_tabix(
        tmp_path / "data.txt.gz",
        """
        #chrom  pos_begin  pos_end    c1
        5       180739426  180742735  0.065122
        5       180742736  180742736  0.156342
        5       180742737  180742813  0.327393
        """, seq_col=0, start_col=1, end_col=2)
    res = build_filesystem_test_resource(tmp_path)
    assert res.config is not None

    TabixGenomicPositionTable.BUFFER_MAXSIZE = buffer_maxsize

    with build_genomic_position_table(res, res.config["tabix_table"]) as table:
        assert isinstance(table, TabixGenomicPositionTable)

        # pylint: disable=no-member
        assert buffer_maxsize == table.BUFFER_MAXSIZE
        assert table.jump_threshold == jump_threshold
        assert [
            tuple(r[PAYLOAD])
            for r in table.get_records_in_region("5", 180740299, 180740300)
        ] == [("5", "180739426", "180742735", "0.065122")]
        assert [
            tuple(r[PAYLOAD])
            for r in table.get_records_in_region("5", 180740301, 180740301)
        ] == [("5", "180739426", "180742735", "0.065122")]
        assert [
            tuple(r[PAYLOAD])
            for r in table.get_records_in_region("5", 180740301, 180742735)
        ] == [("5", "180739426", "180742735", "0.065122")]


def test_contig_length() -> None:
    res = build_inmemory_test_resource({
        "genomic_resource.yaml": """
            table:
                filename: data.mem
            scores:
            - id: c2
              name: c2
              type: float""",
        "data.mem": """
            chrom pos_begin pos2  c2
            1     10        12    3.14
            1     11        11    4.14
            1     12        10    5.14
            1     12        11    6.13
            2     1         2     0"""})
    assert res.config is not None

    with build_genomic_position_table(res, res.config["table"]) as tab:
        assert tab.get_chromosome_length("1") >= 13
        assert tab.get_chromosome_length("2") >= 2


def test_contig_length_tabix_table(
        tabix_table: TabixGenomicPositionTable) -> None:
    assert tabix_table.get_chromosome_length("1") >= 13


def test_vcf_autodetect_format(
        vcf_res_autodetect_format: GenomicResource) -> None:
    assert vcf_res_autodetect_format.config is not None

    with build_genomic_position_table(
        vcf_res_autodetect_format,
        vcf_res_autodetect_format.config["tabix_table"],
    ) as tab:
        assert isinstance(tab, VCFGenomicPositionTable)
        assert len(tuple(tab.get_all_records())) == 1


def test_vcf_yields_records_paired_with_an_allele_index(
    vcf_res: GenomicResource,
) -> None:
    # The VCF backend is on the record contract: it yields the same six-slot
    # plain tuple every other record backend does.  What is VCF-specific is the
    # PAYLOAD: a VCF record explodes one variant record into one record per ALT
    # allele, so the payload is the variant record **paired with the allele
    # index** that says which of its alleles this record stands for.  The header
    # metadata the INFO lookup needs is reachable from the variant record
    # itself (``variant.header.info``), so the payload carries nothing else.
    assert vcf_res.config is not None

    with build_genomic_position_table(
        vcf_res, vcf_res.config["tabix_table"],
    ) as tab:
        assert isinstance(tab, VCFGenomicPositionTable)
        assert tab.yields_records is True

        record = next(iter(tab.get_all_records()))

        # A record is a PLAIN tuple -- not a subclass with attributes bolted on.
        assert type(record) is tuple
        assert len(record) == RECORD_SLOTS

        assert record[CHROM] == "chr1"
        assert record[POS_BEGIN] == 5
        assert record[POS_END] == 5
        assert record[REF] == "A"
        assert record[ALT] == "T"

        variant, allele_index = record[PAYLOAD]
        assert isinstance(variant, pysam.VariantRecord)
        assert allele_index == 0
        # header metadata: derived from the record, not carried beside it
        assert isinstance(variant.header.info, pysam.VariantHeaderMetadata)
        assert isinstance(variant.info, pysam.VariantRecordInfo)


def test_vcf_get_all_records(vcf_res: GenomicResource) -> None:
    assert vcf_res.config is not None

    with build_genomic_position_table(
        vcf_res, vcf_res.config["tabix_table"],
    ) as tab:
        assert isinstance(tab, VCFGenomicPositionTable)

        results = tuple(tab.get_all_records())
        assert len(results) == 3

        assert [
            (r[CHROM], r[POS_BEGIN], r[POS_END], r[REF], r[ALT])
            for r in results
        ] == [
            ("chr1", 5, 5, "A", "T"),
            ("chr1", 15, 15, "A", "T"),
            ("chr1", 30, 30, "A", "T"),
        ]


def test_vcf_records_of_one_variant_differ_by_their_allele_index(
    vcf_res_repeated_alt: GenomicResource,
) -> None:
    # An allele -- not a variant record -- is what a VCF record stands for, and
    # the allele index in its payload is the only thing that always says which
    # one.  Usually the ALT slot proxies for it, but when a variant repeats an
    # ALT ("A -> T,T") the two records agree in all five decoded slots and share
    # the very same variant record: the allele index is then the ONLY thing that
    # tells them apart -- and they carry different per-allele (Number=A) scores.
    assert vcf_res_repeated_alt.config is not None

    with build_genomic_position_table(
        vcf_res_repeated_alt, vcf_res_repeated_alt.config["tabix_table"],
    ) as tab:
        first, second = tuple(tab.get_all_records())

        # Same variant record object, same decoded slots...
        assert first[:PAYLOAD] == second[:PAYLOAD]
        assert first[PAYLOAD][VARIANT] is second[PAYLOAD][VARIANT]
        # ...distinguished by the allele index alone.
        assert first[PAYLOAD][ALLELE_INDEX] == 0
        assert second[PAYLOAD][ALLELE_INDEX] == 1


def test_vcf_records_are_ordered_through_sort_key(
    vcf_res_same_locus: GenomicResource,
) -> None:
    # VCF records are records, so the record contract's ordering rule applies to
    # them unchanged: sort them through ``record.sort_key``, never as bare
    # tuples.  Two *variant records* at one locus with the same REF and the same
    # (missing) ALT tie on all five decoded slots, so a bare sort walks into the
    # PAYLOAD -- where a pysam.VariantRecord has no order at all.  This is the
    # data-dependent trap sort_key exists for, and the VCF payload is one more
    # unorderable thing in that slot.
    assert vcf_res_same_locus.config is not None

    with build_genomic_position_table(
        vcf_res_same_locus, vcf_res_same_locus.config["tabix_table"],
    ) as tab:
        records = list(tab.get_all_records())

    assert len(records) == 3

    with pytest.raises((TypeError, NotImplementedError)):
        sorted(records)

    # sort_key projects the decoded slots and stops at the payload -- the one
    # supported way to order any record, VCF ones included.
    assert [
        record[POS_BEGIN] for record in sorted(records, key=sort_key)
    ] == [5, 5, 9]


def test_vcf_record_carries_the_mapped_contig(
    vcf_res_chrom_mapping: GenomicResource,
) -> None:
    # The mapped (reference) contig is resolved by the parser and laid down in
    # the record's CHROM slot -- the same slot, meaning the same thing, as in
    # every other backend's records.  That is what lets the inherited buffer and
    # read cascade window a VCF record without knowing it is one.
    assert vcf_res_chrom_mapping.config is not None

    with build_genomic_position_table(
        vcf_res_chrom_mapping, vcf_res_chrom_mapping.config["tabix_table"],
    ) as tab:
        record = next(iter(tab.get_all_records()))

        assert record[CHROM] == "1"
        # the file contig is still reachable, from the payload's variant record
        assert record[PAYLOAD][VARIANT].contig == "chr1"


def test_vcf_header_load_silences_htslib_index_probe(
        tmp_path: pathlib.Path,
        mocker: pytest_mock.MockerFixture) -> None:
    """Loading an index-less VCF header must not leak htslib stderr noise.

    Real VCF ``allele_score`` resources (e.g. dbSNP) ship a header-only
    ``*.header.vcf.gz`` with no accompanying ``.tbi``.  Opening it makes
    htslib auto-probe for an index and log ``[E::idx_find_and_load]`` to
    stderr.  ``_load_vcf_header`` only reads ``header.info`` and never
    fetches, so it must bracket the open with ``pysam.set_verbosity(0)``.
    """
    setup_directories(
        tmp_path, {
            "genomic_resource.yaml": textwrap.dedent("""
                tabix_table:
                    filename: data.vcf.gz
                    format: vcf_info
            """),
        })
    setup_vcf(
        tmp_path / "data.vcf.gz",
        textwrap.dedent("""
##fileformat=VCFv4.1
##INFO=<ID=A,Number=1,Type=Integer,Description="Score A">
##contig=<ID=chr1>
#CHROM POS ID REF ALT QUAL FILTER INFO
chr1   5   .  A   T   .    .      A=1
    """),
    )
    # setup_vcf indexes the header; real score resources ship it without an
    # index, so drop the .tbi to exercise the header-only path.
    (tmp_path / "data.header.vcf.gz.tbi").unlink()

    res = build_filesystem_test_resource(tmp_path)
    assert res.config is not None

    spy = mocker.spy(pysam, "set_verbosity")

    with build_genomic_position_table(
        res, res.config["tabix_table"],
    ) as tab:
        assert isinstance(tab, VCFGenomicPositionTable)
        # the header was read despite the missing index
        assert "A" in set(tab.header.keys())

    # the header open is bracketed by set_verbosity(0) ... set_verbosity(prev)
    assert mocker.call(0) in spy.call_args_list


def test_vcf_get_records_in_region(vcf_res: GenomicResource) -> None:
    assert vcf_res.config is not None

    with build_genomic_position_table(
        vcf_res, vcf_res.config["tabix_table"],
    ) as tab:
        assert not tuple(tab.get_records_in_region("chr1", 1, 4))
        assert not tuple(tab.get_records_in_region("chr1", 6, 14))
        assert not tuple(tab.get_records_in_region("chr1", 31, 42))

        def regions(*args: int) -> list[tuple]:
            return [
                (r[CHROM], r[POS_BEGIN], r[POS_END])
                for r in tab.get_records_in_region("chr1", *args)
            ]

        assert regions(1, 6) == [("chr1", 5, 5)]
        assert regions(14, 31) == [("chr1", 15, 15), ("chr1", 30, 30)]
        assert regions(4, 30) == [
            ("chr1", 5, 5), ("chr1", 15, 15), ("chr1", 30, 30)]


def test_vcf_record_payload_reaches_the_info_and_its_metadata(
    vcf_res: GenomicResource,
) -> None:
    # Everything the INFO lookup needs is reachable from the record: the INFO
    # itself off the payload's variant record, and the metadata that types it
    # off that same variant's header.  Nothing is carried beside the record --
    # which is what lets the score layer keep ONE VCF score line class instead
    # of an object per line.  (The lookup itself lives in VCFScoreLine; this
    # only pins that a record is enough to perform it.)
    assert vcf_res.config is not None

    with build_genomic_position_table(
        vcf_res, vcf_res.config["tabix_table"],
    ) as tab:
        results = tuple(tab.get_all_records())
        assert len(results) == 3

        expected_all = [
            {"A": 1, "B": None, "C": ("c11", "c12"), "D": ("d11",)},
            {"A": 2, "B": 21, "C": ("c21",), "D": ("d21", "d22")},
            {"A": 3, "B": 31, "C": ("c21",), "D": ("d31", "d32")},
        ]
        for expected, record in zip(expected_all, results, strict=True):
            variant = record[PAYLOAD][VARIANT]
            for score in "A", "B", "C", "D":
                assert variant.info.get(score) == \
                    expected[score]  # type: ignore
                # the header metadata, derived from the record itself
                assert variant.header.info.get(score).number is not None


def test_vcf_jump_ahead_optimization_use_sequential(
        vcf_res: GenomicResource) -> None:
    """
    Jump-ahead optimization test, use sequential case.

    First fetch gives us the following lines in the buffer:
    # chr1 5 5
    # chr1 15 15
    We set jump threshold to 6 and request region:
    # chr1 20 35

    Distance between last line in buffer and requested region is:
    # (20 - 15) == 5 < jump_threshold

    Therefore it should sequentially seek forward
    """
    assert vcf_res.config is not None

    with build_genomic_position_table(
        vcf_res, vcf_res.config["tabix_table"],
    ) as tab:
        cast(TabixGenomicPositionTable, tab).jump_threshold = 6
        assert isinstance(tab, VCFGenomicPositionTable)

        # pylint: disable=no-member
        assert tab.stats == {}
        tuple(tab.get_records_in_region("chr1", 1, 6))
        assert len(tab.buffer.deque) == 2
        assert tab.stats["yield from tabix"] == 1
        tuple(tab.get_records_in_region("chr1", 20, 35))
        assert len(tab.buffer.deque) == 1
        assert tab.stats["sequential seek forward"] == 1


def test_vcf_jump_ahead_optimization_use_jump(
        vcf_res: GenomicResource) -> None:
    """
    Jump-ahead optimization test, use jump case.

    Same as previous test, but the jump threshold is now set to 5
    Distance between last line in buffer and requested region is:
    # (20 - 15) == 5 == jump_threshold

    Therefore it should use the jump optimization
    """
    assert vcf_res.config is not None

    with build_genomic_position_table(
        vcf_res, vcf_res.config["tabix_table"],
    ) as tab:
        cast(TabixGenomicPositionTable, tab).jump_threshold = 5
        assert isinstance(tab, VCFGenomicPositionTable)

        # pylint: disable=no-member
        assert tab.stats == {}
        tuple(tab.get_records_in_region("chr1", 1, 6))
        assert tab.stats["yield from tabix"] == 1
        assert len(tab.buffer.deque) == 2

        tuple(tab.get_records_in_region("chr1", 20, 35))
        assert tab.stats["sequential seek forward"] == 0
        assert tab.stats["yield from tabix"] == 2
        assert len(tab.buffer.deque) == 1


def test_vcf_multiallelic(vcf_res_multiallelic: GenomicResource) -> None:
    """Test multiallelic variants are read as separate lines.

    Check that each line has proper allele indices.
    """
    assert vcf_res_multiallelic.config is not None

    with build_genomic_position_table(
        vcf_res_multiallelic,
        vcf_res_multiallelic.config["tabix_table"],
    ) as tab:
        assert isinstance(tab, VCFGenomicPositionTable)

        results = [
            (r[CHROM], r[POS_BEGIN], r[POS_END])
            for r in tab.get_all_records()
        ]
        assert results == [
            ("chr1", 2, 2),
            ("chr1", 5, 5),
            ("chr1", 15, 15),
            ("chr1", 15, 15),
            ("chr1", 30, 30),
            ("chr1", 30, 30),
            ("chr1", 30, 30),
        ]


def test_vcf_multiallelic_region(
        vcf_res_multiallelic: GenomicResource) -> None:
    """Same as previous test, but for a given region."""
    assert vcf_res_multiallelic.config is not None
    with build_genomic_position_table(
        vcf_res_multiallelic,
        vcf_res_multiallelic.config["tabix_table"],
    ) as tab:
        assert isinstance(tab, VCFGenomicPositionTable)

        results = [
            (r[CHROM], r[POS_BEGIN], r[POS_END])
            for r in tab.get_records_in_region("chr1", 14, 15)
        ]
        assert results == [
            ("chr1", 15, 15),
            ("chr1", 15, 15),
        ]


def test_vcf_multiallelic_records_carry_their_allele_index(
        vcf_res_multiallelic: GenomicResource) -> None:
    """One record per ALT allele, each carrying the allele it stands for.

    The allele index is what the INFO lookup selects a per-allele (Number=A) or
    per-allele-plus-reference (Number=R) value with -- see VCFScoreLine, and
    test_genomic_scores.py, which pins the values those numbers resolve to.
    A variant with no ALT ('.') yields ONE record, with a null allele index.
    """
    assert vcf_res_multiallelic.config is not None
    with build_genomic_position_table(
        vcf_res_multiallelic,
        vcf_res_multiallelic.config["tabix_table"],
    ) as tab:
        assert isinstance(tab, VCFGenomicPositionTable)

        results = [
            (r[CHROM], r[POS_BEGIN], r[POS_END], r[REF], r[ALT],
             r[PAYLOAD][ALLELE_INDEX])
            for r in tab.get_all_records()
        ]

        # chrom start stop ref alt allele_index
        assert results == [
            ("chr1", 2, 2, "A", None, None),   # no ALT -> null allele index
            ("chr1", 5, 5, "A", "T", 0),
            ("chr1", 15, 15, "A", "T", 0),
            ("chr1", 15, 15, "A", "G", 1),
            ("chr1", 30, 30, "A", "T", 0),
            ("chr1", 30, 30, "A", "G", 1),
            ("chr1", 30, 30, "A", "C", 2),
        ]


def test_get_ref_alt_nonconfigured_missing(tmp_path: pathlib.Path) -> None:
    setup_directories(
        tmp_path, {
            "genomic_resource.yaml": """
                tabix_table:
                  filename: data.txt.gz
                  format: tabix
                scores:
                - id: c2
                  name: c2
                  type: float
                  na_values:
                  - "4.14"
                  - "5.14"
            """,
        })

    setup_tabix(
        tmp_path / "data.txt.gz",
        """
        #chrom  pos_begin  pos_end    c2
        1     10        12       3.14
        1     15        20       4.14
        1     21        30       5.14
        """, seq_col=0, start_col=1, end_col=2)
    res = build_filesystem_test_resource(tmp_path)
    assert res.config is not None
    with build_genomic_position_table(res, res.config["tabix_table"]) as tab:
        results = [
            (r[REF], r[ALT])
            for r in tab.get_all_records()
        ]
        assert results == [
            (None, None),
            (None, None),
            (None, None),
        ]


def test_get_ref_alt_nonconfigured_existing(tmp_path: pathlib.Path) -> None:
    setup_directories(
        tmp_path, {
            "genomic_resource.yaml": """
                tabix_table:
                    filename: data.txt.gz
                    format: tabix
                scores:
                - id: c2
                  name: c2
                  type: float
                  na_values:
                  - "4.14"
                  - "5.14"
            """,
        })
    setup_tabix(
        tmp_path / "data.txt.gz",
        """
        #chrom  pos_begin  pos_end  ref  alt    c2
        1     10        12       A      G       3.14
        1     15        20       A      T       4.14
        1     21        30       A      C       5.14
        """, seq_col=0, start_col=1, end_col=2)
    res = build_filesystem_test_resource(tmp_path)
    assert res.config is not None

    with build_genomic_position_table(res, res.config["tabix_table"]) as tab:
        results = [
            (r[REF], r[ALT])
            for r in tab.get_all_records()
        ]
        assert results == [
            (None, None),
            (None, None),
            (None, None),
        ]


def test_get_ref_alt_configured_existing(tmp_path: pathlib.Path) -> None:
    setup_directories(
        tmp_path, {
            "genomic_resource.yaml": """
                tabix_table:
                    filename: data.txt.gz
                    format: tabix
                    reference:
                      name: reference
                    alternative:
                      name: alternative
                scores:
                - id: c2
                  name: c2
                  type: float
            """,
        })
    setup_tabix(
        tmp_path / "data.txt.gz",
        """
        #chrom  pos_begin  pos_end  reference  alternative    c2
        1     10        12       A      G       3.14
        1     15        20       A      T       4.14
        1     21        30       A      C       5.14
        """, seq_col=0, start_col=1, end_col=2)
    res = build_filesystem_test_resource(tmp_path)
    assert res.config is not None

    with build_genomic_position_table(res, res.config["tabix_table"]) as tab:
        results = [
            (r[REF], r[ALT])
            for r in tab.get_all_records()
        ]
        assert results == [
            ("A", "G"),
            ("A", "T"),
            ("A", "C"),
        ]


def test_get_ref_alt_by_index_on_no_header(tmp_path: pathlib.Path) -> None:
    setup_directories(
        tmp_path, {
            "genomic_resource.yaml": """
                tabix_table:
                    filename: data.txt.gz
                    format: tabix
                    header_mode: none
                    chrom:
                      index: 0
                    pos_begin:
                      index: 1
                    pos_end:
                      index: 2
                    reference:
                      index: 3
                    alternative:
                      index: 4
                scores:
                - id: c2
                  index: 5
                  type: float
            """,
        })
    setup_tabix(
        tmp_path / "data.txt.gz",
        """
        #chrom  pos_begin  pos_end  reference  alternative    c2
        1     10        12       A      G       3.14
        1     15        20       A      T       4.14
        1     21        30       A      C       5.14
        """, seq_col=0, start_col=1, end_col=2)
    res = build_filesystem_test_resource(tmp_path)
    assert res.config is not None

    with build_genomic_position_table(res, res.config["tabix_table"]) as tab:
        results = [
            (r[REF], r[ALT])
            for r in tab.get_all_records()
        ]
        assert results == [
            ("A", "G"),
            ("A", "T"),
            ("A", "C"),
        ]


def test_vcf_get_missing_alt(vcf_res_multiallelic: GenomicResource) -> None:
    # A variant whose ALT is absent ('.') stands for the reference allele: it
    # explodes into exactly ONE record, whose ALT slot is null and whose payload
    # carries a null allele index (which is how VCFScoreLine knows to read a
    # Number=R field at its reference offset).
    assert vcf_res_multiallelic.config is not None

    with build_genomic_position_table(
        vcf_res_multiallelic, vcf_res_multiallelic.config["tabix_table"],
    ) as tab:
        assert isinstance(tab, VCFGenomicPositionTable)

        no_alt_record = next(tab.get_all_records())
        assert no_alt_record is not None

        assert no_alt_record[REF] == "A"
        assert no_alt_record[ALT] is None
        assert no_alt_record[PAYLOAD][ALLELE_INDEX] is None


def test_overlapping_nonattribute_columns_config(
        tmp_path: pathlib.Path) -> None:
    setup_directories(
        tmp_path, {
            "genomic_resource.yaml": """
                table:
                  filename: data.txt.gz
                  format: tabix
                  header_mode: none
                  chrom:
                    index: 0
                  pos_begin:
                    index: 1
                  pos_end:
                    index: 1
                  reference:
                    index: 2
                  alternative:
                    index: 3
                scores:
                  - id: raw
                    index: 4
                    type: float
                    desc: "raw"
                  - id: phred
                    index: 5
                    type: float
                    desc: "phred"
            """,
        })
    setup_tabix(
        tmp_path / "data.txt.gz",
        """
        1       101   A       C       0.123        1
        1       102   A       C       0.456        2
        1       103   A       C       0.789        3
        """, seq_col=0, start_col=1, end_col=1)
    res = build_filesystem_test_resource(tmp_path)
    assert res.config is not None

    with build_genomic_position_table(res, res.config["table"]) as tab:
        results = [
            (r[PAYLOAD][4], r[PAYLOAD][5])
            for r in tab.get_all_records()
        ]
        assert results == [
            ("0.123", "1"),
            ("0.456", "2"),
            ("0.789", "3"),
        ]


def test_vcf_get_chromosomes(vcf_res: GenomicResource) -> None:
    assert vcf_res.config is not None

    with build_genomic_position_table(
        vcf_res, vcf_res.config["tabix_table"],
    ) as tab:
        assert tab.get_chromosomes() == ["chr1"]


def test_tabix_table_zero_based(tmp_path: pathlib.Path) -> None:
    setup_directories(
        tmp_path, {
            "genomic_resource.yaml": textwrap.dedent("""
                table:
                  filename: data.txt.gz
                  format: tabix
                  zero_based: True
                scores:
                - id: c1
                  name: c1
                  type: float
            """),
        })

    setup_tabix(
        tmp_path / "data.txt.gz",
        """
        #chrom pos_begin c1
        1      3         3.14
        1      4         4.14
        1      4         5.14
        1      5         6.14
        1      8         7.14
        """, seq_col=0, start_col=1, end_col=1, zerobased=True)
    res = build_filesystem_test_resource(tmp_path)
    assert res.config is not None

    with build_genomic_position_table(res, res.config["table"]) as table:
        assert [
            (r[CHROM], r[POS_BEGIN], r[POS_END], r[PAYLOAD][2])
            for r in table.get_all_records()
        ] == [
            ("1", 4, 4, "3.14"),
            ("1", 5, 5, "4.14"),
            ("1", 5, 5, "5.14"),
            ("1", 6, 6, "6.14"),
            ("1", 9, 9, "7.14"),
        ]
        assert [
            (r[CHROM], r[POS_BEGIN], r[POS_END], r[PAYLOAD][2])
            for r in table.get_records_in_region("1", 4, 5)] == [
            ("1", 4, 4, "3.14"),
            ("1", 5, 5, "4.14"),
            ("1", 5, 5, "5.14"),
        ]
        assert [
            (r[CHROM], r[POS_BEGIN], r[POS_END], r[PAYLOAD][2])
            for r in table.get_records_in_region("1", 6, None)
        ] == [
            ("1", 6, 6, "6.14"),
            ("1", 9, 9, "7.14"),
        ]
        assert [
            (r[CHROM], r[POS_BEGIN], r[POS_END], r[PAYLOAD][2])
            for r in table.get_records_in_region("1", None, 4)
        ] == [
            ("1", 4, 4, "3.14"),
        ]


def test_tabix_table_zero_based_headerless(tmp_path: pathlib.Path) -> None:
    setup_directories(
        tmp_path, {
            "genomic_resource.yaml": textwrap.dedent("""
                table:
                  filename: data.txt.gz
                  format: tabix
                  header_mode: none
                  zero_based: True
                  chrom:
                    index: 0
                  pos_begin:
                    index: 1
                  pos_end:
                    index: 1
            """),
        })

    setup_tabix(
        tmp_path / "data.txt.gz",
        """
        #chrom pos_begin c1
        1      3         3.14
        1      4         4.14
        1      4         5.14
        1      5         6.14
        1      8         7.14
        """, seq_col=0, start_col=1, end_col=1, zerobased=True)
    res = build_filesystem_test_resource(tmp_path)
    assert res.config is not None

    with build_genomic_position_table(res, res.config["table"]) as table:
        assert len(list(table.get_all_records())) == 5


def test_new_score_configuration_fields() -> None:
    res = build_inmemory_test_resource({
        "genomic_resource.yaml": """
            table:
                filename: data.mem
                header_mode: list
                header: ["chrom", "pos", "pos2", "score", "score2"]
                pos_begin:
                    column_name: pos
                pos_end:
                    column_index: 2
            """,
        "data.mem": convert_to_tab_separated(
            """
            1     10        12    6.28  3.14
            """)})
    assert res.config is not None

    with build_genomic_position_table(res, res.config["table"]) as table:
        assert table.get_column_key("pos_begin") == 1
        assert table.get_column_key("pos_end") == 2


def test_tabix_get_records_in_region_without_chrom(
    tabix_table: GenomicPositionTable,
) -> None:
    table = cast(TabixGenomicPositionTable, tabix_table)
    res = list(table.get_records_in_region())
    assert len(res) == 12
