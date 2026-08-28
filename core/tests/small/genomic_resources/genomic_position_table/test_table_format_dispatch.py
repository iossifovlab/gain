# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""How ``build_genomic_position_table`` picks a backend (gain#348).

Two independent inputs choose the backend: the ``filename`` suffix and an
explicit ``format:`` key.  They used to disagree -- the suffix branch matched
one bigWig spelling exactly (``.bw``), the explicit branch matched two in any
case (``bw``/``bigwig``) -- so a resource named with UCSC's other spelling and
no ``format:`` was routed to the in-memory text parser.  These tests pin the
one rule that now governs both: the suffix is matched case-insensitively, over
the same vocabulary the explicit key accepts.
"""
import pathlib

import pytest
from gain.genomic_resources.genomic_position_table.record import (
    CHROM,
    POS_BEGIN,
    POS_END,
)
from gain.genomic_resources.genomic_position_table.table_bigwig import (
    BigWigTable,
)
from gain.genomic_resources.genomic_position_table.table_tabix import (
    TabixGenomicPositionTable,
)
from gain.genomic_resources.genomic_position_table.utils import (
    build_genomic_position_table,
)
from gain.genomic_resources.repository import GenomicResource
from gain.genomic_resources.testing import (
    build_filesystem_test_repository,
    build_filesystem_test_resource,
    setup_directories,
    setup_gzip,
    setup_tabix,
)
from gain.genomic_resources.testing.builders import (
    a_bigwig_score,
)


@pytest.mark.parametrize("filename", [
    "data.bw",
    "data.BW",
    "data.bigwig",
    "data.bigWig",
    "data.BIGWIG",
])
def test_bigwig_suffix_spelling_autodetected_without_format(
    tmp_path: pathlib.Path, filename: str,
) -> None:
    resource: GenomicResource = (
        a_bigwig_score()
        .with_filename(filename)
        .build_resource(tmp_path)
    )
    assert resource.config is not None

    with build_genomic_position_table(
            resource, resource.config["table"]) as table:
        assert isinstance(table, BigWigTable)
        assert len(tuple(table.get_all_records())) == 3


@pytest.mark.parametrize("table_format", ["bw", "bigwig", "BW", "bigWig"])
def test_explicit_format_wins_over_a_suffix_mapping_elsewhere(
    tmp_path: pathlib.Path, table_format: str,
) -> None:
    """The suffix supplies a DEFAULT; a stated ``format:`` overrides it.

    ``data.txt`` autodetects to the in-memory ``tsv`` backend, which cannot
    read this file at all -- so a table that opens and yields the bigWig's
    records proves the explicit key, not the suffix, chose the backend.
    """
    resource: GenomicResource = (
        a_bigwig_score()
        .with_filename("data.txt")
        .with_format(table_format)
        .build_resource(tmp_path)
    )
    assert resource.config is not None

    with build_genomic_position_table(
            resource, resource.config["table"]) as table:
        assert isinstance(table, BigWigTable)
        assert len(tuple(table.get_all_records())) == 3


def test_uppercase_csv_suffix_selects_the_csv_backend(
    tmp_path: pathlib.Path,
) -> None:
    """Case-insensitivity is one rule, not a bigWig special case.

    The ``csv`` backend splits on commas; ``mem`` -- what an unrecognised
    suffix falls back to -- splits on whitespace and would see each line as a
    single column.  Reading the columns back apart is what says which backend
    was picked.
    """
    setup_directories(tmp_path, {
        "genomic_resource.yaml": """
            table:
                filename: DATA.CSV""",
        "DATA.CSV":
            "chrom,pos_begin,pos_end,c2\n1,10,12,3.14\n1,11,11,4.14\n",
    })
    res = build_filesystem_test_resource(tmp_path)
    assert res.config is not None

    with build_genomic_position_table(res, res.config["table"]) as table:
        records = list(table.get_all_records())

    assert [(rec[CHROM], rec[POS_BEGIN], rec[POS_END]) for rec in records] == [
        ("1", 10, 12), ("1", 11, 11),
    ]


def test_binary_payload_reaching_the_mem_backend_names_the_misconfiguration(
    tmp_path: pathlib.Path,
) -> None:
    """A binary file in the text parser is always a misconfiguration.

    The suffix rule no longer routes a *correctly named* bigWig here, but an
    unrecognised suffix still falls back to ``mem``, and nothing about the
    decision is checked against the payload.  What the diagnostic has to carry
    is the three facts that turn the failure into a one-line config fix: which
    resource, which file, and which format was chosen for it.

    ``UnicodeDecodeError`` is itself a ``ValueError``, so the raised type is
    asserted rather than merely caught -- ``pytest.raises(ValueError)`` alone
    would pass against the unfixed code.
    """
    setup_directories(tmp_path, {
        "grr.yaml": f"""
            id: test_grr
            type: directory
            directory: {tmp_path!s}""",
        "one_score": {
            "genomic_resource.yaml": """
                table:
                    filename: data.dat""",
        },
    })
    # bigWig magic, i.e. the payload the reporter arrived with -- any
    # non-decodable bytes reproduce it.
    (tmp_path / "one_score" / "data.dat").write_bytes(
        b"\x26\xfc\x8f\x88\x04\x00\x00\x00")

    res = build_filesystem_test_repository(tmp_path).get_resource("one_score")
    assert res.config is not None

    with pytest.raises(ValueError) as exc_info:
        build_genomic_position_table(res, res.config["table"]).open()

    assert type(exc_info.value) is ValueError
    message = str(exc_info.value)
    assert "one_score" in message
    assert "data.dat" in message
    assert "mem" in message


def test_uppercase_bgz_suffix_selects_the_tabix_backend(
    tmp_path: pathlib.Path,
) -> None:
    setup_directories(tmp_path, {
        "genomic_resource.yaml": """
            table:
                filename: DATA.BGZ""",
    })
    setup_tabix(
        tmp_path / "data.txt.gz", """
            #chrom pos_begin pos_end c2
            1      10        12      3.14
            1      11        11      4.14
        """, seq_col=0, start_col=1, end_col=2)
    # setup_tabix hard-codes the ``.gz`` name pysam writes; the suffix under
    # test is ``.BGZ``, so move the pair onto it.
    (tmp_path / "data.txt.gz").rename(tmp_path / "DATA.BGZ")
    (tmp_path / "data.txt.gz.tbi").rename(tmp_path / "DATA.BGZ.tbi")

    res = build_filesystem_test_resource(tmp_path)
    assert res.config is not None

    with build_genomic_position_table(res, res.config["table"]) as table:
        assert isinstance(table, TabixGenomicPositionTable)
        assert len(list(table.get_all_records())) == 2


def test_uppercase_gz_suffix_is_still_decompressed(
    tmp_path: pathlib.Path,
) -> None:
    """The suffix rule and the decompression check must agree too.

    Routing ``.TXT.GZ`` to the ``tsv`` backend only helps if that backend then
    recognises the file as gzipped -- its own ``.gz`` test was case-sensitive
    in exactly the same way, so an upper-case name reached the text parser as
    raw deflate bytes.
    """
    setup_directories(tmp_path, {
        "genomic_resource.yaml": """
            table:
                filename: DATA.TXT.GZ""",
    })
    setup_gzip(tmp_path / "data.txt.gz", """
        chrom pos_begin pos_end c2
        1     10        12      3.14
        1     11        11      4.14
    """)
    (tmp_path / "data.txt.gz").rename(tmp_path / "DATA.TXT.GZ")

    res = build_filesystem_test_resource(tmp_path)
    assert res.config is not None

    with build_genomic_position_table(res, res.config["table"]) as table:
        records = list(table.get_all_records())

    assert [(rec[CHROM], rec[POS_BEGIN], rec[POS_END]) for rec in records] == [
        ("1", 10, 12), ("1", 11, 11),
    ]
