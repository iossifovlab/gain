# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""Attribute values must survive parsing and serialization intact.

Both the GTF attributes column and the ``default`` format's ``atts`` column
pack several key/value pairs into one field using delimiters that may also
occur inside a value. See iossifovlab/gain#852.
"""
from io import StringIO

import pytest
from gain.genomic_resources.gene_models.gene_models import GeneModels
from gain.genomic_resources.gene_models.gene_models_factory import (
    build_gene_models_from_resource,
)
from gain.genomic_resources.gene_models.parsers import (
    _parse_gtf_attributes,
    escape_default_attribute,
    parse_default_attributes,
    parse_default_gene_models_format,
)
from gain.genomic_resources.gene_models.serialization import (
    _save_as_default_gene_models,
)
from gain.genomic_resources.testing import build_inmemory_test_resource

NOTE_WITH_SEMICOLON = (
    "The sequence was modified: inserted 5 bases in 4 codons; "
    "deleted 1 base in 1 codon"
)


def _gtf_gene_models(attributes: str) -> GeneModels:
    """Build gene models from a one-transcript GTF with given attributes."""
    def record(feature: str, start: int, end: int) -> str:
        return "\t".join([
            "chr1", "test", feature, str(start), str(end),
            ".", "+", ".", attributes,
        ])

    content = "\n".join([
        record("transcript", 10, 100),
        record("exon", 10, 40),
        record("exon", 60, 100),
    ]) + "\n"
    res = build_inmemory_test_resource(
        content={
            "genomic_resource.yaml":
                "{type: gene_models, filename: genes.gtf, format: gtf}",
            "genes.gtf": content,
        },
    )
    return build_gene_models_from_resource(res).load()


def _default_format_roundtrip(gene_models: GeneModels) -> dict:
    """Save gene models as ``default`` format and parse them back."""
    outfile = StringIO()
    _save_as_default_gene_models(gene_models, outfile)
    reloaded = parse_default_gene_models_format(StringIO(outfile.getvalue()))
    assert reloaded is not None
    return next(iter(reloaded.values())).attributes


def test_gtf_note_with_semicolon_survives_default_roundtrip() -> None:
    gene_models = _gtf_gene_models(
        f'gene_id "G1"; transcript_id "T1"; note "{NOTE_WITH_SEMICOLON}";',
    )

    attributes = _default_format_roundtrip(gene_models)

    assert attributes["note"] == NOTE_WITH_SEMICOLON


def test_gtf_note_fragment_without_a_space_is_not_a_new_attribute() -> None:
    """The D. melanogaster case: a fragment that is a single bare word."""
    note = "lncRNA:CR40469-RA; Dmel\\lncRNA:CR40469-RA; CR40469-RA"

    attributes = _parse_gtf_attributes(f'transcript_id "T1"; note "{note}";')

    assert attributes == {"transcript_id": "T1", "note": note}


def test_gtf_attribute_value_cannot_inject_a_key() -> None:
    """A value that looks like more attributes stays a value."""
    attributes = _parse_gtf_attributes(
        'gene_id "G1"; note "evil; gene_name "HACKED"";',
    )

    assert "gene_name" not in attributes
    assert attributes["gene_id"] == "G1"


def test_gtf_malformed_attribute_reports_the_offending_text() -> None:
    with pytest.raises(ValueError, match="CR40469-RA"):
        _parse_gtf_attributes('transcript_id "T1"; CR40469-RA;')


def test_gtf_unterminated_quote_reports_the_offending_text() -> None:
    with pytest.raises(ValueError, match="unterminated quote"):
        _parse_gtf_attributes('gene_id "G1"; note "runs off the end')


def test_gtf_stray_quote_in_a_value_keeps_the_later_attributes() -> None:
    """GTF cannot escape a quote, so a value may carry a stray one."""
    attributes = _parse_gtf_attributes(
        'gene_id "G1"; product "3" end marker"; transcript_id "T1";',
    )

    assert attributes["product"] == '3" end marker'
    assert attributes["transcript_id"] == "T1"


def test_gtf_bare_value_is_read_to_the_separator() -> None:
    """GENCODE writes exon_number unquoted."""
    attributes = _parse_gtf_attributes('gene_id "G1"; exon_number 1;')

    assert attributes == {"gene_id": "G1", "exon_number": "1"}


def test_gtf_value_whitespace_is_trimmed() -> None:
    assert _parse_gtf_attributes('gene_id "G1 "; gene_name " N";') == {
        "gene_id": "G1", "gene_name": "N",
    }


def test_default_attributes_roundtrip_preserves_every_delimiter() -> None:
    value = "before; after: inner\\slash"

    escaped = escape_default_attribute(value)

    assert parse_default_attributes(f"note:{escaped}") == {"note": value}


def test_default_attributes_keep_unknown_backslash_sequences() -> None:
    """Values written before escaping was introduced read back unchanged."""
    assert parse_default_attributes("note:Dmel\\lncRNA_CR40469-RA") == {
        "note": "Dmel\\lncRNA_CR40469-RA",
    }


def test_legacy_backslash_before_a_delimiter_reads_as_an_escape() -> None:
    """The one sequence a pre-escaping file cannot be told apart from.

    Nothing in the file records whether it was written with escaping, so a
    backslash that happens to sit in front of a delimiter is read as an
    escape. Values written before escaping existed reach this only if they
    end in a backslash, which the quote-blind parser this change replaces
    could never produce -- it truncated values at the first ';'.
    """
    assert parse_default_attributes("note:Dmel\\;gene_name:FOO") == {
        "note": "Dmel;gene_name:FOO",
    }
    assert parse_default_attributes("note:x\\\\y;gene:G") == {
        "note": "x\\y", "gene": "G",
    }


def test_default_attributes_malformed_pair_reports_the_offending_text() -> None:
    with pytest.raises(ValueError, match="orphan"):
        parse_default_attributes("note:fine;orphan")
