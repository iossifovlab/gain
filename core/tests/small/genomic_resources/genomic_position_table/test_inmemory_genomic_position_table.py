# pylint: disable=W0621,C0114,C0116,W0212,W0613,too-many-lines

import pathlib

import pytest
from gain.genomic_resources.genomic_position_table import (
    ContigExtent,
    build_genomic_position_table,
)
from gain.genomic_resources.genomic_position_table.record import (
    CHROM,
    POS_BEGIN,
    POS_END,
)
from gain.genomic_resources.testing import (
    build_filesystem_test_resource,
    convert_to_tab_separated,
    setup_directories,
    setup_gzip,
)


def test_inmemory_genomic_position_table_tsv(tmp_path: pathlib.Path) -> None:
    setup_directories(tmp_path, {
        "genomic_resource.yaml": """
            table:
                filename: data.tsv""",
        "data.tsv": convert_to_tab_separated("""
            chrom pos_begin pos2  c2
            1     10        12    3.14
            1     11        11    4.14
        """)})
    res = build_filesystem_test_resource(tmp_path)
    assert res.config is not None
    tab = build_genomic_position_table(
        res, res.config["table"])
    tab.open()
    assert len(list(tab.get_all_records())) == 2


def test_inmemory_genomic_position_table_tsv_compressed(
    tmp_path: pathlib.Path,
) -> None:
    setup_directories(tmp_path, {
        "genomic_resource.yaml": """
            table:
                filename: data.tsv.gz""",
    })
    setup_gzip(tmp_path / "data.tsv.gz", """
        chrom pos_begin pos2  c2
        1     10        12    3.14
        1     11        11    4.14
    """)
    res = build_filesystem_test_resource(tmp_path)
    assert res.config is not None
    tab = build_genomic_position_table(
        res, res.config["table"])
    tab.open()
    assert len(list(tab.get_all_records())) == 2


def test_inmemory_genomic_position_table_txt(tmp_path: pathlib.Path) -> None:
    setup_directories(tmp_path, {
        "genomic_resource.yaml": """
            table:
                filename: data.txt""",
        "data.txt": convert_to_tab_separated("""
            chrom pos_begin pos2  c2
            1     10        12    3.14
            1     11        11    4.14
        """)})
    res = build_filesystem_test_resource(tmp_path)
    assert res.config is not None
    tab = build_genomic_position_table(
        res, res.config["table"])
    tab.open()
    assert len(list(tab.get_all_records())) == 2


def test_inmemory_genomic_position_table_txt_compressed(
    tmp_path: pathlib.Path,
) -> None:
    setup_directories(tmp_path, {
        "genomic_resource.yaml": """
            table:
                filename: data.txt.gz""",
    })
    setup_gzip(tmp_path / "data.txt.gz", """
        chrom pos_begin pos2  c2
        1     10        12    3.14
        1     11        11    4.14
    """)
    res = build_filesystem_test_resource(tmp_path)
    assert res.config is not None
    tab = build_genomic_position_table(
        res, res.config["table"])
    tab.open()
    assert len(list(tab.get_all_records())) == 2


def test_inmemory_genomic_position_table_csv(tmp_path: pathlib.Path) -> None:
    setup_directories(tmp_path, {
        "genomic_resource.yaml": """
            table:
                filename: data.csv""",
        "data.csv": convert_to_tab_separated("""
            chrom,pos_begin,pos2 ,c2
            1,10,12,3.14
            1,11,11,4.14
        """)})
    res = build_filesystem_test_resource(tmp_path)
    assert res.config is not None
    tab = build_genomic_position_table(
        res, res.config["table"])
    tab.open()
    assert len(list(tab.get_all_records())) == 2


def test_inmemory_genomic_position_table_csv_compressed(
    tmp_path: pathlib.Path,
) -> None:
    setup_directories(tmp_path, {
        "genomic_resource.yaml": """
            table:
                filename: data.csv.gz""",
    })
    setup_gzip(tmp_path / "data.csv.gz", """
        chrom,pos_begin,pos2 ,c2
        1,10,12,3.14
        1,11,11,4.14
    """)
    res = build_filesystem_test_resource(tmp_path)
    assert res.config is not None
    tab = build_genomic_position_table(
        res, res.config["table"])
    tab.open()
    assert len(list(tab.get_all_records())) == 2


def test_inmemory_genomic_position_table_zero_based_no_header(
    tmp_path: pathlib.Path,
) -> None:
    setup_directories(tmp_path, {
        "genomic_resource.yaml": """
            table:
              filename: data.tsv
              header_mode: none
              zero_based: True
              chrom:
                index: 0
              pos_begin:
                index: 1
              pos_end:
                index: 1

        """,
        "data.tsv": convert_to_tab_separated("""
            chr1  0   0.1
            chr1  1   0.2
            chr1  2   0.3
            chr2  0   0.1
            chr2  1   0.2
            chr2  2   0.3
        """)})
    res = build_filesystem_test_resource(tmp_path)
    assert res.config is not None
    table = build_genomic_position_table(res, res.config["table"])
    table.open()
    assert len(list(table.get_all_records())) == 6
    vs = list(table.get_records_in_region("chr1", 2, 2))
    assert len(vs) == 1
    assert vs[0][CHROM] == "chr1"
    assert vs[0][POS_BEGIN] == 2
    assert vs[0][POS_END] == 2


def test_get_records_in_region_without_chrom(tmp_path: pathlib.Path) -> None:
    setup_directories(tmp_path, {
        "genomic_resource.yaml": """
            table:
                filename: data.txt""",
        "data.txt": convert_to_tab_separated("""
            chrom pos_begin pos2  c2
            1     10        12    3.14
            1     11        11    4.14
        """)})
    res = build_filesystem_test_resource(tmp_path)
    assert res.config is not None
    tab = build_genomic_position_table(res, res.config["table"])
    tab.open()
    assert len(list(tab.get_all_records())) == 2


def _empty_mapped_contig_table(tmp_path: pathlib.Path):
    # A chrom_mapping.filename maps two reference contigs -- 'kept' onto a file
    # contig that has data rows, and 'empty' onto a file contig with none.  So
    # 'empty' is in get_chromosomes() but has no records: a known-but-empty
    # contig, the case that exercises the empty/unknown-contig policy.
    setup_directories(tmp_path, {
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
        """),
        "chrom_map.txt": convert_to_tab_separated("""
            chrom   file_chrom
            kept    chr1
            empty   chr99
        """)})
    res = build_filesystem_test_resource(tmp_path)
    assert res.config is not None
    tab = build_genomic_position_table(res, res.config["table"])
    tab.open()
    return tab


def test_get_all_records_skips_empty_mapped_contig(
    tmp_path: pathlib.Path,
) -> None:
    tab = _empty_mapped_contig_table(tmp_path)
    # both contigs are known...
    assert tab.get_chromosomes() == ["kept", "empty"]
    # ...but get_all_records skips the known-but-empty one.
    records = list(tab.get_all_records())
    assert len(records) == 1
    assert records[0][CHROM] == "kept"


def test_get_records_in_region_empty_mapped_contig_yields_nothing(
    tmp_path: pathlib.Path,
) -> None:
    tab = _empty_mapped_contig_table(tmp_path)
    # a known-but-empty contig yields nothing (no error)...
    assert list(tab.get_records_in_region("empty")) == []


def test_get_records_in_region_unknown_contig_raises(
    tmp_path: pathlib.Path,
) -> None:
    tab = _empty_mapped_contig_table(tmp_path)
    # ...but an unknown contig is an error.
    with pytest.raises(ValueError, match="chromosome nosuch"):
        list(tab.get_records_in_region("nosuch"))


def test_find_chromosome_length_reports_a_populated_contigs_length(
    tmp_path: pathlib.Path,
) -> None:
    """The tri-state hook answers with a number when it has one.

    ``find_chromosome_length`` is what ``get_chromosome_length`` is built on:
    it reports the length when there is one, and otherwise says WHY there is
    not -- a distinction a caller needs, because a contig proven to hold no
    records and a contig whose length merely could not be determined get
    opposite treatment (gain#509).
    """
    tab = _empty_mapped_contig_table(tmp_path)
    # The same number the raising wrapper reports, for the same contig: the
    # wrapper adds refusal, not arithmetic.
    assert tab.find_chromosome_length("kept") == 13


def test_find_chromosome_length_proves_a_known_contig_is_empty(
    tmp_path: pathlib.Path,
) -> None:
    """This backend holds the whole file, so no records PROVES no records.

    That proof is what the caller needs: there is nothing to read on this
    contig, so it can be skipped outright rather than scanned as an unbounded
    region -- which for a mapping covering hg38's alts would cost a table open
    per empty contig.
    """
    tab = _empty_mapped_contig_table(tmp_path)
    assert tab.find_chromosome_length("empty") is ContigExtent.EMPTY


def test_find_chromosome_length_unknown_contig_raises_not_empty(
    tmp_path: pathlib.Path,
) -> None:
    """An unknown contig is a bad question, not a proven-empty answer.

    The two look identical from inside this backend -- neither has records to
    take a maximum over -- but they are not the same claim, and collapsing them
    would let a typo'd or mis-mapped contig name read as "proven to hold no
    records" and be silently dropped from a scan.  ``EMPTY`` is reserved for a
    contig the table LISTS and has no rows for.
    """
    tab = _empty_mapped_contig_table(tmp_path)
    with pytest.raises(ValueError, match="nosuch"):
        tab.find_chromosome_length("nosuch")


def test_find_chromosome_length_on_a_closed_table_says_it_is_not_open(
    tmp_path: pathlib.Path,
) -> None:
    """A closed table must refuse, not report every contig as empty.

    ``close()`` empties ``records_by_chr``, so without a not-open guard AHEAD
    of the no-records branch a closed table would answer ``EMPTY`` for every
    contig -- including one the file is full of.  That is worse here than the
    wrong message gain#358 fixed on the raising wrapper: ``EMPTY`` is not an
    error, so a caller would skip the whole genome and report success.

    Asserted against THIS backend's own wording rather than any "not open"
    text, because a closed table refuses ``get_chromosomes()`` too: without its
    own guard this method still raises, but out of the middle of building the
    no-records message, so the caller gets the base class's generic complaint
    about a read this method never meant to make.  That is the gain#358 shape,
    and matching loosely would let it back in.
    """
    tab = _empty_mapped_contig_table(tmp_path)
    tab.close()
    with pytest.raises(ValueError, match="in-memory table not open") as err:
        tab.find_chromosome_length("kept")
    # ...and it names the resource, so an operator knows WHICH table was read
    # after closing.
    assert tab.genomic_resource.resource_id in str(err.value)


def test_get_chromosome_length_empty_mapped_contig_raises(
    tmp_path: pathlib.Path,
) -> None:
    tab = _empty_mapped_contig_table(tmp_path)
    # a known-but-empty contig has no max end position: clear ValueError,
    # not a bare KeyError or a max()-of-empty-sequence error.
    with pytest.raises(ValueError, match="contig empty has no records") as err:
        tab.get_chromosome_length("empty")
    # ...and the diagnostic names the contigs the table does have, which is
    # what tells a caller whether it asked about a contig this table has never
    # heard of or about one it knows and has no rows for.  It is only buildable
    # on an OPEN table -- get_chromosomes() refuses on a closed one -- which is
    # why the closed case is guarded ahead of this branch (gain#358).
    # Asserted by NAME against the list the message ends with, rather than
    # against its exact repr: what the diagnostic owes the caller is the names,
    # not a particular rendering or ordering of them.  Sliced at "contigs:"
    # because "empty" is also the contig asked about, and so appears in the
    # first half of the message either way.
    contigs_listed = str(err.value).split("contigs:")[-1]
    assert "kept" in contigs_listed
    assert "empty" in contigs_listed
    # the populated contig still reports a length.
    assert tab.get_chromosome_length("kept") == 13


def test_get_chromosome_length_on_a_closed_table_says_it_is_not_open(
    tmp_path: pathlib.Path,
) -> None:
    """A closed table reports why it actually failed, not a wrong diagnosis.

    ``close()`` empties ``records_by_chr``, so on a closed table EVERY contig
    -- including one the file is full of -- takes the no-records branch, and
    the message that branch builds interpolates ``get_chromosomes()``, which a
    closed table refuses.  So the intended diagnostic was never built: what
    reached the caller came out of the middle of another message's
    construction, and the "has no records" claim it was on its way to making
    about a perfectly good contig was simply false.

    A closed table refuses this read for the same reason it refuses its file
    contigs, and says so in the same words its ``_load_file_chromosomes``
    uses -- so the answer does not depend on which of the two file-derived
    fields the call happened to reach first (gain#358).
    """
    tab = _empty_mapped_contig_table(tmp_path)
    assert tab.get_chromosome_length("kept") == 13

    tab.close()

    with pytest.raises(ValueError, match="in-memory table not open") as err:
        tab.get_chromosome_length("kept")
    assert "no records" not in str(err.value), (
        "a closed table reported a populated contig as having no records")
