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


#: How much of a cell to quote back when reporting it. Shared with
#: `_scan_gtf_attributes`, so that the two messages truncate alike.
_QUOTED_TEXT_LIMIT = 60


def _parse_exon_positions(
    value: Any, column: str, tr_name: object, chrom: object,
) -> list[int]:
    """Read a comma-separated coordinate column, naming its record.

    pandas delivers a blank cell as a float ``NaN``, which used to reach
    ``str.strip`` and escape as an ``AttributeError`` naming a float
    (gain#907). Text that is simply not a coordinate list fails ``int``
    the same way, and leaves the reader just as stuck, so both are
    reported here as one thing: this record's column could not be read.

    Where a GTF record has to be placed by feature and position -- its
    ``transcript_id`` being what tends to be missing -- a columnar record
    is named by the transcript name and chromosome every columnar layout
    carries in columns of their own.

    The quoted cell is what pandas made of the column, not the file's own
    bytes -- see `_cell_text`. The ``int`` failure stays on the chain, so
    the offending token survives the truncation.
    """
    # This runs once per record per column on files that reach into the
    # hundreds of thousands of records, so the well-formed cell -- a
    # string, always -- takes the cheapest path through, and the message
    # is not built until there is a message to build.
    text = _cell_text(value)
    try:
        return list(map(int, text.strip(",").split(",")))
    except ValueError as ex:
        raise _unparsable(column, tr_name, chrom, text) from ex


def _cell_text(value: Any) -> str:
    """Render what pandas made of a cell as the text the file held.

    A blank cell arrives as a float ``NaN`` and reads back as ``''``, and
    so does every other spelling pandas takes for a missing value, ``NA``
    and ``NULL`` among them, which the messages built from this therefore
    cannot tell apart (gain#931).

    ``value`` is annotated ``Any`` rather than ``object`` because
    ``pd.isna`` has no overload for the latter.
    """
    return value if isinstance(value, str) else \
        "" if pd.isna(value) else str(value)


def _unparsable(
    column: str, tr_name: object, chrom: object, text: str,
) -> ValueError:
    """Report a cell that a record cannot be built from."""
    return ValueError(
        f"transcript {tr_name} at {chrom} has an unparsable "
        f"{column} column: {text[:_QUOTED_TEXT_LIMIT]!r}",
    )


def _parse_coordinate(
    value: Any, column: str, tr_name: object, chrom: object,
) -> int:
    """Read a single coordinate column, naming its record.

    The columnar layouts already wrapped these in ``int()``, which does
    reject a blank cell -- but as ``cannot convert float NaN to
    integer``, naming neither the record nor the column, and the gain#856
    ledger then offers that to the reader as the reason a format was
    rejected. The default format did not convert at all, so a blank cell
    became a transcript bound of ``NaN`` (gain#929).

    A cell pandas has already typed as a number is converted from the
    number rather than from its text, because that is what the parsers
    did before this guard: a column spelled ``100.0`` throughout is
    typed float, and ``int(100.0)`` is what kept it parsing.
    """
    text = _cell_text(value)
    try:
        return int(value) if text and not isinstance(value, str) \
            else int(text)
    except (TypeError, ValueError) as ex:
        raise _unparsable(column, tr_name, chrom, text) from ex


def _parse_exon_bounds(
    rec: dict, tr_name: object, chrom: object,
) -> tuple[list[int], list[int]]:
    """Read the paired exon-position columns of a columnar record.

    Every columnar layout but the default one spells the pair the same
    way, so they share this rather than repeating the pair of reads and
    the length check between them.
    """
    exon_starts = _parse_exon_positions(
        rec["exonStarts"], "exonStarts", tr_name, chrom)
    exon_ends = _parse_exon_positions(
        rec["exonEnds"], "exonEnds", tr_name, chrom)
    assert len(exon_starts) == len(exon_ends)
    return exon_starts, exon_ends


def _parse_transcript_bounds(
    rec: dict, tr_name: object, chrom: object,
) -> tuple[tuple[int, int], tuple[int, int]]:
    """Read the transcript and coding bounds of a columnar record.

    The five UCSC-derived layouts spell these four columns the same way
    and share the half-open convention that shifts each start by one, so
    they share this rather than repeating it between them (gain#941).
    """
    tx = (
        _parse_coordinate(rec["txStart"], "txStart", tr_name, chrom) + 1,
        _parse_coordinate(rec["txEnd"], "txEnd", tr_name, chrom))
    cds = (
        _parse_coordinate(rec["cdsStart"], "cdsStart", tr_name, chrom) + 1,
        _parse_coordinate(rec["cdsEnd"], "cdsEnd", tr_name, chrom))
    return tx, cds


def parse_default_gene_models_format(
    infile: IO,
    gene_mapping: dict[str, str] | None = None,
    nrows: int | None = None,
) -> dict[str, TranscriptModel] | None:
    """Parse default gene models file format."""
    # pylint: disable=too-many-locals
    infile.seek(0)
    df = pd.read_csv(
        infile,
        sep="\t",
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
    for line in records:
        line = cast(dict, line)
        tr_name, chrom = line["trID"], line["chr"]
        exon_starts = _parse_exon_positions(
            line["exonStarts"], "exonStarts", tr_name, chrom)
        exon_ends = _parse_exon_positions(
            line["exonEnds"], "exonEnds", tr_name, chrom)
        exon_frames = _parse_exon_positions(
            line["exonFrames"], "exonFrames", tr_name, chrom)
        assert len(exon_starts) == len(exon_ends) == len(exon_frames)

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
            tr_id=line["trID"],
            tr_name=line["trOrigId"],
            chrom=line["chr"],
            strand=line["strand"],
            tx=(_parse_coordinate(line["tsBeg"], "tsBeg", tr_name, chrom),
                _parse_coordinate(line["txEnd"], "txEnd", tr_name, chrom)),
            cds=(_parse_coordinate(
                     line["cdsStart"], "cdsStart", tr_name, chrom),
                 _parse_coordinate(
                     line["cdsEnd"], "cdsEnd", tr_name, chrom)),
            exons=exons,
            attributes=attributes,
        )
        transcript_models[transcript_model.tr_id] = transcript_model
    return transcript_models


def parse_ref_flat_gene_models_format(
    infile: IO,
    gene_mapping: dict[str, str] | None = None,
    nrows: int | None = None,
) -> dict[str, TranscriptModel] | None:
    """Parse refFlat gene models file format."""
    # pylint: disable=too-many-locals
    expected_columns = [
        "#geneName",
        "name",
        "chrom",
        "strand",
        "txStart",
        "txEnd",
        "cdsStart",
        "cdsEnd",
        "exonCount",
        "exonStarts",
        "exonEnds",
    ]

    infile.seek(0)
    df = parse_raw(infile, expected_columns, nrows=nrows)
    if df is None:
        return None

    records = df.to_dict(orient="records")

    transcript_ids_counter: dict[str, int] = defaultdict(int)
    if gene_mapping is None:
        gene_mapping = {}

    transcript_models = {}
    for rec in records:
        gene = rec["#geneName"]
        gene = gene_mapping.get(gene, gene)
        tr_name = rec["name"]
        chrom = rec["chrom"]
        strand = rec["strand"]
        tx, cds = _parse_transcript_bounds(  # pylint: disable=invalid-name
            rec, tr_name, chrom)

        exon_starts, exon_ends = _parse_exon_bounds(
            rec, tr_name, chrom)

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
        )
        transcript_model.update_frames()
        assert transcript_model.tr_id not in transcript_models
        transcript_models[transcript_model.tr_id] = transcript_model

    return transcript_models


def parse_ref_seq_gene_models_format(
    infile: IO,
    gene_mapping: dict[str, str] | None = None,
    nrows: int | None = None,
) -> dict[str, TranscriptModel] | None:
    """Parse refSeq gene models file format."""
    # pylint: disable=too-many-locals
    expected_columns = [
        "#bin",
        "name",
        "chrom",
        "strand",
        "txStart",
        "txEnd",
        "cdsStart",
        "cdsEnd",
        "exonCount",
        "exonStarts",
        "exonEnds",
        "score",
        "name2",
        "cdsStartStat",
        "cdsEndStat",
        "exonFrames",
    ]

    infile.seek(0)
    df = parse_raw(infile, expected_columns, nrows=nrows)
    if df is None:
        return None

    records = df.to_dict(orient="records")

    transcript_ids_counter: dict[str, int] = defaultdict(int)
    if gene_mapping is None:
        gene_mapping = {}
    transcript_models = {}
    for rec in records:
        gene = rec["name2"]
        gene = gene_mapping.get(gene, gene)

        tr_name = rec["name"]
        chrom = rec["chrom"]
        strand = rec["strand"]
        tx, cds = _parse_transcript_bounds(  # pylint: disable=invalid-name
            rec, tr_name, chrom)

        exon_starts, exon_ends = _parse_exon_bounds(
            rec, tr_name, chrom)

        exons = [
            Exon(start + 1, end)
            for start, end in zip(exon_starts, exon_ends, strict=True)
        ]

        transcript_ids_counter[tr_name] += 1
        tr_id = f"{tr_name}_{transcript_ids_counter[tr_name]}"

        attributes = {
            k: rec[k]
            for k in [
                "#bin",
                "score",
                "exonCount",
                "cdsStartStat",
                "cdsEndStat",
                "exonFrames",
            ]
        }
        transcript_model = TranscriptModel(
            gene=gene,
            tr_id=tr_id,
            tr_name=tr_name,
            chrom=chrom,
            strand=strand,
            tx=tx,
            cds=cds,
            exons=exons,
            attributes=attributes,
        )
        transcript_model.update_frames()
        assert transcript_model.tr_id not in transcript_models
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


def parse_raw(
    infile: IO, expected_columns: list[str],
    nrows: int | None = None, comment: str | None = None,
) -> pd.DataFrame | None:
    """Parse raw gene models data based on expected columns."""
    if probe_header(infile, expected_columns, comment=comment):
        infile.seek(0)
        df = pd.read_csv(
            infile, sep="\t", nrows=nrows, comment=comment,
            dtype=str,
        )
        assert list(df.columns) == expected_columns
        return df

    if probe_columns(infile, expected_columns, comment=comment):
        infile.seek(0)
        df = pd.read_csv(
            infile,
            sep="\t",
            nrows=nrows,
            header=None,
            names=expected_columns,
            comment=comment,
        )
        assert list(df.columns) == expected_columns
        return df
    return None


def parse_ccds_gene_models_format(
    infile: IO,
    gene_mapping: dict[str, str] | None = None,
    nrows: int | None = None,
) -> dict[str, TranscriptModel] | None:
    """Parse CCDS gene models file format."""
    # pylint: disable=too-many-locals
    expected_columns = [
        # CCDS is identical with RefSeq
        "#bin",
        "name",
        "chrom",
        "strand",
        "txStart",
        "txEnd",
        "cdsStart",
        "cdsEnd",
        "exonCount",
        "exonStarts",
        "exonEnds",
        "score",
        "name2",
        "cdsStartStat",
        "cdsEndStat",
        "exonFrames",
    ]

    infile.seek(0)
    df = parse_raw(infile, expected_columns, nrows=nrows)
    if df is None:
        return None

    records = df.to_dict(orient="records")

    transcript_ids_counter: dict[str, int] = defaultdict(int)
    if gene_mapping is None:
        gene_mapping = {}

    transcript_models = {}
    for rec in records:
        gene = rec["name"]
        gene = gene_mapping.get(gene, gene)

        tr_name = rec["name"]
        chrom = rec["chrom"]
        strand = rec["strand"]
        tx, cds = _parse_transcript_bounds(  # pylint: disable=invalid-name
            rec, tr_name, chrom)

        exon_starts, exon_ends = _parse_exon_bounds(
            rec, tr_name, chrom)

        exons = [
            Exon(start + 1, end)
            for start, end in zip(exon_starts, exon_ends, strict=True)
        ]

        transcript_ids_counter[tr_name] += 1
        tr_id = f"{tr_name}_{transcript_ids_counter[tr_name]}"

        attributes = {
            k: rec[k]
            for k in [
                "#bin",
                "score",
                "exonCount",
                "cdsStartStat",
                "cdsEndStat",
                "exonFrames",
            ]
        }
        transcript_model = TranscriptModel(
            gene=gene,
            tr_id=tr_id,
            tr_name=tr_name,
            chrom=chrom,
            strand=strand,
            tx=tx,
            cds=cds,
            exons=exons,
            attributes=attributes,
        )
        transcript_model.update_frames()
        assert transcript_model.tr_id not in transcript_models
        transcript_models[transcript_model.tr_id] = transcript_model

    return transcript_models


def parse_known_gene_models_format(
    infile: IO,
    gene_mapping: dict[str, str] | None = None,
    nrows: int | None = None,
) -> dict[str, TranscriptModel] | None:
    """Parse known gene models file format."""
    # pylint: disable=too-many-locals
    expected_columns = [
        "name",
        "chrom",
        "strand",
        "txStart",
        "txEnd",
        "cdsStart",
        "cdsEnd",
        "exonCount",
        "exonStarts",
        "exonEnds",
        "proteinID",
        "alignID",
    ]

    infile.seek(0)
    df = parse_raw(infile, expected_columns, nrows=nrows)
    if df is None:
        return None

    records = df.to_dict(orient="records")

    transcript_ids_counter: dict[str, int] = defaultdict(int)

    if gene_mapping is None:
        gene_mapping = {}
    transcript_models = {}
    for rec in records:
        gene = rec["name"]
        gene = gene_mapping.get(gene, gene)

        tr_name = rec["name"]
        chrom = rec["chrom"]
        strand = rec["strand"]
        tx, cds = _parse_transcript_bounds(  # pylint: disable=invalid-name
            rec, tr_name, chrom)

        exon_starts, exon_ends = _parse_exon_bounds(
            rec, tr_name, chrom)

        exons = [
            Exon(start + 1, end)
            for start, end in zip(exon_starts, exon_ends, strict=True)
        ]

        transcript_ids_counter[tr_name] += 1
        tr_id = f"{tr_name}_{transcript_ids_counter[tr_name]}"

        attributes = {k: rec[k] for k in ["proteinID", "alignID"]}
        transcript_model = TranscriptModel(
            gene=gene,
            tr_id=tr_id,
            tr_name=tr_name,
            chrom=chrom,
            strand=strand,
            tx=tx,
            cds=cds,
            exons=exons,
            attributes=attributes,
        )
        transcript_model.update_frames()
        assert transcript_model.tr_id not in transcript_models
        transcript_models[transcript_model.tr_id] = transcript_model

    return transcript_models


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
    # pylint: disable=too-many-locals
    expected_columns = [
        "name",
        "chrom",
        "strand",
        "txStart",
        "txEnd",
        "cdsStart",
        "cdsEnd",
        "exonCount",
        "exonStarts",
        "exonEnds",
        "score",
        "name2",
        "cdsStartStat",
        "cdsEndStat",
        "exonFrames",
    ]

    infile.seek(0)
    df = parse_raw(infile, expected_columns[:10], nrows=nrows)
    if df is None:
        infile.seek(0)
        df = parse_raw(infile, expected_columns, nrows=nrows)
        if df is None:
            return None

    records = df.to_dict(orient="records")

    transcript_ids_counter: dict[str, int] = defaultdict(int)
    if gene_mapping is None:
        gene_mapping = {}
    transcript_models = {}
    for rec in records:
        gene = rec.get("name2")
        if not gene:
            gene = rec["name"]
        gene = gene_mapping.get(gene, gene)

        tr_name = rec["name"]
        chrom = rec["chrom"]
        strand = rec["strand"]
        tx, cds = _parse_transcript_bounds(  # pylint: disable=invalid-name
            rec, tr_name, chrom)

        exon_starts, exon_ends = _parse_exon_bounds(
            rec, tr_name, chrom)

        exons = [
            Exon(start + 1, end)
            for start, end in zip(exon_starts, exon_ends, strict=True)
        ]

        transcript_ids_counter[tr_name] += 1
        tr_id = f"{tr_name}_{transcript_ids_counter[tr_name]}"

        attributes = {}
        for attr in expected_columns[10:]:
            if attr in rec:
                attributes[attr] = rec.get(attr)
        transcript_model = TranscriptModel(
            gene=gene,
            tr_id=tr_id,
            tr_name=tr_name,
            chrom=chrom,
            strand=strand,
            tx=tx,
            cds=cds,
            exons=exons,
            attributes=attributes,
        )
        transcript_model.update_frames()
        assert transcript_model.tr_id not in transcript_models
        transcript_models[transcript_model.tr_id] = transcript_model

    return transcript_models


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
                    f"{data[index:index + _QUOTED_TEXT_LIMIT]!r}",
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
#: sequence. This is a policy about these two spellings, not an invariant the
#: parser enforces -- any accepted feature with no ``exon`` child still yields
#: an empty exon list. Moving one of these into ``GTF_TRANSCRIPT_FEATURES``
#: should be deliberate, not a silent behaviour change.
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
    df = parse_raw(
        infile, expected_columns, nrows=nrows, comment="#")
    if df is None:
        expected_columns.append("comment")
        infile.seek(0)
        df = parse_raw(
            infile, expected_columns, nrows=nrows, comment="#")
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
        # as text: pandas reads a blank cell as a float ``NaN`` (whether
        # the row is short or ends on an empty column), and infers a
        # numeric dtype for a column that is numeric throughout. Either
        # would escape as an ``AttributeError`` naming a float and neither
        # the record nor the file, which is gain#907. A blank column names
        # the record; anything else is coerced and left to the scanner,
        # which rejects it as the malformed attribute it is.
        attributes_column = rec["attributes"]
        if pd.isna(attributes_column):
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

        df = pd.read_csv(infile, sep="\t")
        assert len(df.columns) == 2

        df = df.rename(columns={df.columns[0]: "tr_id", df.columns[1]: "gene"})

        records = df.to_dict(orient="records")

        alt_names = {}
        for rec in records:
            rec = cast(dict, rec)
            alt_names[rec["tr_id"]] = rec["gene"]

        return alt_names


SUPPORTED_GENE_MODELS_FILE_FORMATS: set[str] = {
    "default",
    "refflat",
    "refseq",
    "ccds",
    "knowngene",
    "gtf",
    "ucscgenepred",
}


def get_parser(
    fileformat: str,
) -> GeneModelsParser | None:
    """Get gene models parser based on file format."""
    # pylint: disable=too-many-return-statements
    if fileformat == "default":
        return parse_default_gene_models_format
    if fileformat == "refflat":
        return parse_ref_flat_gene_models_format
    if fileformat == "refseq":
        return parse_ref_seq_gene_models_format
    if fileformat == "ccds":
        return parse_ccds_gene_models_format
    if fileformat == "knowngene":
        return parse_known_gene_models_format
    if fileformat == "gtf":
        return parse_gtf_gene_models_format
    if fileformat == "ucscgenepred":
        return parse_ucscgenepred_models_format
    return None


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
    # match.
    infile.seek(0)
    names = pd.read_csv(
        infile, sep="\t", header=None, usecols=[1], dtype=str,
        nrows=sampled_rows, na_filter=False,
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
