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

import pytest
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
    Exon,
    TranscriptModel,
)
from gain.genomic_resources.testing import build_inmemory_test_resource

from tests.small.genomic_resources.gene_models.columnar_formats import (
    DEFAULT,
    REFSEQ,
)


def refseq_row(name: str, score: str) -> str:
    """One refSeq row, built from the shared layout table.

    The columns and their well-formed values live in `columnar_formats`;
    spelling a second copy of them here would be a second thing to keep
    in step with the parsers.
    """
    fields = list(REFSEQ.fields)
    fields[REFSEQ.columns.index("name")] = name
    fields[REFSEQ.columns.index("score")] = score
    return "\t".join(fields) + "\n"


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

    Both the record and the column are found through the header the
    file carries rather than by a fixed position, so this says which
    cell it means in the format's own terms.
    """
    header, *rows = written.splitlines()
    columns = header.split("\t")
    for row in rows:
        cells = row.split("\t")
        if cells[columns.index("trID")] == tr_id:
            return cells[columns.index(column)]
    raise AssertionError(f"no record {tr_id} in the written file")


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
    gene_models = models_of(genes, "refseq")

    written = written_out(gene_models)
    reloaded = models_of(written, "default")

    assert shapes(reloaded.transcript_models) == \
        shapes(gene_models.transcript_models)

    # and the file itself is a fixpoint: writing the models that were
    # read back reproduces the bytes they were read from. This is the
    # type-independent half -- it compares the format to itself.
    assert written_out(reloaded) == written


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
    gene_models = models_of(
        REFSEQ.file_with("cdsEnd", "0").read(), "refseq")

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
    gene_models = models_of(REFSEQ.row_with("cdsEnd", "0"), "refseq")
    [tr_id] = gene_models.transcript_models

    written = written_out(gene_models)

    assert cell_of(written, tr_id, "cdsEnd") == "0"


def test_a_zero_transcript_start_survives_the_round_trip() -> None:
    """The transcript start is the other bound that reaches a model as 0.

    gain's own format is not half-open -- it is already in gain's
    coordinates -- so a `tsBeg` of 0 is read as 0 rather than shifted to
    1, which is what puts a second column through the guard. gain would
    not itself emit this file: its coordinates are 1-based and closed,
    so 0 is off-spec, and the file here is hand-made rather than
    gain-written. What it pins is the guard, not a bound gain produces.

    The first record's start is well-formed, so this pins the neighbour
    as well as the 0 itself.
    """
    gene_models = models_of(
        DEFAULT.file_with("tsBeg", "0").read(), "default")

    written = written_out(gene_models)
    reloaded = models_of(written, "default")

    assert shapes(reloaded.transcript_models) == \
        shapes(gene_models.transcript_models)
    # and writing the models that were read back reproduces the bytes
    # they were read from
    assert written_out(reloaded) == written


def test_an_exon_with_no_frame_is_refused_rather_than_written() -> None:
    """An unset frame stops the write, naming the record and the column.

    An `Exon` built without a frame holds `None`, and `str()` inside the
    exon-frame column spelled that as the literal `None` -- a token the
    format cannot express and the read side refuses. Every parser fills
    the frames in through `update_frames()` before the models escape, so
    nothing gain reads can reach here; this guards the caller that built
    a model by hand and never computed them.

    Refusing is what the read side does with a cell it cannot use, and
    the write side reports the same way. Writing `-1` instead would put
    a value in the file that the model never held.
    """
    gene_models = models_of(REFSEQ.good_row(), "refseq")
    [tr_id] = gene_models.transcript_models
    transcript = gene_models.transcript_models[tr_id]
    transcript.exons = [Exon(exon.start, exon.stop)
                        for exon in transcript.exons]

    with pytest.raises(ValueError, match="exonFrames") as error:
        written_out(gene_models)

    assert tr_id in str(error.value)
    assert transcript.chrom in str(error.value)


def test_a_blank_cell_is_written_out_as_nothing() -> None:
    """What lands in the file is the empty cell the source held.

    The round trip above would be satisfied by any token that survives
    it, `nan` included, as long as it came back unchanged. This is the
    half that says which token: none.
    """
    gene_models = models_of(refseq_row("NM_001126", ""), "refseq")

    written = written_out(gene_models)

    assert "nan" not in written
    assert "score:;" in written
