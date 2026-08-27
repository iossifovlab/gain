# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""Tests for gain.genomic_resources.gene_models.parsers module."""
import inspect
import re
from io import StringIO

import pytest
from gain.genomic_resources.gene_models import parsers
from gain.genomic_resources.gene_models.parsers import (
    SUPPORTED_GENE_MODELS_FILE_FORMATS,
    _parse_gtf_attributes,
    get_parser,
    infer_gene_model_parser,
    load_gene_mapping,
    parse_ccds_gene_models_format,
    parse_default_gene_models_format,
    parse_gtf_gene_models_format,
    parse_known_gene_models_format,
    parse_raw,
    parse_ref_flat_gene_models_format,
    parse_ref_seq_gene_models_format,
    parse_ucscgenepred_models_format,
    probe_columns,
    probe_header,
)
from gain.genomic_resources.testing import (
    build_inmemory_test_resource,
    convert_to_tab_separated,
)


def test_probe_header_matches() -> None:
    """Test probe_header when header matches expected columns."""
    data = StringIO("#col1\tcol2\tcol3\n")
    expected = ["#col1", "col2", "col3"]
    assert probe_header(data, expected) is True


def test_probe_header_no_match() -> None:
    """Test probe_header when header doesn't match expected columns."""
    data = StringIO("#col1\tcol2\tcol4\n")
    expected = ["#col1", "col2", "col3"]
    assert probe_header(data, expected) is False


def test_probe_header_with_comment() -> None:
    """Test probe_header with comment character."""
    # When comment="#", lines starting with # are skipped
    # so we need data without # prefix for the header
    data = StringIO("# comment line\ncol1\tcol2\tcol3\n")
    expected = ["col1", "col2", "col3"]
    assert probe_header(data, expected, comment="#") is True


def test_probe_columns_correct_count() -> None:
    """Test probe_columns with correct column count."""
    data = StringIO("val1\tval2\tval3\n")
    expected = ["col1", "col2", "col3"]
    assert probe_columns(data, expected) is True


def test_probe_columns_incorrect_count() -> None:
    """Test probe_columns with incorrect column count."""
    data = StringIO("val1\tval2\n")
    expected = ["col1", "col2", "col3"]
    assert probe_columns(data, expected) is False


def test_probe_columns_with_comment() -> None:
    """Test probe_columns ignoring comment lines."""
    data = StringIO("# comment\nval1\tval2\tval3\n")
    expected = ["col1", "col2", "col3"]
    assert probe_columns(data, expected, comment="#") is True


def test_parse_raw_with_header() -> None:
    """Test parse_raw with header line."""
    data = StringIO("#col1\tcol2\tcol3\nval1\tval2\tval3\n")
    expected = ["#col1", "col2", "col3"]
    df = parse_raw(data, expected)
    assert df is not None
    assert list(df.columns) == expected
    assert len(df) == 1
    assert df.iloc[0]["#col1"] == "val1"


def test_parse_raw_without_header() -> None:
    """Test parse_raw without header line."""
    data = StringIO("val1\tval2\tval3\nval4\tval5\tval6\n")
    expected = ["col1", "col2", "col3"]
    df = parse_raw(data, expected)
    assert df is not None
    assert list(df.columns) == expected
    assert len(df) == 2
    assert df.iloc[0]["col1"] == "val1"


def test_parse_raw_with_nrows() -> None:
    """Test parse_raw respects nrows parameter."""
    data = StringIO("#col1\tcol2\nval1\tval2\nval3\tval4\nval5\tval6\n")
    expected = ["#col1", "col2"]
    df = parse_raw(data, expected, nrows=2)
    assert df is not None
    assert len(df) == 2


def test_parse_raw_with_comment() -> None:
    """Test parse_raw ignores comment lines."""
    data = StringIO(
        "# this is a comment\n#col1\tcol2\nval1\tval2\n")
    expected = ["#col1", "col2"]
    df = parse_raw(data, expected, comment="#")
    assert df is not None
    assert len(df) == 1


def test_parse_raw_no_match_returns_none() -> None:
    """Test parse_raw returns None when no format matches."""
    data = StringIO("val1\tval2\n")
    expected = ["col1", "col2", "col3"]
    df = parse_raw(data, expected)
    assert df is None


def test_parse_gtf_attributes_basic() -> None:
    """Test parsing basic GTF attributes."""
    data = 'gene_id "ENSG00000223972"; transcript_id "ENST00000456328";'
    result = _parse_gtf_attributes(data)
    assert result == {
        "gene_id": "ENSG00000223972",
        "transcript_id": "ENST00000456328",
    }


def test_parse_gtf_attributes_with_spaces() -> None:
    """Test parsing GTF attributes with trailing spaces in values."""
    data = 'gene_id "ENSG00000223972" ; gene_name "DDX11L1" ;'
    result = _parse_gtf_attributes(data)
    assert "gene_id" in result
    assert "gene_name" in result
    assert result["gene_id"] == "ENSG00000223972"
    assert result["gene_name"] == "DDX11L1"


def test_parse_gtf_attributes_empty() -> None:
    """Test parsing empty GTF attributes."""
    data = ""
    result = _parse_gtf_attributes(data)
    assert not result


def test_parse_gtf_attributes_single() -> None:
    """Test parsing single GTF attribute."""
    data = 'gene_id "ENSG00000223972";'
    result = _parse_gtf_attributes(data)
    assert result == {"gene_id": "ENSG00000223972"}


def test_parse_gtf_attributes_with_values_containing_spaces() -> None:
    """Test parsing GTF attributes with values containing spaces."""
    data = 'gene_name "Gene Name With Spaces"; gene_id "ENSG001";'
    result = _parse_gtf_attributes(data)
    assert result["gene_name"] == "Gene Name With Spaces"
    assert result["gene_id"] == "ENSG001"


#: Which parser each format name is meant to reach, spelled out rather than
#: read back from the registry under test -- a lookup keyed by the same dict
#: would agree with any binding, including a wrong one. Swapping two rows
#: here has to fail, so the pairs stay written by hand; the test below keeps
#: the list from drifting out of step with the supported formats.
_GET_PARSER_CASES = [
    ("default", parse_default_gene_models_format),
    ("refflat", parse_ref_flat_gene_models_format),
    ("refseq", parse_ref_seq_gene_models_format),
    ("ccds", parse_ccds_gene_models_format),
    ("knowngene", parse_known_gene_models_format),
    ("gtf", parse_gtf_gene_models_format),
    ("ucscgenepred", parse_ucscgenepred_models_format),
]


@pytest.mark.parametrize("format_name,expected_parser", _GET_PARSER_CASES)
def test_get_parser(format_name: str, expected_parser) -> None:  # type: ignore
    """Test get_parser returns correct parser for supported formats."""
    parser = get_parser(format_name)
    assert parser is expected_parser


def test_get_parser_cases_cover_every_supported_format() -> None:
    """Every supported format has its binding pinned above.

    Without this, adding a format is only half-tested: the new name reaches
    a parser, but nothing says it reaches the intended one.
    """
    covered = {format_name for format_name, _ in _GET_PARSER_CASES}
    assert covered == SUPPORTED_GENE_MODELS_FILE_FORMATS


def test_get_parser_unsupported() -> None:
    """Test get_parser returns None for unsupported format."""
    parser = get_parser("unsupported_format")
    assert parser is None


def test_infer_with_explicit_format() -> None:
    """Test infer when explicit format is provided."""
    data = StringIO("")
    result = infer_gene_model_parser(data, file_format="gtf")
    assert result == "gtf"


def test_infer_with_invalid_explicit_format() -> None:
    """Test infer with invalid explicit format."""
    data = StringIO("")
    result = infer_gene_model_parser(data, file_format="invalid")
    assert result is None


def test_infer_refflat_format() -> None:
    """Test inferring refflat format."""
    data = StringIO(convert_to_tab_separated("""
        #geneName name chrom strand txStart txEnd cdsStart cdsEnd exonCount exonStarts exonEnds
        TP53 NM_000546 17 - 7571719 7590868 7572826 7590856 11 7571719,7572926,7573927,7576525,7576853,7577018,7577155,7577498,7578176,7578371,7579311 7573008,7573009,7574033,7576657,7576926,7577095,7577243,7577608,7578289,7578554,7590868
    """))  # noqa
    result = infer_gene_model_parser(data)
    assert result == "refflat"


def test_infer_gtf_format() -> None:
    """Test inferring GTF format."""
    data = StringIO(
        "chr1\ttest\ttranscript\t100\t200\t.\t+\t.\t"
        'gene_id "ENSG001"; transcript_id "ENST001"; gene_name "TEST";\n'
        "chr1\ttest\texon\t100\t150\t.\t+\t.\t"
        'gene_id "ENSG001"; transcript_id "ENST001"; gene_name "TEST";\n')
    result = infer_gene_model_parser(data)
    assert result == "gtf"


def test_infer_no_matching_format() -> None:
    """Test infer returns None when no format matches."""
    data = StringIO("invalid data format\n")
    result = infer_gene_model_parser(data)
    assert result is None


def test_supported_formats_are_inferrable() -> None:
    """Test that all supported formats have parsers."""
    for fmt in SUPPORTED_GENE_MODELS_FILE_FORMATS:
        parser = get_parser(fmt)
        assert parser is not None, f"Format {fmt} has no parser"


#: A format parser is named for the format it reads. The shared helpers the
#: parsers module also carries -- ``parse_columnar_format``, ``parse_raw``,
#: ``parse_transcript_bounds``, ``parse_exon_bounds``,
#: ``parse_default_attributes`` -- deliberately do not match, so the scan
#: below sees the format parsers and nothing else. Note it is ``_models_``
#: that excludes them: matching a bare ``_format`` suffix would sweep up
#: ``parse_columnar_format``, which is the shared record loop and is
#: registered under no name of its own.
_FORMAT_PARSER_NAME = re.compile(r"parse_\w+_models_format")


def test_every_format_parser_is_reachable_through_get_parser() -> None:
    """A parser under no format name is dead code no caller can reach.

    ``test_supported_formats_are_inferrable`` covers the other direction --
    every supported name resolves to a parser. Together they pin the two
    structures to each other; neither alone catches a one-sided edit.
    """
    defined = {
        obj
        for name, obj in vars(parsers).items()
        if _FORMAT_PARSER_NAME.fullmatch(name) and inspect.isfunction(obj)
    }
    assert defined, \
        "the format-parser naming convention moved; this scan matched nothing"

    reachable = {get_parser(fmt) for fmt in SUPPORTED_GENE_MODELS_FILE_FORMATS}

    unreachable = defined - reachable
    assert not unreachable, (
        "format parsers registered under no name: "
        f"{sorted(parser.__name__ for parser in unreachable)}"
    )


def test_load_gene_mapping_simple() -> None:
    """Test loading simple gene mapping."""
    res = build_inmemory_test_resource(
        content={
            "genomic_resource.yaml": (
                "{type: gene_models, filename: genes.txt, "
                "gene_mapping: mapping.txt}"
            ),
            "genes.txt": "",
            "mapping.txt": convert_to_tab_separated("""
                from   to
                POGZ   gene1
                TP53   gene2
            """),
        })

    mapping = load_gene_mapping(res)
    assert mapping == {"POGZ": "gene1", "TP53": "gene2"}


def test_load_gene_mapping_no_mapping_file() -> None:
    """Test load_gene_mapping when no mapping file is configured."""
    res = build_inmemory_test_resource(
        content={
            "genomic_resource.yaml":
                "{type: gene_models, filename: genes.txt}",
            "genes.txt": "",
        })

    mapping = load_gene_mapping(res)
    assert not mapping


def test_load_gene_mapping_custom_column_names() -> None:
    """Test loading gene mapping with custom column names."""
    res = build_inmemory_test_resource(
        content={
            "genomic_resource.yaml": (
                "{type: gene_models, filename: genes.txt, "
                "gene_mapping: mapping.txt}"
            ),
            "genes.txt": "",
            "mapping.txt": convert_to_tab_separated("""
                old_name   new_name
                POGZ       POGZ_NEW
                TP53       TP53_ALT
            """),
        })

    mapping = load_gene_mapping(res)
    assert mapping == {"POGZ": "POGZ_NEW", "TP53": "TP53_ALT"}


def test_parse_default_basic() -> None:
    """Test parsing basic default format."""
    data = StringIO(convert_to_tab_separated("""
        chr trID gene strand tsBeg txEnd cdsStart cdsEnd exonStarts exonEnds exonFrames atts
        1 tx1 GENE1 + 100 200 110 190 100,150 130,200 0,1 attr1:val1
    """))  # noqa

    result = parse_default_gene_models_format(data)
    assert result is not None
    assert len(result) == 1
    assert "tx1" in result
    tm = result["tx1"]
    assert tm.gene == "GENE1"
    assert tm.chrom == "1"
    assert tm.strand == "+"
    assert tm.tx == (100, 200)
    assert tm.cds == (110, 190)
    assert len(tm.exons) == 2


def test_parse_default_with_gene_mapping() -> None:
    """Test parsing with gene mapping."""
    data = StringIO(convert_to_tab_separated("""
        chr trID gene strand tsBeg txEnd cdsStart cdsEnd exonStarts exonEnds exonFrames atts
        1 tx1 GENE1 + 100 200 110 190 100,150 130,200 0,1 attr1:val1
    """))  # noqa

    gene_mapping = {"GENE1": "MAPPED_GENE"}
    result = parse_default_gene_models_format(data, gene_mapping)
    assert result is not None
    assert result["tx1"].gene == "MAPPED_GENE"


def test_parse_default_with_nrows() -> None:
    """Test parsing with nrows limit."""
    data = StringIO(convert_to_tab_separated("""
        chr trID gene strand tsBeg txEnd cdsStart cdsEnd exonStarts exonEnds exonFrames atts
        1 tx1 GENE1 + 100 200 110 190 100,150 130,200 0,1 attr1:val1
        1 tx2 GENE2 + 300 400 310 390 300,350 330,400 0,1 attr2:val2
        1 tx3 GENE3 + 500 600 510 590 500,550 530,600 0,1 attr3:val3
    """))  # noqa

    result = parse_default_gene_models_format(data, nrows=2)
    assert result is not None
    assert len(result) == 2
    assert "tx1" in result
    assert "tx2" in result
    assert "tx3" not in result


def test_parse_default_with_attributes() -> None:
    """Test parsing attributes field."""
    data = StringIO(convert_to_tab_separated("""
        chr trID gene strand tsBeg txEnd cdsStart cdsEnd exonStarts exonEnds exonFrames atts
        1 tx1 GENE1 + 100 200 110 190 100,150 130,200 0,1 key1:val1;key2:val2
    """))  # noqa

    result = parse_default_gene_models_format(data)
    assert result is not None
    tm = result["tx1"]
    assert tm.attributes == {"key1": "val1", "key2": "val2"}


def test_parse_refflat_basic() -> None:
    """Test parsing basic refflat format."""
    data = StringIO(convert_to_tab_separated("""
        #geneName name chrom strand txStart txEnd cdsStart cdsEnd exonCount exonStarts exonEnds
        GENE1 NM_001 1 + 99 200 109 190 2 99,149 130,200
    """))  # noqa

    result = parse_ref_flat_gene_models_format(data)
    assert result is not None
    assert len(result) == 1
    # transcript_id is generated as name_counter
    assert "NM_001_1" in result
    tm = result["NM_001_1"]
    assert tm.gene == "GENE1"
    assert tm.tr_name == "NM_001"
    assert tm.chrom == "1"
    assert tm.strand == "+"
    # coordinates are 0-based in refflat, converted to 1-based
    assert tm.tx == (100, 200)
    assert tm.cds == (110, 190)
    assert len(tm.exons) == 2


def test_parse_refflat_with_gene_mapping() -> None:
    """Test parsing refflat with gene mapping."""
    data = StringIO(convert_to_tab_separated("""
        #geneName name chrom strand txStart txEnd cdsStart cdsEnd exonCount exonStarts exonEnds
        GENE1 NM_001 1 + 99 200 109 190 2 99,149 130,200
    """))  # noqa

    gene_mapping = {"GENE1": "MAPPED"}
    result = parse_ref_flat_gene_models_format(data, gene_mapping)
    assert result is not None
    assert result["NM_001_1"].gene == "MAPPED"


def test_parse_refflat_multiple_transcripts_same_name() -> None:
    """Test parsing multiple transcripts with same name."""
    data = StringIO(convert_to_tab_separated("""
        #geneName name chrom strand txStart txEnd cdsStart cdsEnd exonCount exonStarts exonEnds
        GENE1 NM_001 1 + 99 200 109 190 2 99,149 130,200
        GENE1 NM_001 1 + 99 250 109 240 2 99,199 130,250
    """))  # noqa

    result = parse_ref_flat_gene_models_format(data)
    assert result is not None
    assert len(result) == 2
    assert "NM_001_1" in result
    assert "NM_001_2" in result


def test_parse_refseq_basic() -> None:
    """Test parsing basic refseq format."""
    data = StringIO(convert_to_tab_separated("""
        #bin name chrom strand txStart txEnd cdsStart cdsEnd exonCount exonStarts exonEnds score name2 cdsStartStat cdsEndStat exonFrames
        585 NM_001 chr1 + 99 200 109 190 2 99,149 130,200 0 GENE1 cmpl cmpl 0,1
    """))  # noqa

    result = parse_ref_seq_gene_models_format(data)
    assert result is not None
    assert len(result) == 1
    assert "NM_001_1" in result
    tm = result["NM_001_1"]
    assert tm.gene == "GENE1"
    assert tm.tr_name == "NM_001"
    assert tm.tx == (100, 200)
    assert tm.cds == (110, 190)
    assert "score" in tm.attributes
    assert "cdsStartStat" in tm.attributes


def test_parse_ccds_basic() -> None:
    """Test parsing basic CCDS format."""
    data = StringIO(convert_to_tab_separated("""
        #bin name chrom strand txStart txEnd cdsStart cdsEnd exonCount exonStarts exonEnds score name2 cdsStartStat cdsEndStat exonFrames
        585 CCDS1 chr1 + 99 200 109 190 2 99,149 130,200 0 GENE1 cmpl cmpl 0,1
    """))  # noqa

    result = parse_ccds_gene_models_format(data)
    assert result is not None
    assert len(result) == 1
    assert "CCDS1_1" in result
    tm = result["CCDS1_1"]
    # In CCDS format, gene comes from 'name' field
    assert tm.gene == "CCDS1"
    assert tm.tr_name == "CCDS1"


def test_parse_knowngene_basic() -> None:
    """Test parsing basic known gene format."""
    data = StringIO(convert_to_tab_separated("""
        name chrom strand txStart txEnd cdsStart cdsEnd exonCount exonStarts exonEnds proteinID alignID
        uc001aaa.1 chr1 + 99 200 109 190 2 99,149 130,200 P12345 Q5T123
    """))  # noqa

    result = parse_known_gene_models_format(data)
    assert result is not None
    assert len(result) == 1
    assert "uc001aaa.1_1" in result
    tm = result["uc001aaa.1_1"]
    assert tm.gene == "uc001aaa.1"
    assert "proteinID" in tm.attributes
    assert tm.attributes["proteinID"] == "P12345"


def test_parse_ucscgenepred_basic_format() -> None:
    """Test parsing basic UCSC genePred format (10 columns)."""
    data = StringIO(convert_to_tab_separated("""
        name chrom strand txStart txEnd cdsStart cdsEnd exonCount exonStarts exonEnds
        ENST001 chr1 + 99 200 109 190 2 99,149 130,200
    """))  # noqa

    result = parse_ucscgenepred_models_format(data)
    assert result is not None
    assert len(result) == 1
    assert "ENST001_1" in result
    tm = result["ENST001_1"]
    # Without name2, gene comes from name
    assert tm.gene == "ENST001"
    assert tm.tr_name == "ENST001"


def test_parse_ucscgenepred_extended_format() -> None:
    """Test parsing extended UCSC genePredExt format (15 columns)."""
    data = StringIO(convert_to_tab_separated("""
        name chrom strand txStart txEnd cdsStart cdsEnd exonCount exonStarts exonEnds score name2 cdsStartStat cdsEndStat exonFrames
        ENST001 chr1 + 99 200 109 190 2 99,149 130,200 0 GENE1 cmpl cmpl 0,1
    """))  # noqa

    result = parse_ucscgenepred_models_format(data)
    assert result is not None
    assert len(result) == 1
    assert "ENST001_1" in result
    tm = result["ENST001_1"]
    # With name2, gene comes from name2
    assert tm.gene == "GENE1"
    assert tm.tr_name == "ENST001"
    assert "score" in tm.attributes


def test_parse_gtf_basic() -> None:
    """Test parsing basic GTF format."""
    data = StringIO(
        "chr1\ttest\ttranscript\t100\t200\t.\t+\t.\t"
        'gene_id "ENSG001"; transcript_id "ENST001"; gene_name "GENE1";\n'
        "chr1\ttest\texon\t100\t150\t.\t+\t.\t"
        'gene_id "ENSG001"; transcript_id "ENST001"; gene_name "GENE1";\n'
        "chr1\ttest\texon\t160\t200\t.\t+\t.\t"
        'gene_id "ENSG001"; transcript_id "ENST001"; gene_name "GENE1";\n')

    result = parse_gtf_gene_models_format(data)
    assert result is not None
    assert len(result) == 1
    assert "ENST001" in result
    tm = result["ENST001"]
    assert tm.gene == "GENE1"
    assert tm.tr_name == "ENST001"
    assert len(tm.exons) == 2
    assert tm.exons[0].start == 100
    assert tm.exons[0].stop == 150


def test_parse_gtf_with_cds() -> None:
    """Test parsing GTF with CDS and start/stop codons."""
    data = StringIO(
        "chr1\ttest\ttranscript\t100\t300\t.\t+\t.\t"
        'gene_id "ENSG001"; transcript_id "ENST001"; gene_name "GENE1";\n'
        "chr1\ttest\texon\t100\t200\t.\t+\t.\t"
        'gene_id "ENSG001"; transcript_id "ENST001"; gene_name "GENE1";\n'
        "chr1\ttest\texon\t250\t300\t.\t+\t.\t"
        'gene_id "ENSG001"; transcript_id "ENST001"; gene_name "GENE1";\n'
        "chr1\ttest\tstart_codon\t150\t152\t.\t+\t.\t"
        'gene_id "ENSG001"; transcript_id "ENST001"; gene_name "GENE1";\n'
        "chr1\ttest\tstop_codon\t280\t282\t.\t+\t.\t"
        'gene_id "ENSG001"; transcript_id "ENST001"; gene_name "GENE1";\n')

    result = parse_gtf_gene_models_format(data)
    assert result is not None
    tm = result["ENST001"]
    # CDS should be updated based on start/stop codons
    assert tm.cds[0] == 150
    assert tm.cds[1] == 282
    assert len(tm.exons) == 2


def test_parse_gtf_with_gene_mapping() -> None:
    """Test parsing GTF with gene mapping."""
    data = StringIO(
        "chr1\ttest\ttranscript\t100\t200\t.\t+\t.\t"
        'gene_id "ENSG001"; transcript_id "ENST001"; gene_name "GENE1";\n'
        "chr1\ttest\texon\t100\t200\t.\t+\t.\t"
        'gene_id "ENSG001"; transcript_id "ENST001"; gene_name "GENE1";\n')

    gene_mapping = {"GENE1": "MAPPED_GENE"}
    result = parse_gtf_gene_models_format(data, gene_mapping)
    assert result is not None
    assert result["ENST001"].gene == "MAPPED_GENE"


def test_parse_gtf_ignores_gene_feature() -> None:
    """Test that GTF parser ignores gene features."""
    data = StringIO(
        "chr1\ttest\tgene\t100\t300\t.\t+\t.\t"
        'gene_id "ENSG001"; gene_name "GENE1";\n'
        "chr1\ttest\ttranscript\t100\t200\t.\t+\t.\t"
        'gene_id "ENSG001"; transcript_id "ENST001"; gene_name "GENE1";\n'
        "chr1\ttest\texon\t100\t200\t.\t+\t.\t"
        'gene_id "ENSG001"; transcript_id "ENST001"; gene_name "GENE1";\n')

    result = parse_gtf_gene_models_format(data)
    assert result is not None
    assert len(result) == 1  # Only transcript, not gene


@pytest.mark.parametrize("feature", ["start_codon", "stop_codon"])
def test_parse_gtf_codon_for_unseen_transcript_names_it(
    feature: str,
) -> None:
    """A codon preceding its transcript record must not leak a KeyError."""
    data = StringIO(
        f"chr1\ttest\t{feature}\t10\t12\t.\t+\t0\t"
        'gene_id "G"; transcript_id "T1";\n'
        "chr1\ttest\ttranscript\t10\t100\t.\t+\t.\t"
        'gene_id "G"; transcript_id "T1";\n')

    with pytest.raises(
            ValueError,
            match=f"{feature} transcript T1 not found in transcript models"):
        parse_gtf_gene_models_format(data)


def test_parse_gtf_with_nrows() -> None:
    """Test parsing GTF with nrows limit."""
    data = StringIO(
        "chr1\ttest\ttranscript\t100\t200\t.\t+\t.\t"
        'gene_id "ENSG001"; transcript_id "ENST001"; gene_name "GENE1";\n'
        "chr1\ttest\texon\t100\t200\t.\t+\t.\t"
        'gene_id "ENSG001"; transcript_id "ENST001"; gene_name "GENE1";\n'
        "chr1\ttest\ttranscript\t300\t400\t.\t+\t.\t"
        'gene_id "ENSG002"; transcript_id "ENST002"; gene_name "GENE2";\n')

    result = parse_gtf_gene_models_format(data, nrows=2)
    assert result is not None
    # Only first transcript should be parsed
    assert len(result) == 1
    assert "ENST001" in result
