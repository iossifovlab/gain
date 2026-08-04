"""A tabix table is refused when its index and its config disagree.

Refused on the column the table *resolves* to, whether the configuration
spells that column out or the table falls back to it: what the index has to
agree with is the column the records are actually read through.

The fixtures here are built with the low-level ``setup_tabix`` helper rather
than with the fluent builders on purpose: the builders derive the tabix index
columns from the very header the config is rendered from, so a
builder-authored fixture cannot express the disagreement this module is about
(gain#553).  ``setup_tabix`` forwards ``seq_col``/``start_col``/``end_col``
straight into ``pysam.tabix_index``, with no consistency check -- which is what
makes a malformed resource constructible at all.
"""
# pylint: disable=C0116
import logging
import pathlib
import struct
import textwrap
from typing import Any, cast

import pysam
import pytest
from gain.genomic_resources.cli import cli_manage
from gain.genomic_resources.genomic_position_table import (
    build_genomic_position_table,
    table_tabix,
)
from gain.genomic_resources.genomic_position_table.index_columns import (
    parse_index_columns,
)
from gain.genomic_resources.repository import GenomicResource
from gain.genomic_resources.resource_errors import MalformedResourceError
from gain.genomic_resources.testing import (
    build_filesystem_test_protocol,
    setup_directories,
    setup_tabix,
)

RESOURCE_ID = "two_ends_score"

# One record, two candidate end columns: the WIDE one an index can be built
# from and the NARROW one a config can point ``pos_end`` at.  A query at
# 1:500-600 overlaps the wide span and not the narrow one, which is the
# read-path symptom -- a record fetched and then silently dropped -- that the
# refusal replaces.
#
# The header deliberately names neither column ``pos_end``: with no such
# column and no configuration, ``pos_end`` resolves to the ``pos_begin``
# column, so every column key in play here has to be stated rather than
# inherited from the header.
TWO_ENDS_DATA = """
#chrom  pos_begin  wide_end  narrow_end  s
1       10         1000      20          0.5
"""


# A header that spells the begin column ``pos_beg``, so that nothing --
# neither the configuration nor the header -- states which column
# ``pos_begin`` is: the table falls back to its hardcoded default of column 1
# while the index was built over column 2.  The table then reads the CHROMOSOME
# column as a begin position, and a region query is filtered on a column it
# never reads.  Making no claim is not the same as making a right one.
UNSTATED_BEGIN_DATA = """
#name  chrom  pos_beg  pos_end  s
v1     1      10       1000     0.5
"""


# The same two-ended records, spread over a dozen long contig names.  A
# ``.csi`` index carries its column configuration in an auxiliary block that
# ENDS with the contig names, so the length of that block is a property of the
# file's contigs -- which is what this data is here to make non-trivial.
MANY_CONTIGS_DATA = "#chrom  pos_begin  wide_end  narrow_end  s\n" + "\n".join(
    f"a_contig_with_a_fairly_long_name_{index:02d}  10  1000  20  0.5"
    for index in range(1, 13)
)


def build_resource(
    tmp_path: pathlib.Path, config: str,
    data: str = TWO_ENDS_DATA, **index_columns: Any,
) -> GenomicResource:
    """Build a one-resource repository from a config and index columns."""
    root = tmp_path / RESOURCE_ID
    setup_directories(root, {
        "genomic_resource.yaml": textwrap.dedent(config),
    })
    setup_tabix(root / "data.txt.gz", data, **index_columns)
    return build_filesystem_test_protocol(tmp_path).get_resource(RESOURCE_ID)


def a_config(table_body: str) -> str:
    return f"""
        type: position_score
        table:
            filename: data.txt.gz
            format: tabix
        {table_body}
        scores:
        - id: s
          name: s
          type: float
        """


def test_pos_end_column_disagreeing_with_the_index_is_refused(
    tmp_path: pathlib.Path,
) -> None:
    resource = build_resource(
        tmp_path,
        a_config("""    pos_end:
              column_index: 3"""),
        seq_col=0, start_col=1, end_col=2)
    table = build_genomic_position_table(resource, resource.config["table"])

    with pytest.raises(MalformedResourceError) as excinfo:
        table.open()

    assert (
        "pos_end is indexed on column 2 but the configuration resolves it "
        "to column 3"
    ) in str(excinfo.value)


def test_the_refusal_names_the_resource(tmp_path: pathlib.Path) -> None:
    resource = build_resource(
        tmp_path,
        a_config("""    pos_end:
              column_index: 3"""),
        seq_col=0, start_col=1, end_col=2)
    table = build_genomic_position_table(resource, resource.config["table"])

    with pytest.raises(MalformedResourceError) as excinfo:
        table.open()

    assert RESOURCE_ID in str(excinfo.value)


@pytest.mark.parametrize("field,indexed", [("chrom", 0), ("pos_begin", 1)])
def test_every_core_column_is_compared_against_the_index(
    tmp_path: pathlib.Path, field: str, indexed: int,
) -> None:
    resource = build_resource(
        tmp_path,
        a_config(f"""    {field}:
              column_index: 3"""),
        seq_col=0, start_col=1, end_col=2)
    table = build_genomic_position_table(resource, resource.config["table"])

    with pytest.raises(MalformedResourceError) as excinfo:
        table.open()

    assert (
        f"{field} is indexed on column {indexed} but the configuration "
        f"resolves it to column 3"
    ) in str(excinfo.value)


def test_every_disagreeing_field_is_named_not_just_the_first(
    tmp_path: pathlib.Path,
) -> None:
    # The remedy is a per-field config edit, so a refusal that named only the
    # first disagreement would send a reader back for another open to find
    # the next one.  Two fields are put out of step at once -- ``chrom`` and
    # ``pos_end``, with ``pos_begin`` left agreeing -- and BOTH clauses have
    # to be present.  Without this, truncating the mismatch list to its first
    # entry goes undetected.
    resource = build_resource(
        tmp_path,
        a_config("""    chrom:
              column_index: 3
            pos_end:
              column_index: 3"""),
        seq_col=0, start_col=1, end_col=2)
    table = build_genomic_position_table(resource, resource.config["table"])

    with pytest.raises(MalformedResourceError) as excinfo:
        table.open()

    message = str(excinfo.value)
    assert (
        "chrom is indexed on column 0 but the configuration resolves it "
        "to column 3"
    ) in message
    assert (
        "pos_end is indexed on column 2 but the configuration resolves it "
        "to column 3"
    ) in message


def test_no_end_column_note_is_absent_when_pos_end_agrees(
    tmp_path: pathlib.Path,
) -> None:
    # The "no end column" note is about ``pos_end`` alone.  Here the index
    # records no end column (``col_end == 0``) but the table's ``pos_end``
    # resolves to the same implied column, so the only disagreement is
    # ``chrom`` -- and a note about the end column would be telling a reader
    # to go and look at a field that is not what is wrong.  Without this, a
    # refusal that always carried the note goes undetected.
    resource = build_resource(
        tmp_path,
        a_config("""    chrom:
              column_index: 3"""),
        preset="vcf")
    table = build_genomic_position_table(resource, resource.config["table"])

    with pytest.raises(MalformedResourceError) as excinfo:
        table.open()

    message = str(excinfo.value)
    assert (
        "chrom is indexed on column 0 but the configuration resolves it "
        "to column 3"
    ) in message
    assert "The index records no end column" not in message


def test_an_index_with_no_end_column_is_compared_by_its_begin_column(
    tmp_path: pathlib.Path,
) -> None:
    # The ``vcf`` preset builds an index that records no end column at all
    # (``col_end == 0``), which means every record ends where it begins.  A
    # config declaring a distinct ``pos_end`` disagrees with that just as much
    # as it disagrees with a wrong end column, and is refused the same way.
    resource = build_resource(
        tmp_path,
        a_config("""    pos_end:
              column_index: 2"""),
        preset="vcf")
    table = build_genomic_position_table(resource, resource.config["table"])

    with pytest.raises(MalformedResourceError) as excinfo:
        table.open()

    message = str(excinfo.value)
    assert (
        "pos_end is indexed on column 1 but the configuration resolves it "
        "to column 2"
    ) in message
    assert "The index records no end column" in message


def build_unstated_begin_table(
    tmp_path: pathlib.Path,
) -> table_tabix.TabixGenomicPositionTable:
    resource = build_resource(
        tmp_path, a_config(""), data=UNSTATED_BEGIN_DATA,
        seq_col=1, start_col=2, end_col=3)
    return cast(
        table_tabix.TabixGenomicPositionTable,
        build_genomic_position_table(resource, resource.config["table"]))


def test_a_column_the_configuration_never_states_is_refused_too(
    tmp_path: pathlib.Path,
) -> None:
    # What the table READS a coordinate through is what the index has to
    # agree with, and a coordinate nothing states is read through a hardcoded
    # fallback just as surely as one that is spelled out.  Here the index
    # filters on column 2 while the table reads pos_begin from column 1 --
    # exactly the index-vs-read split this rule exists to end -- so the
    # absence of a claim is no reason to let it through.
    table = build_unstated_begin_table(tmp_path)

    with pytest.raises(MalformedResourceError) as excinfo:
        table.open()

    assert (
        "pos_begin is indexed on column 2 but the configuration resolves it "
        "to column 1"
    ) in str(excinfo.value)


def test_a_csi_index_is_validated_the_same_way(
    tmp_path: pathlib.Path,
) -> None:
    resource = build_resource(
        tmp_path,
        a_config("""    pos_end:
              column_index: 3"""),
        seq_col=0, start_col=1, end_col=2, csi=True)
    table = build_genomic_position_table(resource, resource.config["table"])

    with pytest.raises(MalformedResourceError) as excinfo:
        table.open()

    assert "data.txt.gz.csi" in str(excinfo.value)
    assert (
        "pos_end is indexed on column 2 but the configuration resolves it "
        "to column 3"
    ) in str(excinfo.value)


def test_a_csi_index_over_many_contigs_is_still_decoded(
    tmp_path: pathlib.Path,
) -> None:
    # The columns sit in FRONT of the contig names in a csi's auxiliary block,
    # so a fixed-size read reaches them however many contigs the file has.  A
    # reader that insisted on the whole block would decline this index -- and
    # decline it exactly where the file is big enough to want a csi.
    resource = build_resource(
        tmp_path,
        a_config("""    pos_end:
              column_index: 3"""),
        data=MANY_CONTIGS_DATA,
        seq_col=0, start_col=1, end_col=2, csi=True)
    table = build_genomic_position_table(resource, resource.config["table"])

    with pytest.raises(MalformedResourceError) as excinfo:
        table.open()

    assert (
        "pos_end is indexed on column 2 but the configuration resolves it "
        "to column 3"
    ) in str(excinfo.value)


@pytest.mark.parametrize("csi", [False, True], ids=["tbi", "csi"])
def test_a_table_configured_over_the_indexed_columns_opens(
    tmp_path: pathlib.Path, csi: bool,
) -> None:
    resource = build_resource(
        tmp_path,
        a_config("""    pos_end:
              column_index: 2"""),
        seq_col=0, start_col=1, end_col=2, csi=csi)
    table = build_genomic_position_table(resource, resource.config["table"])

    with table.open() as opened:
        assert opened.get_chromosomes() == ["1"]


def test_the_pinned_index_is_the_one_validated(
    tmp_path: pathlib.Path,
) -> None:
    # The sibling ``data.txt.gz.tbi`` agrees with the configuration; the
    # pinned index does not.  A check that re-derived the index name from the
    # data file would validate the sibling and let this resource through.
    resource = build_resource(
        tmp_path,
        """
        table:
            filename: data.txt.gz
            index_filename: pinned.txt.gz.tbi
            format: tabix
            pos_end:
              column_index: 2
        scores:
        - id: s
          name: s
          type: float
        """,
        seq_col=0, start_col=1, end_col=2)
    setup_tabix(
        tmp_path / RESOURCE_ID / "pinned.txt.gz", TWO_ENDS_DATA,
        seq_col=0, start_col=1, end_col=3)

    table = build_genomic_position_table(resource, resource.config["table"])

    with pytest.raises(MalformedResourceError) as excinfo:
        table.open()

    assert "pinned.txt.gz.tbi" in str(excinfo.value)


def test_grr_manage_reports_the_refusal_as_one_attributed_line(
    tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture,
) -> None:
    # The point of raising a ``MalformedResourceError`` -- a ``ValueError``,
    # and so one of the CLI's resource errors -- rather than any exception of
    # its own: the repository-wide command blames the resource on one line and
    # keeps going, instead of reporting an internal defect with a traceback.
    build_resource(
        tmp_path,
        a_config("""    pos_end:
              column_index: 3"""),
        seq_col=0, start_col=1, end_col=2)

    with caplog.at_level(logging.INFO, logger="grr_manage"), \
            pytest.raises(SystemExit) as excinfo:
        cli_manage(["repo-repair", "-R", str(tmp_path), "-j", "1"])

    assert excinfo.value.code != 0
    assert RESOURCE_ID in caplog.text
    assert "is indexed on column 2" in caplog.text
    assert "unexpected internal error" not in caplog.text
    assert [
        record.name for record in caplog.records
        if record.exc_info is not None
    ] == []


@pytest.mark.parametrize("header,reason", [
    (b"BAI\x01" + b"\x00" * 60, "an index of another kind"),
    (b"TBI\x01" + b"\x00" * 8, "a header cut short"),
    (b"CSI\x01" + struct.pack("<iii", 14, 8, 4) + b"\x00" * 4,
     "a csi carrying no tabix columns"),
])
def test_an_index_this_decoder_cannot_read_yields_no_columns(
    header: bytes, reason: str,
) -> None:
    assert parse_index_columns(header) is None, reason


def test_an_index_this_decoder_cannot_read_is_declined_out_loud(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Passing such a resource in silence is the one outcome that is not
    # acceptable: it would look fixed while checking nothing.  The table opens
    # -- an index this decoder cannot read is not evidence of a malformed
    # resource -- but says so.
    resource = build_resource(
        tmp_path,
        a_config("""    pos_end:
              column_index: 2"""),
        seq_col=0, start_col=1, end_col=2)
    monkeypatch.setattr(
        table_tabix, "parse_index_columns", lambda _header: None)
    table = build_genomic_position_table(resource, resource.config["table"])

    with caplog.at_level(logging.WARNING), table.open() as opened:
        assert opened.get_chromosomes() == ["1"]

    assert any(
        "data.txt.gz.tbi" in record.message and RESOURCE_ID in record.message
        for record in caplog.records
    ), caplog.text


def test_the_refused_table_leaves_no_open_handle(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    resource = build_resource(
        tmp_path,
        a_config("""    pos_end:
              column_index: 3"""),
        seq_col=0, start_col=1, end_col=2)
    handles: list[pysam.TabixFile] = []
    open_tabix_file = resource.open_tabix_file

    def spy(*args: Any, **kwargs: Any) -> pysam.TabixFile:
        handle = open_tabix_file(*args, **kwargs)
        handles.append(handle)
        return handle

    monkeypatch.setattr(resource, "open_tabix_file", spy)
    table = build_genomic_position_table(resource, resource.config["table"])

    with pytest.raises(MalformedResourceError):
        table.open()

    assert len(handles) == 1
    assert handles[0].closed
    assert table.pysam_file is None
