from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from typing import IO, Any, cast

import pandas as pd

from gain import logging
from gain.genomic_resources.repository import (
    GenomicResource,
)
from gain.utils.log_safety import escape_unsafe_characters

from .default_attributes import parse_default_attributes
from .record_cells import (
    QUOTED_TEXT_LIMIT,
    cell_text,
    parse_coordinate,
    parse_exon_bounds,
    parse_exon_positions,
    parse_transcript_bounds,
    record_identity,
    require_cell,
    require_equal_exon_counts,
)
from .transcript_models import (
    Exon,
    TranscriptModel,
)

logger = logging.getLogger(__name__)


# Every parser signals a rejection the same two ways, and format inference
# reads that convention back to say why a format lost: ``None`` means the
# file does not have this format's column layout, while an empty dict means
# the layout matched but no transcript model came of it. Anything else the
# parser cannot handle it raises.
GeneModelsParser = Callable[
    [IO, dict[str, str] | None, int | None],
    dict[str, TranscriptModel] | None,
]


def parse_default_gene_models_format(
    infile: IO,
    gene_mapping: dict[str, str] | None = None,
    nrows: int | None = None,
) -> dict[str, TranscriptModel] | None:
    """Parse default gene models file format."""
    # pylint: disable=too-many-locals
    infile.seek(0)
    # The four bound columns are deliberately left to inference: they are
    # converted by `parse_coordinate` either way, and pinning them here
    # would say something this format does not mean -- gain writes them,
    # and writes them as integers. `na_filter=False` is what keeps a
    # blank cell the empty text it was rather than a float ``NaN``, which
    # `gene` -- the one column here that is neither load-bearing nor an
    # attribute -- carried into serialization as the token ``nan``
    # (gain#931).
    df = read_gene_models_tsv(
        infile,
        nrows=nrows,
        dtype={
            "chr": str,
            "trID": str,
            "trOrigId": str,
            "gene": str,
            "strand": str,
            "atts": str,
        },
    )

    expected_columns = [
        "chr",
        "trID",
        "gene",
        "strand",
        "tsBeg",
        "txEnd",
        "cdsStart",
        "cdsEnd",
        "exonStarts",
        "exonEnds",
        "exonFrames",
        "atts",
    ]
    if not set(expected_columns) <= set(df.columns):
        return None

    if "trOrigId" not in df.columns:
        tr_names = pd.Series(data=df["trID"].values)
        df["trOrigId"] = tr_names

    if gene_mapping is None:
        gene_mapping = {}

    transcript_models = {}
    records = df.to_dict(orient="records")
    for record, line in enumerate(records, start=1):
        line = cast(dict, line)
        tr_name, chrom = record_identity(line, record, "trID", "chr")
        exon_starts = parse_exon_positions(
            line["exonStarts"], "exonStarts", tr_name, chrom)
        exon_ends = parse_exon_positions(
            line["exonEnds"], "exonEnds", tr_name, chrom)
        exon_frames = parse_exon_positions(
            line["exonFrames"], "exonFrames", tr_name, chrom)
        require_equal_exon_counts(
            tr_name, chrom,
            exonStarts=exon_starts, exonEnds=exon_ends,
            exonFrames=exon_frames)

        exons = []
        for start, end, frame in zip(exon_starts, exon_ends, exon_frames,
                                     strict=True):
            exons.append(Exon(start=start, stop=end, frame=frame))
        attributes: dict = {}
        atts = line.get("atts")
        if atts and isinstance(atts, str):
            attributes = parse_default_attributes(atts)
        gene = line["gene"]
        gene = gene_mapping.get(gene, gene)
        transcript_model = TranscriptModel(
            gene=gene,
            tr_id=tr_name,
            tr_name=require_cell(
                line["trOrigId"], "trOrigId", tr_name, chrom),
            chrom=chrom,
            strand=require_cell(line["strand"], "strand", tr_name, chrom),
            tx=(parse_coordinate(line["tsBeg"], "tsBeg", tr_name, chrom),
                parse_coordinate(line["txEnd"], "txEnd", tr_name, chrom)),
            cds=(parse_coordinate(
                     line["cdsStart"], "cdsStart", tr_name, chrom),
                 parse_coordinate(
                     line["cdsEnd"], "cdsEnd", tr_name, chrom)),
            exons=exons,
            attributes=attributes,
        )
        transcript_models[transcript_model.tr_id] = transcript_model
    return transcript_models


def probe_header(
    infile: IO, expected_columns: list[str],
    comment: str | None = None,
) -> bool:
    """Probe gene models file header based on expected columns."""
    infile.seek(0)
    df = pd.read_csv(
        infile, sep="\t", nrows=1, header=None, comment=comment)
    return list(df.iloc[0, :]) == expected_columns


def probe_columns(
    infile: IO, expected_columns: list[str],
    comment: str | None = None,
) -> bool:
    """Probe gene models file based on expected columns."""
    infile.seek(0)
    df = pd.read_csv(
        infile, sep="\t", nrows=1, header=None, comment=comment)
    return cast(list[int], list(df.columns)) == \
        list(range(len(expected_columns)))


def read_gene_models_tsv(infile: IO, **kwargs: Any) -> pd.DataFrame:
    """Read a gene-models table, keeping every cell as its own text.

    Every read that builds records goes through here, so that
    ``na_filter`` is off in one place rather than five. pandas otherwise
    reads a blank cell -- and several spellings that are not blank,
    ``NA`` and ``NULL`` among them -- as a float ``NaN``, which reaches
    serialization as the fabricated token ``nan`` and re-types the
    column around it (gain#931).

    `probe_header` and `probe_columns` do not: they read one row to
    recognise a layout and never look at a value, so what a blank cell
    becomes there cannot reach a record.

    That the setting had to be repeated per call site is how a read got
    missed: the gene mapping kept filtering long after the two model
    reads stopped, and wrote ``nan`` into the gene column.

    What each caller pins with ``dtype`` still differs, and is theirs to
    decide -- the layouts do not agree on which columns are text.
    """
    return cast(
        pd.DataFrame,
        pd.read_csv(infile, sep="\t", na_filter=False, **kwargs))


#: The columns a columnar record is identified by. These, plus whatever
#: the caller names in ``text_columns``, are what the headerless read
#: pins to text. Pinning the whole frame would do it too, but an
#: all-object frame makes pandas' own ``to_dict`` much slower --
#: measured at +13% over a 196k-record refSeq file, against +2% for these
#: two -- and every other column is converted by `record_cells` anyway.
#:
#: Adding a layout's gene column to the pin is free on that same file:
#: its alternate names are symbols, so they are text whichever way the
#: column is read, and the two arms produce the same frame. Where the
#: pin does change the frame -- a gene column of bare digits, the case
#: it exists for -- reading it as text rather than as the ``int64``
#: pandas would infer costs +7.5% of this read plus the ``to_dict``
#: that follows it. Against a whole `parse_columnar_format` it is under
#: 3% and does not clear that run's own spread, the record loop being
#: the larger half (196k records, pandas 3.0.2, gain#963). The scope is
#: named here because the two figures above do not name theirs, and the
#: numbers are not comparable without it.
_IDENTIFYING_COLUMNS = ("name", "chrom")


def parse_raw(
    infile: IO, expected_columns: list[str],
    nrows: int | None = None, comment: str | None = None,
    text_columns: tuple[str, ...] = (),
) -> pd.DataFrame | None:
    """Parse raw gene models data based on expected columns.

    Both branches keep a blank cell as the empty string it was. Letting
    pandas filter it instead made what a cell became a property of its
    whole column rather than of itself: one blank re-typed the column, so
    a well-formed record serialized differently depending on whether some
    *other* row was blank, and the blank itself reached serialization as
    the fabricated token ``nan`` (gain#931).

    What each branch pins differs, and only because of what it costs. The
    headered branch has always pinned every column to text. The
    headerless branch pins `_IDENTIFYING_COLUMNS`, which is what settles
    the typing gain#929 left here -- a chromosome column of bare digits
    was handed over as the int 17, and a transcript index keyed by that
    is unreachable by a query for "17". The two read the same values
    either way; the pin decides only how much of the frame is object.

    ``text_columns`` is how a caller names the rest of what it keys by.
    A gene label is the second such column, and for the same reason: it
    keys the gene index, so a gene labelled by a bare digit was
    unreachable by a lookup for its own name, and *which* label a record
    got depended on which branch below recognised the file (gain#963).
    Only the caller knows which column that is -- it differs per layout,
    and for one of them it is the alternate name with the transcript
    name behind it. gain's own output format, which does not come
    through here, pins its gene column the same way and always has.

    The GTF reader shares this and names none of these columns, so it
    keeps the inference its own arithmetic depends on. It does not
    escape ``na_filter``: a blank cell reaches it as ``''`` too, which
    is what its blank-attributes guard now decides on.
    """
    if probe_header(infile, expected_columns, comment=comment):
        infile.seek(0)
        df = read_gene_models_tsv(
            infile, nrows=nrows, comment=comment, dtype=str)
        assert list(df.columns) == expected_columns
        return df

    if probe_columns(infile, expected_columns, comment=comment):
        infile.seek(0)
        df = read_gene_models_tsv(
            infile,
            nrows=nrows,
            header=None,
            names=expected_columns,
            comment=comment,
            dtype={
                column: str
                for column in (*_IDENTIFYING_COLUMNS, *text_columns)
                if column in expected_columns
            },
        )
        assert list(df.columns) == expected_columns
        return df
    return None


@dataclass(frozen=True)
class ColumnarLayout:
    """One UCSC-derived columnar gene-models layout.

    The five layouts gain reads -- refFlat, refSeq, CCDS, knownGene and
    UCSC genePred -- are one record loop over one raw read. They differ
    on three axes and no others, which are the three fields below
    (gain#941). Everything else the loop does is the same for all of
    them and lives in `parse_columnar_format`: the half-open-to-inclusive
    coordinate shift, suffixing a transcript name into a unique id, the
    exon read, and `update_frames()`.

    gain's own output format is deliberately not one of these. It is read
    by column name rather than by position, is already in gain's
    coordinates, carries a third exon column, and builds its attributes
    by parsing a dedicated column rather than by copying whole cells.
    """

    #: The column lists this format accepts, tried in order. Only
    #: genePred has more than one -- the ten-column `genePred` core and
    #: the fifteen-column `genePredExt`. Neither attempt can consume the
    #: other's file, though the two branches of `parse_raw` rule that out
    #: differently: `probe_header` matches the header against these names
    #: and `probe_columns` counts them.
    accepted_columns: tuple[tuple[str, ...], ...]

    #: Where the gene label comes from, best candidate first. The first
    #: column carrying a non-blank cell wins; when none does, the last
    #: column named here supplies its cell anyway, blank or absent or
    #: not. A one-column rule is therefore just that column, read
    #: unconditionally, which is what four of the five layouts want.
    #:
    #: This list is also what `parse_columnar_format` hands `parse_raw`
    #: to pin to text, so adding a column here does more than reorder
    #: the fallback: it changes the dtype that column is read at, and
    #: for a column that is also an attribute it changes what
    #: serializes back out (gain#963).
    gene_columns: tuple[str, ...]

    #: The columns copied into `TranscriptModel.attributes`, in this
    #: order -- attributes are written back out in iteration order, so
    #: the order is part of the layout. This is the union over the
    #: accepted column lists, not a subset any one file carries: it is
    #: how genePred's two widths share one row, the narrow one carrying
    #: none of these five. `parse_columnar_format` narrows it to the
    #: width that actually matched, once, before reading any record.
    attribute_columns: tuple[str, ...] = ()


#: refSeq and CCDS declare the same sixteen columns and copy the same six
#: of them into attributes; which column the gene label comes from is the
#: whole of the difference between the two formats. Because the layouts
#: are indistinguishable, a headerless file matching one matches the
#: other, which is what `_break_refseq_ccds_tie` exists to settle
#: (gain#869) -- so these must stay two entries, not one.
_REFSEQ_COLUMNS = (
    "#bin", "name", "chrom", "strand", "txStart", "txEnd", "cdsStart",
    "cdsEnd", "exonCount", "exonStarts", "exonEnds", "score", "name2",
    "cdsStartStat", "cdsEndStat", "exonFrames",
)
_REFSEQ_ATTRIBUTES = (
    "#bin", "score", "exonCount", "cdsStartStat", "cdsEndStat", "exonFrames",
)

#: The genePred core. refFlat is this with a leading gene-name column and
#: knownGene is this with two trailing identifier columns, so both are
#: spelled as extensions of it.
_GENEPRED_COLUMNS = (
    "name", "chrom", "strand", "txStart", "txEnd", "cdsStart", "cdsEnd",
    "exonCount", "exonStarts", "exonEnds",
)
#: What genePredExt adds to that core, and exactly what it copies into
#: attributes -- the wide layout carries no other column the narrow one
#: lacks.
_GENEPRED_EXT_ONLY_COLUMNS = (
    "score", "name2", "cdsStartStat", "cdsEndStat", "exonFrames",
)
_GENEPRED_EXT_COLUMNS = (*_GENEPRED_COLUMNS, *_GENEPRED_EXT_ONLY_COLUMNS)

REF_FLAT_LAYOUT = ColumnarLayout(
    accepted_columns=(("#geneName", *_GENEPRED_COLUMNS),),
    gene_columns=("#geneName",),
)

REF_SEQ_LAYOUT = ColumnarLayout(
    accepted_columns=(_REFSEQ_COLUMNS,),
    gene_columns=("name2",),
    attribute_columns=_REFSEQ_ATTRIBUTES,
)

CCDS_LAYOUT = ColumnarLayout(
    accepted_columns=(_REFSEQ_COLUMNS,),
    gene_columns=("name",),
    attribute_columns=_REFSEQ_ATTRIBUTES,
)

KNOWN_GENE_LAYOUT = ColumnarLayout(
    accepted_columns=((*_GENEPRED_COLUMNS, "proteinID", "alignID"),),
    gene_columns=("name",),
    attribute_columns=("proteinID", "alignID"),
)

#: The only layout accepting two widths, and the only one whose gene
#: label has a fallback: the narrow form has no alternate-name column at
#: all, and the wide form may carry a blank one. UCSC's own `genePred`
#: and `genePredExt` table definitions -- the sole specification either
#: width has -- are quoted in `parse_ucscgenepred_models_format`.
UCSC_GENEPRED_LAYOUT = ColumnarLayout(
    accepted_columns=(_GENEPRED_COLUMNS, _GENEPRED_EXT_COLUMNS),
    gene_columns=("name2", "name"),
    attribute_columns=_GENEPRED_EXT_ONLY_COLUMNS,
)


def parse_columnar_format(
    layout: ColumnarLayout,
    infile: IO,
    gene_mapping: dict[str, str] | None = None,
    nrows: int | None = None,
) -> dict[str, TranscriptModel] | None:
    """Parse a columnar gene-models file against one layout.

    Returns ``None`` when the file matches none of the layout's accepted
    column lists -- the `GeneModelsParser` rejection convention, which
    format inference reads back to say why a format lost.

    Both of the layout's per-record rules are resolved against the width
    that matched before any record is read, rather than re-derived per
    record. That is not only for speed, though it is worth about 250ns a
    record over files that run to the hundreds of thousands: which
    columns a record carries is settled by the match, because `parse_raw`
    asserts the frame's columns are exactly the ones it was given.
    """
    # pylint: disable=too-many-locals
    df = None
    matched: tuple[str, ...] = ()
    for columns in layout.accepted_columns:
        infile.seek(0)
        df = parse_raw(
            infile, list(columns), nrows=nrows,
            text_columns=layout.gene_columns)
        if df is not None:
            matched = columns
            break
    if df is None:
        return None

    # Both rules narrow to the width that matched. A gene candidate this
    # width does not carry drops out entirely -- that is how genePred's
    # narrow layout, which has no alternate-name column, falls straight
    # through to the transcript name -- and the attribute subset keeps
    # only what the record will have, which for that same narrow layout
    # is none of the five.
    *candidates, gene_fallback = layout.gene_columns
    gene_candidates = tuple(
        column for column in candidates if column in matched
    )
    attribute_columns = tuple(
        column for column in layout.attribute_columns if column in matched
    )

    records = df.to_dict(orient="records")

    transcript_ids_counter: dict[str, int] = defaultdict(int)
    if gene_mapping is None:
        gene_mapping = {}

    transcript_models = {}
    for record, rec in enumerate(records, start=1):
        for column in gene_candidates:
            gene = rec[column]
            if cell_text(gene).strip():
                break
        else:
            gene = rec[gene_fallback]
        gene = gene_mapping.get(gene, gene)

        tr_name, chrom = record_identity(rec, record, *_IDENTIFYING_COLUMNS)
        strand = require_cell(rec["strand"], "strand", tr_name, chrom)
        tx, cds = parse_transcript_bounds(  # pylint: disable=invalid-name
            rec, tr_name, chrom)

        exon_starts, exon_ends = parse_exon_bounds(rec, tr_name, chrom)

        exons = [
            Exon(start + 1, end)
            for start, end in zip(exon_starts, exon_ends, strict=True)
        ]

        transcript_ids_counter[tr_name] += 1
        tr_id = f"{tr_name}_{transcript_ids_counter[tr_name]}"

        transcript_model = TranscriptModel(
            gene=gene,
            tr_id=tr_id,
            tr_name=tr_name,
            chrom=chrom,
            strand=strand,
            tx=tx,
            cds=cds,
            exons=exons,
            attributes={
                column: rec[column] for column in attribute_columns
            },
        )
        transcript_model.update_frames()
        assert transcript_model.tr_id not in transcript_models
        transcript_models[transcript_model.tr_id] = transcript_model

    return transcript_models


def parse_ref_flat_gene_models_format(
    infile: IO,
    gene_mapping: dict[str, str] | None = None,
    nrows: int | None = None,
) -> dict[str, TranscriptModel] | None:
    """Parse refFlat gene models file format."""
    return parse_columnar_format(
        REF_FLAT_LAYOUT, infile, gene_mapping, nrows)


def parse_ref_seq_gene_models_format(
    infile: IO,
    gene_mapping: dict[str, str] | None = None,
    nrows: int | None = None,
) -> dict[str, TranscriptModel] | None:
    """Parse refSeq gene models file format."""
    return parse_columnar_format(
        REF_SEQ_LAYOUT, infile, gene_mapping, nrows)


def parse_ccds_gene_models_format(
    infile: IO,
    gene_mapping: dict[str, str] | None = None,
    nrows: int | None = None,
) -> dict[str, TranscriptModel] | None:
    """Parse CCDS gene models file format.

    A CCDS model's gene and transcript name are the same string; see
    `_REFSEQ_COLUMNS` for why this format and refSeq are two entries.
    """
    return parse_columnar_format(CCDS_LAYOUT, infile, gene_mapping, nrows)


def parse_known_gene_models_format(
    infile: IO,
    gene_mapping: dict[str, str] | None = None,
    nrows: int | None = None,
) -> dict[str, TranscriptModel] | None:
    """Parse known gene models file format."""
    return parse_columnar_format(
        KNOWN_GENE_LAYOUT, infile, gene_mapping, nrows)


def parse_ucscgenepred_models_format(
    infile: IO,
    gene_mapping: dict[str, str] | None = None,
    nrows: int | None = None,
) -> dict[str, TranscriptModel] | None:
    """Parse UCSC gene prediction models file fomrat.

    table genePred
    "A gene prediction."
        (
        string  name;               "Name of gene"
        string  chrom;              "Chromosome name"
        char[1] strand;             "+ or - for strand"
        uint    txStart;            "Transcription start position"
        uint    txEnd;              "Transcription end position"
        uint    cdsStart;           "Coding region start"
        uint    cdsEnd;             "Coding region end"
        uint    exonCount;          "Number of exons"
        uint[exonCount] exonStarts; "Exon start positions"
        uint[exonCount] exonEnds;   "Exon end positions"
        )

    table genePredExt
    "A gene prediction with some additional info."
        (
        string name;        	"Name of gene (usually transcript_id from
                                    GTF)"
        string chrom;       	"Chromosome name"
        char[1] strand;     	"+ or - for strand"
        uint txStart;       	"Transcription start position"
        uint txEnd;         	"Transcription end position"
        uint cdsStart;      	"Coding region start"
        uint cdsEnd;        	"Coding region end"
        uint exonCount;     	"Number of exons"
        uint[exonCount] exonStarts; "Exon start positions"
        uint[exonCount] exonEnds;   "Exon end positions"
        int score;            	"Score"
        string name2;       	"Alternate name (e.g. gene_id from GTF)"
        string cdsStartStat; 	"Status of CDS start annotation (none,
                                    unknown, incomplete, or complete)"
        string cdsEndStat;   	"Status of CDS end annotation
                                    (none, unknown,
                                    incomplete, or complete)"
        lstring exonFrames; 	"Exon frame offsets {0,1,2}"
        )
    """
    return parse_columnar_format(
        UCSC_GENEPRED_LAYOUT, infile, gene_mapping, nrows)


def _find_gtf_closing_quote(data: str, start: int) -> int:
    """Return the index of the quote closing a value opened before `start`.

    GTF defines no escape for a quote inside a value, so a value may carry a
    stray one. The closing quote is the first that is followed only by blanks
    and then either a separator or the end of the column; anything else is
    taken to be part of the value.
    """
    index = data.find('"', start)
    while index != -1:
        probe = index + 1
        while probe < len(data) and data[probe] == " ":
            probe += 1
        if probe >= len(data) or data[probe] == ";":
            return index
        index = data.find('"', index + 1)
    return -1


def _parse_gtf_attributes_unquoted(data: str) -> dict[str, str] | None:
    """Parse an attributes column in which no value carries a quote.

    Returns ``None`` for anything this cannot settle on its own -- a value
    holding a quote that is not simply wrapped around it, or a fragment that
    is not a pair -- leaving those to `_scan_gtf_attributes`, which reads
    quotes by position. Splitting wholesale is worth this second pass: the
    overwhelming majority of GTF records need no scanning at all.
    """
    result = {}
    for fragment in data.split(";"):
        attr = fragment.strip()
        if not attr:
            continue
        key, separator, value = attr.partition(" ")
        if not separator:
            return None
        value = value.strip()
        if '"' in value:
            if len(value) < 2 or value[0] != '"' or value[-1] != '"':
                return None
            value = value[1:-1]
            if '"' in value:
                return None
            value = value.strip()
        result[key] = value
    return result


def _end_of_gtf_attribute(data: str, start: int) -> int:
    """Return the index of the next separator at or after `start`."""
    stop = data.find(";", start)
    return len(data) if stop == -1 else stop


def _scan_gtf_attributes(data: str) -> dict[str, str]:
    """Parse a GTF attributes column, reading quotes by position."""
    result = {}
    index = 0
    length = len(data)
    while index < length:
        while index < length and (data[index] == ";" or data[index].isspace()):
            index += 1
        if index >= length:
            break

        space = data.find(" ", index)
        stop = _end_of_gtf_attribute(data, index)
        if space == -1 or stop < space:
            raise ValueError(
                f"malformed GTF attribute {data[index:stop].strip()!r}; "
                f"expected a 'key value' pair",
            )
        key = data[index:space]

        index = space
        while index < length and data[index] == " ":
            index += 1

        if index < length and data[index] == '"':
            closing = _find_gtf_closing_quote(data, index + 1)
            if closing == -1:
                raise ValueError(
                    f"unterminated quote in GTF attribute {key!r}: "
                    f"{data[index:index + QUOTED_TEXT_LIMIT]!r}",
                )
            value = data[index + 1:closing]
            index = _end_of_gtf_attribute(data, closing + 1)
        else:
            stop = _end_of_gtf_attribute(data, index)
            value = data[index:stop]
            index = stop

        result[key] = value.strip()
    return result


def _parse_gtf_attributes(data: str) -> dict[str, str]:
    """Parse a GTF attributes column into key/value pairs.

    A ``;`` separates attributes and is data anywhere inside a value -- NCBI
    RefSeq routinely embeds them in ``note`` and ``product``. Values may be
    quoted or bare, and a bare value runs to the next separator.
    """
    parsed = _parse_gtf_attributes_unquoted(data)
    if parsed is not None:
        return parsed
    return _scan_gtf_attributes(data)


#: Features that introduce a transcript. Ensembl and RefSeq emit the literal
#: ``transcript``; FlyBase instead names the transcript by its biotype. Every
#: entry here is handled identically -- it creates a transcript model keyed by
#: ``transcript_id``. Supporting a flavour usually takes more than this set:
#: FlyBase also relies on the ``5UTR``/``3UTR`` spellings in
#: ``GTF_IGNORED_FEATURES`` and on ``gene_symbol`` as its gene label. Check a
#: new file's ``cut -f3 | sort -u`` against the module's ``GTF_*``
#: constants, which between them spell out the whole vocabulary the loop
#: dispatches on. Flavour is an intake concern only --
#: ``serialization.py`` normalises back out, always writing
#: ``transcript`` and ``gene_name``.
GTF_TRANSCRIPT_FEATURES = frozenset({
    "transcript",
    "mRNA",
    "ncRNA",
    "pseudogene",
    "rRNA",
    "snRNA",
    "snoRNA",
    "tRNA",
})

#: Transcript-level features FlyBase emits with no ``exon`` records at all,
#: so admitting them would add hundreds of transcript models carrying no
#: sequence. This is a policy about these two spellings: it skips them up
#: front, before their children are read, which is what lets a child record
#: be reported against a named skipped transcript. An accepted feature that
#: turns out to have no ``exon`` child is a separate matter -- it is dropped
#: after the whole file is read (gain#965), so no transcript reaches the
#: models with an empty exon list by either route. Moving one of these into
#: ``GTF_TRANSCRIPT_FEATURES`` should be deliberate, not a silent behaviour
#: change.
GTF_EXONLESS_TRANSCRIPT_FEATURES = frozenset({
    "miRNA",
    "pre_miRNA",
})

#: Features whose records contribute nothing to the models and are skipped
#: outright, before attribute parsing -- so an ignored record is not
#: required to carry a ``transcript_id`` (Ensembl ``gene`` records genuinely
#: lack one). ``gene`` restates what every transcript-level record already
#: carries, and the UTR spellings are implied by the exons. The exonless
#: biotypes are deliberately not here: their skip runs after attribute
#: parsing, so their children's errors can name the skipped transcript.
GTF_IGNORED_FEATURES = frozenset({
    "gene",
    "UTR",
    "5UTR",
    "3UTR",
    "five_prime_utr",
    "three_prime_utr",
})

#: Features that append an exon to their transcript's model.
GTF_EXON_FEATURES = frozenset({
    "exon",
})

#: Features that delimit the coding sequence. Each record widens its
#: transcript's ``cds`` interval to cover the codon's span.
GTF_CODON_FEATURES = frozenset({
    "start_codon",
    "stop_codon",
})

#: Features that state the coding sequence itself, one record per coding
#: stretch of an exon. Widened into ``cds`` exactly as the codon records
#: are, and wherever a codon record is missing they are the only
#: statement of the extent there is. For a complete transcript they add
#: nothing: GENCODE and Ensembl exclude the stop codon from their ``CDS``
#: records, so the codon span already covers them. NCBI includes it --
#: folding both sources together answers the same under either
#: convention, so this carries no assumption about flavour.
GTF_CDS_FEATURES = frozenset({
    "CDS",
})

#: Features that mark a site within their transcript and contribute
#: nothing to the model. GENCODE emits one ``Selenocysteine`` record per
#: recoded UGA codon of a selenoprotein. Taking no measurement from them
#: is a redundancy, not a policy: every such site falls inside a ``CDS``
#: record of the same transcript, so ``cds`` already covers it -- all 130
#: records across the 88 selenoproteins of GENCODE v49 comprehensive.
#: Dispatched as child records, so that a record with no parent
#: transcript is reported rather than silently turned into a transcript
#: of its own.
GTF_SELENOCYSTEINE_FEATURES = frozenset({
    "Selenocysteine",
})


def _record_location(rec: dict) -> str:
    """Identify a GTF record by feature and position.

    For the errors that cannot lean on the ``transcript_id`` that
    ``_parent_transcript``'s messages use, because the attributes are
    themselves what is missing.
    """
    return (
        f"{rec['feature']} record at "
        f"{rec['seqname']}:{rec['start']}-{rec['end']}"
    )


def _parent_transcript(
    transcript_models: dict[str, TranscriptModel],
    feature: str,
    tr_id: str,
    skipped_transcripts: dict[str, str],
) -> TranscriptModel:
    """Fetch the parent transcript model of a child record.

    A child whose parent transcript is absent is an error naming the
    transcript and why it is absent: never seen, or seen but skipped
    as an exonless feature.
    """
    transcript_model = transcript_models.get(tr_id)
    if transcript_model is not None:
        return transcript_model
    if tr_id in skipped_transcripts:
        raise ValueError(
            f"{feature} transcript {tr_id} was skipped as "
            f"exonless feature {skipped_transcripts[tr_id]}",
        )
    raise ValueError(
        f"{feature} transcript {tr_id} not found in transcript models",
    )


def parse_gtf_gene_models_format(
    infile: IO,
    gene_mapping: dict[str, str] | None = None,
    nrows: int | None = None,
) -> dict[str, TranscriptModel] | None:
    """Parse GTF gene models file format."""
    # pylint: disable=too-many-locals,too-many-branches,too-many-statements
    expected_columns = [
        "seqname",
        "source",
        "feature",
        "start",
        "end",
        "score",
        "strand",
        "phase",
        "attributes",
        # "comments",
    ]

    infile.seek(0)
    df = parse_raw(infile, expected_columns, nrows=nrows, comment="#")
    if df is None:
        expected_columns.append("comment")
        infile.seek(0)
        df = parse_raw(infile, expected_columns, nrows=nrows, comment="#")
        if df is None:
            return None

    if gene_mapping is None:
        gene_mapping = {}
    transcript_models = {}
    skipped_transcripts: dict[str, str] = {}

    for rec in df.to_dict(orient="records"):
        feature = rec["feature"]
        if feature in GTF_IGNORED_FEATURES:
            continue
        # The scanner takes text, and this column does not always arrive
        # as text: a column that is numeric throughout is inferred as a
        # number, which would escape as an ``AttributeError`` naming a
        # float and neither the record nor the file (gain#907). A blank
        # column names the record; anything else is coerced and left to
        # the scanner, which rejects it as the malformed attribute it is.
        #
        # Blankness is decided on the text rather than by ``pd.isna``:
        # since gain#931 the read keeps a blank cell -- and a row that
        # stops short of this column -- as ``''``, which ``pd.isna``
        # does not report. Whitespace is deliberately not stripped here:
        # a cell holding spaces went to the scanner before and still does.
        attributes_column = rec["attributes"]
        if not cell_text(attributes_column):
            raise ValueError(
                f"{_record_location(rec)} has an empty attributes column",
            )
        attributes = _parse_gtf_attributes(str(attributes_column))
        if feature in GTF_EXONLESS_TRANSCRIPT_FEATURES:
            skipped_tr_id = attributes.get("transcript_id")
            if skipped_tr_id is not None:
                skipped_transcripts[skipped_tr_id] = feature
            continue
        # Absent is the error, present-but-empty is not: an empty value
        # is a value, and the parser has always taken it. Both guards
        # below turn on that distinction, so both test against ``None``.
        tr_id = attributes.get("transcript_id")
        if tr_id is None:
            raise ValueError(
                f"{_record_location(rec)} has no transcript_id attribute",
            )
        if feature in GTF_TRANSCRIPT_FEATURES:
            if tr_id in transcript_models:
                raise ValueError(
                    f"{tr_id} of {feature} already in transcript models",
                )
            # FlyBase labels genes with ``gene_symbol``; Ensembl and RefSeq
            # with ``gene_name``. Both fall back to the accession. Spelled
            # out rather than driven off a tuple of the three names:
            # ``or`` steps past an empty label where membership would
            # return it, so only the last spelling has to be there.
            gene = (
                attributes.get("gene_name")
                or attributes.get("gene_symbol")
                or attributes.get("gene_id")
            )
            if gene is None:
                raise ValueError(
                    f"{_record_location(rec)} has no usable gene label; "
                    "expected gene_name, gene_symbol or gene_id",
                )
            gene = gene_mapping.get(gene, gene)

            transcript_model = TranscriptModel(
                gene=gene,
                tr_id=tr_id,
                tr_name=tr_id,
                chrom=rec["seqname"],
                strand=rec["strand"],
                tx=(rec["start"], rec["end"]),
                cds=(rec["end"], rec["start"]),
                attributes=attributes,
            )
            assert transcript_model.tr_id not in transcript_models
            transcript_models[transcript_model.tr_id] = transcript_model
            continue
        if feature in GTF_EXON_FEATURES:
            transcript_model = _parent_transcript(
                transcript_models, feature, tr_id, skipped_transcripts)
            exon = Exon(
                rec["start"], rec["end"], frame=-1,
            )
            transcript_model.exons.append(exon)
            continue
        if feature in GTF_CODON_FEATURES or feature in GTF_CDS_FEATURES:
            transcript_model = _parent_transcript(
                transcript_models, feature, tr_id, skipped_transcripts)
            cds = transcript_model.cds
            transcript_model.cds = \
                (min(cds[0], rec["start"]), max(cds[1], rec["end"]))
            continue
        if feature in GTF_SELENOCYSTEINE_FEATURES:
            # Called for the parent check alone; nothing to record.
            _parent_transcript(
                transcript_models, feature, tr_id, skipped_transcripts)
            continue

        raise ValueError(
            f"unknown feature {feature} found in gtf gene models")

    # Known only now: a transcript's exons arrive as separate records,
    # so "this one has none" cannot be decided until the whole file has
    # been read. The early skip of GTF_EXONLESS_TRANSCRIPT_FEATURES is
    # keyed on the feature name and so cannot catch a transcript of any
    # other spelling that simply turns out to have no exons -- which
    # serialized to three blank exon columns that the read side then
    # refused, leaving gain unable to read back what it had written
    # (gain#965).
    #
    # Only on a whole-file parse, hence the `nrows` guard: format
    # inference parses a prefix, where the evidence for this simply is
    # not in yet. A feature-sorted GTF introduces its transcripts before
    # any of their exons, so dropping on prefix evidence emptied the
    # sample, and a well-formed file stopped being recognised as a GTF
    # at all unless its format was declared.
    parsed_whole_file = nrows is None
    if parsed_whole_file:
        parsed = len(transcript_models)
        exonless = [
            tr_id
            for tr_id, transcript_model in transcript_models.items()
            if not transcript_model.exons
        ]
        for tr_id in exonless:
            dropped = transcript_models.pop(tr_id)
            logger.warning(
                "dropping transcript %s at %s: it has no exon records",
                tr_id, dropped.chrom,
            )
        if exonless:
            logger.warning(
                "dropped %d of %d transcripts with no exon records",
                len(exonless), parsed,
            )

    for transcript_model in transcript_models.values():
        transcript_model.exons = sorted(
            transcript_model.exons, key=lambda x: x.start)
        transcript_model.update_frames()

    return transcript_models


def load_gene_mapping(resource: GenomicResource) -> dict[str, str]:
    """Load alternative names for genes.

    Assume that its first line has two column names
    """
    gene_mapping_filename = resource.get_config().get(
        "gene_mapping", None)
    if gene_mapping_filename is None:
        return {}
    compression = False
    if gene_mapping_filename.endswith(".gz"):
        compression = True
    with resource.open_raw_file(
            gene_mapping_filename, "rt",
            compression=compression) as infile:

        logger.debug(
            "loading gene mapping from %s", gene_mapping_filename)

        # Read as text, for the reasons `parse_raw` gives: a blank
        # replacement label became a float ``NaN`` and was written into
        # the gene column as the token ``nan``, and a label spelled
        # ``NA`` was rewritten as ``nan`` -- a value the file did give,
        # replaced by one it never did. Inference also typed a
        # bare-digit transcript id as a number, which no lookup by the
        # model's own string id could ever match (gain#931).
        df = read_gene_models_tsv(infile, dtype=str)
        assert len(df.columns) == 2

        df = df.rename(columns={df.columns[0]: "tr_id", df.columns[1]: "gene"})

        records = df.to_dict(orient="records")

        alt_names = {}
        for rec in records:
            rec = cast(dict, rec)
            alt_names[rec["tr_id"]] = rec["gene"]

        return alt_names


# The one place a format name is bound to a parser. What follows derives
# from it, so the two cannot be edited apart -- which matters because they
# are read by different callers, and a divergence between them was silent
# rather than loud. `infer_gene_models_format` iterates the supported names
# -- they are the inference candidate list -- while `get_parser` is the
# gate that decides whether a format named outright in a resource config is
# accepted at all. A name wired into one but not the other used to mean a
# format that loads when spelled explicitly and is never inferred from
# content, with no complaint at either seam.
#
# Both are settled at import -- the set is a snapshot of these keys, and
# each value is the function object rather than a name resolved per call.
# So rebinding a parser's module attribute no longer reaches `get_parser`:
# a test patching one and exercising it through here runs unpatched code.
_PARSERS: dict[str, GeneModelsParser] = {
    "default": parse_default_gene_models_format,
    "refflat": parse_ref_flat_gene_models_format,
    "refseq": parse_ref_seq_gene_models_format,
    "ccds": parse_ccds_gene_models_format,
    "knowngene": parse_known_gene_models_format,
    "gtf": parse_gtf_gene_models_format,
    "ucscgenepred": parse_ucscgenepred_models_format,
}

SUPPORTED_GENE_MODELS_FILE_FORMATS: set[str] = set(_PARSERS)


def get_parser(
    fileformat: str,
) -> GeneModelsParser | None:
    """Get gene models parser based on file format."""
    return _PARSERS.get(fileformat)


INFERENCE_SAMPLE_ROWS = 50

# refseq and ccds declare identical column layouts, so a headerless file
# matching one matches the other; only the content of the transcript-name
# column can separate them.
_REFSEQ_TRANSCRIPT_NAME = re.compile(r"[NX][MR]_\d+(\.\d+)?")
_CCDS_TRANSCRIPT_NAME = re.compile(r"CCDS\d+(\.\d+)?")

# The evidence a tie-break verdict rests on, by winning format.
_TIE_BREAK_EVIDENCE = {
    "refseq": "every sampled transcript name is a RefSeq accession",
    "ccds": "every sampled transcript name is a CCDS id",
}


def _break_refseq_ccds_tie(infile: IO, sampled_rows: int) -> str | None:
    """Choose between refseq and ccds by transcript-name content.

    Returns the winning format, or None when the sampled names do not all
    share one format's accession shape.
    """
    # header=None keeps a header row, if there is one, in the sample where
    # the explicit check below can see it. na_filter=False keeps a blank
    # name field a string, so it fails both shapes instead of crashing the
    # match -- belt and braces since gain#929, which refuses a record with
    # a blank name outright: the tie-break runs only once both formats
    # have parsed records, so such a file no longer reaches it.
    infile.seek(0)
    names = read_gene_models_tsv(
        infile, header=None, usecols=[1], dtype=str,
        nrows=sampled_rows,
    )[1].tolist()
    assert names, "the tie-break runs only after both formats parsed records"
    if names[0] == "name":
        # A headered file collides on this pair too, and headered files
        # keep their pre-tie-break behavior: the header row opts out.
        return None
    if all(_REFSEQ_TRANSCRIPT_NAME.fullmatch(name) for name in names):
        return "refseq"
    if all(_CCDS_TRANSCRIPT_NAME.fullmatch(name) for name in names):
        return "ccds"
    return None


def _describe_exception(ex: Exception) -> str:
    """Render an exception as a rejection reason.

    Some parsers reject through an exception carrying no message at all --
    naming the type keeps the ledger free of blank entries. The message
    quotes text read out of the file, and the ledger it joins is itself
    newline-structured, so a raw line break in it would forge a ledger
    line; escape it the way the repository modules do.
    """
    message = escape_unsafe_characters(str(ex).strip())
    if not message:
        return f"{type(ex).__name__} (no message)"
    return f"{type(ex).__name__}: {message}"


@dataclass(frozen=True)
class FormatInference:
    """What trying every supported format against a file prefix established.

    A format rejects a file through one of two channels: it raises, or it
    quietly returns no transcript models. Both end up in `rejected`, so the
    ledger has no holes -- a reason the reader cannot see is the whole of
    gain#856.
    """

    matched: tuple[str, ...]
    rejected: tuple[tuple[str, str], ...]
    sampled_rows: int
    tie_break: str | None = None

    def __post_init__(self) -> None:
        if self.tie_break is not None and self.tie_break not in self.matched:
            raise ValueError(
                f"tie-break winner {self.tie_break!r} is not among the "
                f"matched formats {self.matched!r}")

    @property
    def file_format(self) -> str | None:
        """The inferred format, or None when the file stays ambiguous.

        Exactly one matching format is an inference; a multi-format
        collision resolved by a content tie-break is one too.
        """
        if len(self.matched) == 1:
            return self.matched[0]
        return self.tie_break

    def report(self) -> str:
        """Render why inference did not settle on a single format."""
        if self.file_format is not None:
            headline = (
                f"the format is {self.file_format}, from the first "
                f"{self.sampled_rows} records"
            )
        elif len(self.matched) > 1:
            headline = (
                f"{len(self.matched)} formats match the first "
                f"{self.sampled_rows} records, so the format is ambiguous: "
                f"{', '.join(self.matched)}"
            )
        else:
            headline = (
                f"no supported format matches the first "
                f"{self.sampled_rows} records"
            )
        lines = [headline, "formats tried:"]
        for fmt in self.matched:
            line = f"  {fmt}: matched the sampled records"
            if self.tie_break is not None and fmt != self.tie_break:
                line += (
                    " but lost the content tie-break: "
                    f"{_TIE_BREAK_EVIDENCE[self.tie_break]}"
                )
            lines.append(line)
        lines.extend(
            f"  {fmt}: {reason}" for fmt, reason in self.rejected
        )
        lines.append(
            f"note: inference reads only the first {self.sampled_rows} "
            "records, so a format matching here is not evidence that the "
            "whole file parses",
        )
        return "\n".join(lines)


def infer_gene_models_format(infile: IO) -> FormatInference:
    """Try every supported format against a prefix of `infile`."""
    sampled_rows = INFERENCE_SAMPLE_ROWS
    logger.info("going to infer gene models file format...")
    matched: list[str] = []
    rejected: list[tuple[str, str]] = []
    for candidate in sorted(SUPPORTED_GENE_MODELS_FILE_FORMATS):
        parser = get_parser(candidate)
        if parser is None:
            continue
        logger.debug("trying file format: %s...", candidate)
        try:
            infile.seek(0)
            res = parser(infile, None, sampled_rows)
        except Exception as ex:  # noqa: BLE001 pylint: disable=broad-except
            logger.debug(
                "file format %s does not match; %s",
                candidate, ex, exc_info=True)
            rejected.append((candidate, _describe_exception(ex)))
            continue
        if res:
            matched.append(candidate)
            logger.debug("gene models format %s matches input", candidate)
        elif res is None:
            rejected.append(
                (candidate, "does not have this format's column layout"))
        else:
            rejected.append(
                (candidate,
                 "has this format's column layout but yielded no "
                 "transcript models"))

    tie_break = None
    # matched is sorted by construction -- the loop iterates the supported
    # formats in sorted order.
    if matched == ["ccds", "refseq"]:
        tie_break = _break_refseq_ccds_tie(infile, sampled_rows)

    logger.info("inferred file formats: %s", matched)
    return FormatInference(
        matched=tuple(matched),
        rejected=tuple(rejected),
        sampled_rows=sampled_rows,
        tie_break=tie_break,
    )


def infer_gene_model_parser(
    infile: IO,
    file_format: str | None = None,
) -> str | None:
    """Infer gene models file format."""
    if file_format is not None:
        parser = get_parser(file_format)
        if parser is not None:
            return file_format

    inference = infer_gene_models_format(infile)
    if inference.file_format is not None:
        return inference.file_format

    logger.warning("can't infer gene models file format; %s",
                   inference.report())
    return None


def load_transcript_models(
    resource: GenomicResource,
) -> dict[str, TranscriptModel]:
    """Load gene models."""
    assert resource.get_type() == "gene_models"

    filename = resource.get_config()["filename"]
    fileformat = resource.get_config().get("format", None)

    gene_mapping = load_gene_mapping(resource)

    logger.debug("loading gene models %s (%s)", filename, fileformat)
    compression = False

    if filename.endswith(".gz"):
        compression = True
    with resource.open_raw_file(
            filename, mode="rt", compression=compression) as infile:

        if fileformat is None:
            inference = infer_gene_models_format(infile)
            fileformat = inference.file_format
            # A resource built straight from a local file has no meaningful
            # id, and naming it adds noise rather than help. Both spellings
            # of the repository root are per
            # `repository.uncontained_resource_id_reason`.
            identity = escape_unsafe_characters(filename)
            if resource.resource_id not in {"", "."}:
                identity = (
                    f"{identity} (resource "
                    f"{escape_unsafe_characters(resource.resource_id)})")
            if fileformat is None:
                report = inference.report()
                logger.error(
                    "can't infer gene models file format for %s; %s",
                    identity, report)
                raise ValueError(
                    f"can't infer gene models file format for {identity}; "
                    f"{report}")
            # Inference read a prefix. Saying so is the difference between
            # "this file is gtf" and "the first records of it are" -- a
            # malformed record past them loads silently corrupted.
            logger.info(
                "inferred gene models file format %s for %s from only the "
                "first %d records; that is not evidence that the rest of "
                "the file parses",
                fileformat, identity, inference.sampled_rows)

        parser = get_parser(fileformat)
        if parser is None:
            logger.error(
                "Unsupported file format %s for "
                "gene model file %s.", fileformat,
                resource.resource_id)
            raise ValueError

        infile.seek(0)
        transcript_models = parser(
            infile, gene_mapping, None)
        if transcript_models is None:
            raise ValueError(
                f"Failed to parse gene models file {filename} "
                f"with format {fileformat}")
    return transcript_models
