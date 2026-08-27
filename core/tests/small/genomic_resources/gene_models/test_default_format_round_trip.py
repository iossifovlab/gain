"""What gain writes out, gain reads back -- cell by cell.

Serializing to gain's own format and parsing the result is what a
published GRR gene-models resource is: the columnar file is read once,
and everything downstream reads what gain wrote. So every cell has to
survive that trip unchanged.

Two cells have failed to. A blank one was written as a fabricated `nan`
-- a token the source never held, which came back as the string `"nan"`,
a different value from the one the round trip started with (gain#931).
And a coordinate of `0` was written as a blank, which since gain#929 is
a hard parse error, so the file gain wrote was one gain could not read
back at all (gain#957).

Each of the two is covered by a pair of tests: one that the models
survive the trip, and one that says which token was written. Neither
half stands on its own -- the round trip is satisfied by any token that
survives it, and the written token means nothing if the models it came
from are wrong -- so the pairs are load-bearing. Do not drop one half.

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

from tests.small.genomic_resources.gene_models.columnar_formats import (
    DEFAULT,
    REFSEQ,
)


def refseq_row(name: str, score: str, **overrides: str) -> str:
    """One refSeq row, built from the shared layout table.

    The columns and their well-formed values live in `columnar_formats`;
    spelling a second copy of them here would be a second thing to keep
    in step with the parsers.

    `overrides` names any further column by its layout-table name, so a
    row that varies a coordinate does not need a second hand-spelled
    copy of the sixteen columns either.
    """
    fields = list(REFSEQ.fields)
    fields[REFSEQ.columns.index("name")] = name
    fields[REFSEQ.columns.index("score")] = score
    for column, value in overrides.items():
        fields[REFSEQ.columns.index(column)] = value
    return "\t".join(fields) + "\n"


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


def default_file(**overrides: str) -> str:
    """gain's own format: a header and one row, from the shared table.

    Unlike the UCSC layouts this one is headered, so the columns are
    written out rather than left to position.
    """
    fields = list(DEFAULT.fields)
    for column, value in overrides.items():
        fields[DEFAULT.columns.index(column)] = value
    return "\t".join(DEFAULT.columns) + "\n" + "\t".join(fields) + "\n"


def shapes(models: dict[str, TranscriptModel]) -> dict[str, tuple]:
    return {tr_id: shape(tm) for tr_id, tm in models.items()}


def models_from(text: str, fmt: str) -> GeneModels:
    """Load gene models from a file of `fmt` held in memory."""
    resource = build_inmemory_test_resource(content={
        "genomic_resource.yaml":
            f"{{type: gene_models, filename: genes.txt, format: {fmt}}}",
        "genes.txt": text,
    })
    gene_models = build_gene_models_from_resource(resource)
    gene_models.load()
    return gene_models


def written_out(gene_models: GeneModels) -> str:
    """The file gain's own serializer makes of these models."""
    outfile = StringIO()
    _save_as_default_gene_models(gene_models, outfile)
    return outfile.getvalue()


def test_a_record_with_a_blank_cell_survives_the_round_trip() -> None:
    """Written out and read back, the models are the ones we started with.

    The second record's `score` is blank; the first record's is not, so
    this pins the neighbour as well as the blank itself.
    """
    gene_models = models_from(
        refseq_row("NM_000546", "0") + refseq_row("NM_001126", ""), "refseq")

    written = written_out(gene_models)
    reloaded = models_from(written, "default")

    assert shapes(reloaded.transcript_models) == \
        shapes(gene_models.transcript_models)

    # and the file itself is a fixpoint: writing the models that were
    # read back reproduces the bytes they were read from. This is the
    # type-independent half -- it compares the format to itself.
    assert written_out(reloaded) == written


def test_a_zero_coordinate_survives_the_round_trip() -> None:
    """A bound of `0` is a coordinate, not an absent cell.

    Written as a blank cell it stops being a coordinate at all, and
    since #929 a blank coordinate is a hard parse error, so the file
    gain wrote is one gain cannot read back (#957).

    A zero reaches serialization from either direction. Out of a UCSC
    layout it is `cdsEnd`: the half-open shift lifts the start clear of
    falsiness and leaves the end as it was, so a source that spells "no
    CDS" as the pair `0`/`0` keeps a `cdsEnd` of `0`. That spelling is a
    departure from the UCSC convention, which spells no-CDS as
    `cdsStart == cdsEnd == txEnd` -- no record in the published hg38
    refSeq resource has a `cdsEnd` of `0`. Out of gain's own format
    there is no shift at all, so `tsBeg` and `cdsStart` reach `0` too,
    which is what re-serializing a default-format resource does. This
    covers the first; `test_a_zero_bound_of_our_own_format_round_trips`
    covers the second.

    The first record keeps the layout table's own non-zero bounds, so
    this pins the neighbour as well as the zero: the reload fails for
    the whole file, not just for the record that provoked it.
    """
    gene_models = models_from(
        refseq_row("NM_000546", "0")
        + refseq_row("NR_000001", "0", cdsStart="0", cdsEnd="0"), "refseq")

    written = written_out(gene_models)
    reloaded = models_from(written, "default")

    assert shapes(reloaded.transcript_models) == \
        shapes(gene_models.transcript_models)
    assert written_out(reloaded) == written


def test_a_zero_bound_of_our_own_format_round_trips() -> None:
    """Re-serializing our own format is where the other bounds reach 0.

    The UCSC layouts shift each start by one, which lifts `tsBeg` and
    `cdsStart` clear of falsiness before serialization ever sees them.
    Our own format is already 1-based and shifts nothing, so a resource
    written in it can hold a `0` in any of the four bounds -- and
    re-serializing such a resource is exactly what the GRR build step
    does. Reading one back out and writing it again has to keep them.
    """
    gene_models = models_from(
        default_file(tsBeg="0", cdsStart="0", exonStarts="0,300"), "default")
    assert [tm.tx[0] for tm in gene_models.transcript_models.values()] == [0]

    written = written_out(gene_models)
    reloaded = models_from(written, "default")

    assert shapes(reloaded.transcript_models) == \
        shapes(gene_models.transcript_models)
    assert written_out(reloaded) == written


def test_a_zero_coordinate_is_written_out_as_zero() -> None:
    """What lands in the file is the coordinate, not an empty cell.

    The round trip above is satisfied by anything that reads back as
    `0`. This is the half that says which token, and it is read out of
    the column the header names rather than by position, so it keeps
    pointing at the coordinate if the layout ever grows a column.
    """
    written = written_out(models_from(
        refseq_row("NR_000001", "0", cdsStart="0", cdsEnd="0"), "refseq"))

    header, record = written.strip("\n").split("\n")
    cells = dict(zip(header.split("\t"), record.split("\t"), strict=True))
    assert cells["cdsEnd"] == "0"


def test_a_blank_cell_is_written_out_as_nothing() -> None:
    """What lands in the file is the empty cell the source held.

    The round trip above would be satisfied by any token that survives
    it, `nan` included, as long as it came back unchanged. This is the
    half that says which token: none.
    """
    written = written_out(models_from(refseq_row("NM_001126", ""), "refseq"))

    assert "nan" not in written
    assert "score:;" in written
