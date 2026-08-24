# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""Attribute values must survive parsing and serialization intact.

Both the GTF attributes column and the ``default`` format's ``atts`` column
pack several key/value pairs into one field using delimiters that may also
occur inside a value. See iossifovlab/gain#852.
"""
from io import StringIO

from gain.genomic_resources.gene_models.gene_models import GeneModels
from gain.genomic_resources.gene_models.gene_models_factory import (
    build_gene_models_from_resource,
)
from gain.genomic_resources.gene_models.parsers import (
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
