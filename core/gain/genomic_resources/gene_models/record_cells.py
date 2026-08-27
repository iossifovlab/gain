"""Reading one cell of a columnar gene-models record, and refusing it.

The gene-models parsers all face the same problem: a cell arrives as
whatever pandas made of it, and a record built from a cell that cannot be
read is worse than no record at all. What a bad cell should produce is
one thing -- a `ValueError` naming the record and the column -- and it is
gathered here rather than repeated in each parser (gain#907, gain#929).

Two shapes of message live here, because a record is named by two of its
own columns:

* Once the transcript name and chromosome are known, everything else is
  reported against them: ``transcript NM_000546 at chr17 has ...``.
* Those two cannot be named that way themselves, so each falls back to
  the record's position in the file, plus whichever of the pair is
  readable: ``gene models record 2 at chr17 has a blank name column``.

The layouts that use these are near-duplicates of one another, and
driving them from a table instead is gain#941; this module is what such a
table would call.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

#: How much of a cell to quote back when reporting it. Shared with
#: `_scan_gtf_attributes`, so that the two messages truncate alike.
QUOTED_TEXT_LIMIT = 60


def cell_text(value: Any) -> str:
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


def unparsable(
    column: str, tr_name: object, chrom: object, text: str,
) -> ValueError:
    """Report a cell that a record cannot be built from."""
    return ValueError(
        f"transcript {tr_name} at {chrom} has an unparsable "
        f"{column} column: {text[:QUOTED_TEXT_LIMIT]!r}",
    )


def parse_exon_positions(
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
    bytes -- see `cell_text`. The ``int`` failure stays on the chain, so
    the offending token survives the truncation.
    """
    # This runs once per record per column on files that reach into the
    # hundreds of thousands of records, so the well-formed cell -- a
    # string, always -- takes the cheapest path through, and the message
    # is not built until there is a message to build.
    text = cell_text(value)
    try:
        return list(map(int, text.strip(",").split(",")))
    except ValueError as ex:
        raise unparsable(column, tr_name, chrom, text) from ex


def parse_coordinate(
    value: Any, column: str, tr_name: object, chrom: object,
) -> int:
    """Read a single coordinate column, naming its record.

    The columnar layouts already wrapped these in ``int()``, which does
    reject a blank cell -- but as ``cannot convert float NaN to
    integer``, naming neither the record nor the column, and the gain#856
    ledger then offers that to the reader as the reason a format was
    rejected. The default format did not convert at all, so a blank cell
    became a transcript bound of ``NaN`` (gain#929).

    A coordinate spelled ``100.0`` is read as ``100`` whether it arrives
    as text or as a number. It used to be only the latter: a column
    spelled that way throughout was inferred as float on the headerless
    path and ``int(100.0)`` kept it parsing, while the headered path
    pinned it to text and ``int("100.0")`` did not, so the same file
    parsed or failed depending on which branch recognised it. Since
    gain#931 reads every columnar cell as text, the text conversion is
    the one that has to accept both spellings.

    ``OverflowError`` is caught alongside the rest because ``inf`` is a
    coordinate pandas accepts and ``int()`` will not take. It reaches
    here only on the read path that infers a float column, so without it
    the two paths report the same file differently -- and the one that
    escaped named neither the record nor the column.

    This runs four times per record on files reaching into the hundreds
    of thousands, so the numeric cell -- the common one, since nothing
    pins these columns to a string dtype -- converts straight from the
    number, and its text is built only if there turns out to be a
    message to build.
    """
    if isinstance(value, str):
        text = value
    elif pd.isna(value):
        text = ""
    else:
        try:
            return int(value)
        except (TypeError, ValueError, OverflowError) as ex:
            raise unparsable(column, tr_name, chrom, str(value)) from ex
    try:
        return int(text)
    except ValueError:
        pass
    # ``int`` will not take the text of a whole number spelled as a
    # decimal, so the float conversion is what reads ``100.0``. It is
    # tried second because it also accepts spellings a coordinate column
    # has no business holding -- ``nan`` and ``inf`` among them, which
    # ``int`` then refuses, the latter by ``OverflowError``.
    try:
        return int(float(text))
    except (ValueError, OverflowError) as ex:
        raise unparsable(column, tr_name, chrom, text) from ex


def record_identity(
    rec: dict, record: int, name_column: str, chrom_column: str,
) -> tuple[Any, Any]:
    """Read the two columns that say which record a columnar row is.

    Both used to be taken as they came. A blank one became a float
    ``NaN`` in the model: a ``NaN`` chromosome keys the transcript index
    all by itself, so the record is unreachable by every location query,
    and a ``NaN`` transcript name reaches serialization as the literal
    token ``nan`` -- and is suffixed into a transcript id of ``nan_1``,
    an identifier no file ever carried (gain#929).

    These two are what every other message here names a record by, so
    they cannot be named that way themselves. Each falls back to the
    record's position in the file, plus whichever of the pair is still
    readable.

    Blankness is decided on the text, but what is returned is the cell
    pandas handed over, untouched. The headerless read path infers a
    dtype, so a chromosome column of bare digits comes back as an int --
    arguably the wrong type, since a transcript index keyed by the int
    17 is unreachable by a query for "17", but it is what these files
    produce today. Normalising it belongs to gain#931's work at the read
    boundary; a guard meant only to reject must not re-type a cell that
    parses.
    """
    name_text = cell_text(rec[name_column])
    chrom_text = cell_text(rec[chrom_column])
    if not name_text.strip():
        raise _blank_identifier(
            name_column, record,
            f" at {chrom_text}" if chrom_text.strip() else "")
    if not chrom_text.strip():
        raise _blank_identifier(
            chrom_column, record, f" (transcript {name_text})")
    return rec[name_column], rec[chrom_column]


def _blank_identifier(column: str, record: int, context: str) -> ValueError:
    """Report an identifying column that the record cannot be named by."""
    return ValueError(
        f"gene models record {record}{context} "
        f"has a blank {column} column",
    )


def require_cell(
    value: Any, column: str, tr_name: object, chrom: object,
) -> Any:
    """Read a load-bearing text column of an already-identified record.

    Blank is refused rather than carried: a strand reaches
    ``update_frames()``, so a record without one does not merely hold an
    odd value -- its exon frames come out as if it had a strand, and the
    output is quietly wrong rather than missing (gain#929).

    Blankness is decided on the text, but what is returned is the cell
    pandas handed over, untouched -- see `record_identity`.
    """
    if not cell_text(value).strip():
        raise ValueError(
            f"transcript {tr_name} at {chrom} "
            f"has a blank {column} column",
        )
    return value


def parse_exon_bounds(
    rec: dict, tr_name: object, chrom: object,
) -> tuple[list[int], list[int]]:
    """Read the paired exon-position columns of a columnar record.

    Every columnar layout but the default one spells the pair the same
    way, so they share this rather than repeating the pair of reads and
    the length check between them.
    """
    exon_starts = parse_exon_positions(
        rec["exonStarts"], "exonStarts", tr_name, chrom)
    exon_ends = parse_exon_positions(
        rec["exonEnds"], "exonEnds", tr_name, chrom)
    require_equal_exon_counts(
        tr_name, chrom,
        exonStarts=exon_starts, exonEnds=exon_ends)
    return exon_starts, exon_ends


def require_equal_exon_counts(
    tr_name: object, chrom: object, **columns: list[int],
) -> None:
    """Refuse a record whose exon columns disagree on how many exons.

    This was a bare ``assert``, which carries no message; the gain#856
    ledger renders whatever a parser raised, so what reached the reader
    as the reason a format was rejected was ``AssertionError (no
    message)``. Naming the record and the counts costs nothing and is the
    whole of what the reader needed.

    The counts themselves are only tallied once they disagree: this runs
    once per record, and the records that reach it agree.
    """
    lengths = iter(columns.values())
    expected = len(next(lengths))
    if all(len(values) == expected for values in lengths):
        return

    counts = {column: len(values) for column, values in columns.items()}
    raise ValueError(
        f"transcript {tr_name} at {chrom} has mismatched exon "
        "columns: " + ", ".join(
            f"{column} has {count}" for column, count in counts.items()),
    )


def parse_transcript_bounds(
    rec: dict, tr_name: object, chrom: object,
) -> tuple[tuple[int, int], tuple[int, int]]:
    """Read the transcript and coding bounds of a columnar record.

    The five UCSC-derived layouts spell these four columns the same way
    and share the half-open convention that shifts each start by one, so
    they share this rather than repeating it between them (gain#941).
    """
    tx = (
        parse_coordinate(rec["txStart"], "txStart", tr_name, chrom) + 1,
        parse_coordinate(rec["txEnd"], "txEnd", tr_name, chrom))
    cds = (
        parse_coordinate(rec["cdsStart"], "cdsStart", tr_name, chrom) + 1,
        parse_coordinate(rec["cdsEnd"], "cdsEnd", tr_name, chrom))
    return tx, cds
