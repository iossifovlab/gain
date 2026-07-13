# pylint: disable=W0621,C0114,C0116,W0212,W0613,too-many-lines

import pathlib

import pytest
from gain.genomic_resources.genomic_position_table import (
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
    assert len(list(tab.get_records_in_region())) == 2


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


def test_get_chromosome_length_empty_mapped_contig_raises(
    tmp_path: pathlib.Path,
) -> None:
    tab = _empty_mapped_contig_table(tmp_path)
    # a known-but-empty contig has no max end position: clear ValueError,
    # not a bare KeyError or a max()-of-empty-sequence error.
    with pytest.raises(ValueError, match="contig empty has no records"):
        tab.get_chromosome_length("empty")
    # the populated contig still reports a length.
    assert tab.get_chromosome_length("kept") == 13
