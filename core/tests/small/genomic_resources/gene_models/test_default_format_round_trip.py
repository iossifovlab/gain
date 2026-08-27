"""A record with a blank cell survives being written out and read back.

Serializing to gain's own format and parsing the result is what a
published GRR gene-models resource is: the columnar file is read once,
and everything downstream reads what gain wrote. So a blank cell has to
survive that trip unchanged, and the fabricated `nan` did not -- it was
written as a token the source never held and came back as the string
`"nan"`, a different value from the one the round trip started with
(gain#931).

The file here is built with real tabs rather than through
`convert_to_tab_separated`, which spells an empty cell as `.` and so
cannot express the thing under test.
"""

from io import StringIO

from gain.genomic_resources.gene_models.gene_models_factory import (
    build_gene_models_from_resource,
)
from gain.genomic_resources.gene_models.parsers import (
    parse_default_gene_models_format,
)
from gain.genomic_resources.gene_models.serialization import (
    _save_as_default_gene_models,
)
from gain.genomic_resources.gene_models.transcript_models import (
    TranscriptModel,
)
from gain.genomic_resources.testing import build_inmemory_test_resource

REFSEQ_COLUMNS = (
    "#bin", "name", "chrom", "strand", "txStart", "txEnd", "cdsStart",
    "cdsEnd", "exonCount", "exonStarts", "exonEnds", "score", "name2",
    "cdsStartStat", "cdsEndStat", "exonFrames",
)


def refseq_row(name: str, score: str) -> str:
    return "\t".join([
        "0", name, "chr17", "+", "100", "400", "150", "350", "2",
        "100,300,", "200,400,", score, "TP53", "cmpl", "cmpl", "0,0",
    ])


def shape(transcript: TranscriptModel) -> tuple:
    """Everything about a transcript that the round trip must preserve.

    The models carry no equality of their own, so "the same models" is
    spelled out here rather than left to identity.
    """
    return (
        transcript.gene,
        transcript.tr_name,
        transcript.chrom,
        transcript.strand,
        transcript.tx,
        transcript.cds,
        tuple((exon.start, exon.stop, exon.frame)
              for exon in transcript.exons),
        transcript.attributes,
    )


def shapes(models: dict[str, TranscriptModel]) -> dict[str, tuple]:
    return {tr_id: shape(tm) for tr_id, tm in models.items()}


def test_a_record_with_a_blank_cell_survives_the_round_trip() -> None:
    """Written out and read back, the models are the ones we started with.

    The second record's `score` is blank; the first record's is not, so
    this pins the neighbour as well as the blank itself.
    """
    genes = "\n".join([
        refseq_row("NM_000546", "0"),
        refseq_row("NM_001126", ""),
    ]) + "\n"
    resource = build_inmemory_test_resource(content={
        "genomic_resource.yaml":
            "{type: gene_models, filename: genes.txt, format: refseq}",
        "genes.txt": genes,
    })
    gene_models = build_gene_models_from_resource(resource)
    gene_models.load()

    written = StringIO()
    _save_as_default_gene_models(gene_models, written)
    reparsed = parse_default_gene_models_format(
        StringIO(written.getvalue()))

    assert reparsed is not None
    assert shapes(reparsed) == shapes(gene_models.transcript_models)


def test_a_blank_cell_is_written_out_as_nothing() -> None:
    """What lands in the file is the empty cell the source held.

    The round trip above would be satisfied by any token that survives
    it, `nan` included, as long as it came back unchanged. This is the
    half that says which token: none.
    """
    genes = refseq_row("NM_001126", "") + "\n"
    resource = build_inmemory_test_resource(content={
        "genomic_resource.yaml":
            "{type: gene_models, filename: genes.txt, format: refseq}",
        "genes.txt": genes,
    })
    gene_models = build_gene_models_from_resource(resource)
    gene_models.load()

    written = StringIO()
    _save_as_default_gene_models(gene_models, written)

    assert "nan" not in written.getvalue()
    assert "score:;" in written.getvalue()
