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

from gain.genomic_resources.gene_models.default_attributes import (
    format_default_attributes,
)
from gain.genomic_resources.gene_models.gene_models import GeneModels
from gain.genomic_resources.gene_models.gene_models_factory import (
    build_gene_models_from_resource,
)
from gain.genomic_resources.gene_models.serialization import (
    _save_as_default_gene_models,
)
from gain.genomic_resources.gene_models.transcript_models import (
    TranscriptModel,
)
from gain.genomic_resources.testing import build_inmemory_test_resource

from tests.small.genomic_resources.gene_models.columnar_formats import REFSEQ


def refseq_row_with(name: str, **cells: str) -> str:
    """One refSeq row, built from the shared layout table.

    The columns and their well-formed values live in `columnar_formats`;
    spelling a second copy of them here would be a second thing to keep
    in step with the parsers.
    """
    fields = list(REFSEQ.fields)
    fields[REFSEQ.columns.index("name")] = name
    for column, value in cells.items():
        fields[REFSEQ.columns.index(column)] = value
    return "\t".join(fields) + "\n"


def refseq_row(name: str, score: str) -> str:
    """One refSeq row whose `score` is the cell under test."""
    return refseq_row_with(name, score=score)


def models_of(source: str, file_format: str) -> GeneModels:
    """The models a source file in `file_format` loads as."""
    gene_models = build_gene_models_from_resource(build_inmemory_test_resource(
        content={
            "genomic_resource.yaml":
                f"{{type: gene_models, filename: genes.txt, "
                f"format: {file_format}}}",
            "genes.txt": source,
        }))
    gene_models.load()
    return gene_models


def written_out(gene_models: GeneModels) -> str:
    """The default-format file these models serialize to."""
    written = StringIO()
    _save_as_default_gene_models(gene_models, written)
    return written.getvalue()


def cell_of(written: str, tr_id: str, column: str) -> str:
    """One named cell of one record of a written default-format file.

    The column is found through the header the file carries rather than
    by a fixed position, so this says which cell it means in the
    format's own terms.
    """
    header, *rows = written.splitlines()
    index = header.split("\t").index(column)
    [row] = [r for r in rows if r.split("\t")[1] == tr_id]
    return row.split("\t")[index]


def shape(transcript: TranscriptModel) -> tuple:
    """Everything about a transcript that the round trip must preserve.

    The models carry no equality of their own, so "the same models" is
    spelled out here rather than left to identity.

    The attributes are compared as the text they serialize to, not as
    the dict. A layout that leaves its optional columns to pandas hands
    them over as pandas' own numbers -- refSeq's `score` arrives as an
    integer -- and gain's own format has only text to write them as, so
    the dicts differ by type on a trip that changed nothing. What the
    format can represent is the text, and that is what has to survive.
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
        format_default_attributes(transcript.attributes),
    )


def shapes(models: dict[str, TranscriptModel]) -> dict[str, tuple]:
    return {tr_id: shape(tm) for tr_id, tm in models.items()}


def test_a_record_with_a_blank_cell_survives_the_round_trip() -> None:
    """Written out and read back, the models are the ones we started with.

    The second record's `score` is blank; the first record's is not, so
    this pins the neighbour as well as the blank itself.
    """
    genes = refseq_row("NM_000546", "0") + refseq_row("NM_001126", "")
    resource = build_inmemory_test_resource(content={
        "genomic_resource.yaml":
            "{type: gene_models, filename: genes.txt, format: refseq}",
        "genes.txt": genes,
    })
    gene_models = build_gene_models_from_resource(resource)
    gene_models.load()

    written = StringIO()
    _save_as_default_gene_models(gene_models, written)
    reloaded = build_gene_models_from_resource(build_inmemory_test_resource(
        content={
            "genomic_resource.yaml":
                "{type: gene_models, filename: genes.txt, format: default}",
            "genes.txt": written.getvalue(),
        }))
    reloaded.load()

    assert shapes(reloaded.transcript_models) == \
        shapes(gene_models.transcript_models)

    # and the file itself is a fixpoint: writing the models that were
    # read back reproduces the bytes they were read from. This is the
    # type-independent half -- it compares the format to itself.
    again = StringIO()
    _save_as_default_gene_models(reloaded, again)
    assert again.getvalue() == written.getvalue()


def test_a_zero_coordinate_survives_the_round_trip() -> None:
    """A coding bound of `0` comes back the value it went out as.

    The UCSC layouts spell an empty coding region `cdsStart == cdsEnd ==
    0`, and only `cdsEnd` reaches the model holding `0`: those layouts
    are half-open, so the parser shifts `cdsStart` by one and a `0`
    there becomes a `1`. Under a truthiness guard that surviving `0`
    left the file as a blank cell, and the read side then refused the
    file gain had just written (gain#951).

    The first record's bounds are well-formed, so this pins the
    neighbour as well as the `0` itself.
    """
    genes = refseq_row_with("NM_000546") \
        + refseq_row_with("NM_001126", cdsEnd="0")
    gene_models = models_of(genes, "refseq")

    written = written_out(gene_models)
    reloaded = models_of(written, "default")

    assert shapes(reloaded.transcript_models) == \
        shapes(gene_models.transcript_models)


def test_a_zero_coordinate_is_written_out_as_zero() -> None:
    """What lands in the file is the digit, not an empty cell.

    The round trip above would be satisfied by any spelling that reads
    back as `0` -- `0.0` among them, which the coordinate parser
    accepts. This is the half that says which spelling: the coordinate
    as the model holds it.
    """
    gene_models = models_of(
        refseq_row_with("NM_001126", cdsEnd="0"), "refseq")
    [tr_id] = gene_models.transcript_models

    written = written_out(gene_models)

    assert cell_of(written, tr_id, "cdsEnd") == "0"


def test_a_blank_cell_is_written_out_as_nothing() -> None:
    """What lands in the file is the empty cell the source held.

    The round trip above would be satisfied by any token that survives
    it, `nan` included, as long as it came back unchanged. This is the
    half that says which token: none.
    """
    genes = refseq_row("NM_001126", "")
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
