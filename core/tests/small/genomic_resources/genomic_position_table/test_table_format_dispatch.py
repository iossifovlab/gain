# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""How ``build_genomic_position_table`` picks a backend (gain#348).

Two independent inputs choose the backend: the ``filename`` suffix and an
explicit ``format:`` key.  They used to disagree -- the suffix branch matched
one bigWig spelling exactly (``.bw``), the explicit branch matched two in any
case (``bw``/``bigwig``) -- so a resource named with UCSC's other spelling and
no ``format:`` was routed to the in-memory text parser.  These tests pin the
rule the suffix branch now follows: every suffix is matched
case-insensitively, and the bigWig arm accepts both spellings the explicit
key accepts.

The two branches are NOT fully symmetric even so, and these tests do not
claim they are: the explicit branch still exact-matches its non-bigWig
values, so ``format: TSV`` remains an error.  Widening what ``format:``
accepts is out of scope for gain#348 -- what had to stop was the two
branches disagreeing about the same *file*.
"""
import gzip
import pathlib
import textwrap

import pytest
from gain.genomic_resources.genomic_position_table.record import (
    CHROM,
    POS_BEGIN,
    POS_END,
)
from gain.genomic_resources.genomic_position_table.table_bigwig import (
    BigWigTable,
)
from gain.genomic_resources.genomic_position_table.table_inmemory import (
    InmemoryGenomicPositionTable,
)
from gain.genomic_resources.genomic_position_table.table_tabix import (
    TabixGenomicPositionTable,
)
from gain.genomic_resources.genomic_position_table.table_vcf import (
    VCFGenomicPositionTable,
)
from gain.genomic_resources.genomic_position_table.utils import (
    build_genomic_position_table,
)
from gain.genomic_resources.repository import GenomicResource
from gain.genomic_resources.testing import (
    build_filesystem_test_repository,
    build_filesystem_test_resource,
    setup_directories,
    setup_tabix,
    setup_vcf,
)
from gain.genomic_resources.testing.builders import (
    a_bigwig_score,
)


def _assert_dispatches_to_bigwig(resource: GenomicResource) -> None:
    """Assert the resource's table is a bigWig one that reads its records.

    Reading the records matters as much as the type: the in-memory backend
    this used to be routed to fails only when a row is pulled, so a table
    that is merely *built* proves less than one that is read.
    """
    assert resource.config is not None
    with build_genomic_position_table(
            resource, resource.config["table"]) as table:
        assert isinstance(table, BigWigTable)
        assert len(tuple(table.get_all_records())) == 3


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
    _assert_dispatches_to_bigwig(
        a_bigwig_score().with_filename(filename).build_resource(tmp_path))


@pytest.mark.parametrize("table_format", ["bw", "bigwig", "BW", "bigWig"])
def test_explicit_format_wins_over_a_suffix_mapping_elsewhere(
    tmp_path: pathlib.Path, table_format: str,
) -> None:
    """The suffix supplies a DEFAULT; a stated ``format:`` overrides it.

    ``data.txt`` autodetects to the in-memory ``tsv`` backend, which cannot
    read this file at all -- so a table that opens and yields the bigWig's
    records proves the explicit key, not the suffix, chose the backend.
    """
    _assert_dispatches_to_bigwig(
        a_bigwig_score()
        .with_filename("data.txt")
        .with_format(table_format)
        .build_resource(tmp_path))


# The last column carries a SPACE, which is what makes each case below
# discriminating rather than decorative.  ``tsv``/``csv`` split on their one
# separator and read it as a single field; ``mem`` -- the fallback an
# unrecognised suffix lands on -- splits on any whitespace, so it sees a fifth
# column and dies on "Inconsistent number of columns".  Without the space,
# ``mem`` parses these fixtures identically and the tests pass either way.
_TAB_ROWS = "chrom\tpos_begin\tpos_end\tc2\n1\t10\t12\t3 14\n1\t11\t11\t4 14\n"
_COMMA_ROWS = "chrom,pos_begin,pos_end,c2\n1,10,12,3 14\n1,11,11,4 14\n"


@pytest.mark.parametrize(("filename", "rows", "gzipped"), [
    ("DATA.TXT", _TAB_ROWS, False),
    ("DATA.TSV", _TAB_ROWS, False),
    ("DATA.CSV", _COMMA_ROWS, False),
    ("DATA.TXT.GZ", _TAB_ROWS, True),
    ("DATA.TSV.GZ", _TAB_ROWS, True),
    ("DATA.CSV.GZ", _COMMA_ROWS, True),
])
def test_uppercase_text_suffix_selects_its_separator_backend(
    tmp_path: pathlib.Path, filename: str, rows: str, *, gzipped: bool,
) -> None:
    """Case-insensitivity is one rule, not a bigWig special case.

    The ``.GZ`` cases additionally pin that the suffix rule and the in-memory
    backend's own decompression check AGREE: routing ``.TXT.GZ`` to ``tsv``
    only helps if that backend then recognises the file as gzipped, and its
    ``.gz`` test was case-sensitive in exactly the same way.  Both halves have
    to be lowered -- either one alone leaves these red.
    """
    path = tmp_path / filename
    if gzipped:
        with gzip.open(path, "wt") as outfile:
            outfile.write(rows)
    else:
        path.write_text(rows)
    setup_directories(tmp_path, {
        "genomic_resource.yaml": f"""
            table:
                filename: {filename}""",
    })
    res = build_filesystem_test_resource(tmp_path)
    assert res.config is not None

    with build_genomic_position_table(res, res.config["table"]) as table:
        records = list(table.get_all_records())

    assert [(rec[CHROM], rec[POS_BEGIN], rec[POS_END]) for rec in records] == [
        ("1", 10, 12), ("1", 11, 11),
    ]


def test_explicit_format_can_also_send_a_bigwig_suffix_to_the_text_parser(
    tmp_path: pathlib.Path,
) -> None:
    """Precedence runs both ways: the new bigWig default is still a DEFAULT.

    The complement of the override test above, and the direction the widened
    suffix rule could plausibly have broken -- a ``.bw``-named text file whose
    config states ``format: mem`` must still reach the in-memory backend.
    """
    setup_directories(tmp_path, {
        "genomic_resource.yaml": """
            table:
                filename: data.BW
                format: mem""",
        "data.BW": "chrom pos_begin pos_end c2\n1 10 12 3.14\n1 11 11 4.14\n",
    })
    res = build_filesystem_test_resource(tmp_path)
    assert res.config is not None

    with build_genomic_position_table(res, res.config["table"]) as table:
        assert isinstance(table, InmemoryGenomicPositionTable)
        assert len(list(table.get_all_records())) == 2


_HEADER_FROM_FILE_TABLE = """
                table:
                    filename: data.dat"""

# ``header_mode: none`` skips the header scan entirely, so the FIRST row this
# backend pulls is in the record loop rather than the header loop.  The two
# loops decode separately, and a fixture that only ever trips the header one
# leaves the record loop's diagnostic unexercised.
_HEADERLESS_TABLE = """
                table:
                    filename: data.dat
                    header_mode: none
                    chrom:
                        column_index: 0
                    pos_begin:
                        column_index: 1
                    pos_end:
                        column_index: 2"""


@pytest.mark.parametrize("table_config", [
    pytest.param(_HEADER_FROM_FILE_TABLE, id="header-read-from-file"),
    pytest.param(_HEADERLESS_TABLE, id="headerless"),
])
def test_binary_payload_reaching_the_mem_backend_names_the_misconfiguration(
    tmp_path: pathlib.Path, table_config: str,
) -> None:
    """A binary file in the text parser is always a misconfiguration.

    The suffix rule no longer routes a *correctly named* bigWig here, but an
    unrecognised suffix still falls back to ``mem``, and nothing about the
    decision is checked against the payload.  What the diagnostic has to carry
    is the three facts that turn the failure into a one-line config fix: which
    resource, which file, and which format was chosen for it.

    Run against both header modes because they fail in different loops --
    see ``_HEADERLESS_TABLE``.

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
            "genomic_resource.yaml": table_config,
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


def test_uppercase_vcf_gz_suffix_selects_the_vcf_backend(
    tmp_path: pathlib.Path,
) -> None:
    """Routing a suffix somewhere it then crashes is not resolving it.

    The VCF backend derives its header sidecar by splitting the filename at
    ``.vcf``, which was case-sensitive in the same way the dispatch was -- so
    lowering the suffix rule alone would hand ``.VCF.GZ`` to a backend that
    dies in its constructor with a bare ``substring not found``, naming
    neither the resource nor the file.  That is the gain#348 failure mode
    relocated, not fixed.
    """
    setup_directories(tmp_path, {
        "genomic_resource.yaml": """
            table:
                filename: DATA.VCF.GZ""",
    })
    setup_vcf(tmp_path / "data.vcf.gz", textwrap.dedent("""
##fileformat=VCFv4.1
##INFO=<ID=A,Number=1,Type=Integer,Description="Score A">
#CHROM POS ID REF ALT QUAL FILTER  INFO
chr1   5   .  A   T   .    .       A=1
chr1   15  .  A   T   .    .       A=2
    """))
    # setup_vcf writes the pair under the lower-case names it derives itself;
    # the suffix under test is ``.VCF.GZ``, so move all four onto it.
    for src, dst in (
        ("data.vcf.gz", "DATA.VCF.GZ"),
        ("data.vcf.gz.tbi", "DATA.VCF.GZ.tbi"),
        ("data.header.vcf.gz", "DATA.header.VCF.GZ"),
        ("data.header.vcf.gz.tbi", "DATA.header.VCF.GZ.tbi"),
    ):
        (tmp_path / src).rename(tmp_path / dst)

    res = build_filesystem_test_resource(tmp_path)
    assert res.config is not None

    with build_genomic_position_table(res, res.config["table"]) as table:
        assert isinstance(table, VCFGenomicPositionTable)
        assert len(list(table.get_all_records())) == 2
