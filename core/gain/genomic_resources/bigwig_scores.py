"""Reading a bigWig's single value column as a genomic score.

Everything the score layer knows about bigWig, in one module: how a bigWig's
score definitions are finished off, what a bigWig resource is allowed to
configure, and how the one value is read off a record.  Symmetric to
``vcf_scores``, and for the same reason -- these are all statements about one
backend's peculiarities, and they belong together rather than scattered through
``genomic_scores``.

**Reject what corrupts values; warn about what is merely inert.**  That is the
principle that decides which of the two halves below a piece of misconfiguration
falls into, and it is worth stating because bigWig attracts a lot of config that
does nothing.  A bigWig is a *binary* format with a fixed layout: one numeric
value per interval, no columns, no header, no text.  So a resource that declares
a second score, or a score ``type:`` of ``int``, or a column ``index:`` other
than the deprecated 3, is asking for a value the file cannot give -- it would be
truncated, or read from a column that does not exist -- and
:func:`validate_bigwig_scoredefs` refuses to open it, naming the resource and
the score.  A resource that declares ``chrom:``/``pos_begin:``/``pos_end:``
column blocks, or a ``header:``, is merely describing a tabular file that this
one is not; nothing reads those keys, no value changes because of them, and
``genomic_position_table.utils`` warns and ignores them.

**The bigWig table itself is not here and does not belong here.**
``genomic_position_table.table_bigwig`` produces records: it owns the payload's
shape -- which, since this module exists, is the bare value.  This module says
what that value means as a score.  That is the same seam ``vcf_scores`` draws,
and it is why the table layer still imports nothing from the score layer.
"""
from __future__ import annotations

import dataclasses
from typing import Any

from gain import logging
from gain.genomic_resources.genomic_position_table.record import (
    PAYLOAD,
    Record,
)
from gain.genomic_resources.genomic_position_table.table_bigwig import (
    VALUE_COLUMN,
)
from gain.genomic_resources.score_def import GenomicScoreDef, ScoreValue

logger = logging.getLogger(__name__)

# The one column index a bigWig score config may still name.  It addressed the
# value inside the four-element payload a bigWig record used to carry; the
# payload is now the value itself, so the key means nothing and is accepted
# only as a no-op, with a deprecation warning per open.  See
# :func:`validate_bigwig_scoredefs`.
DEPRECATED_VALUE_INDEX = 3

# Where a bigWig score's value lives for the bulk column-array read, which is
# the one place a bigWig score still has a "column" at all.  Re-exported from
# the table layer, which owns the payload's shape, so that the score layer's
# ``score_index`` and the backend's served column cannot drift.
BIGWIG_VALUE_COLUMN = VALUE_COLUMN


def extract_bigwig_value(
    record: Record, score_def: GenomicScoreDef,  # noqa: ARG001
) -> ScoreValue:
    """Read the one score off a bigWig record: the payload IS the value.

    A true identity function, and that is the whole point.  A bigWig stores a
    ``float`` per interval and ``pyBigWig`` hands it up as a Python ``float``;
    a bigWig score is declared ``type: float`` (anything else is refused at
    open); and the NA default for such a score is empty (see
    :func:`build_bigwig_scoredefs`).  So every step
    :meth:`GenomicScoreDef.parse_value` would perform on this value -- the NA
    membership test, the ``float(value)`` reparse -- is provably a no-op, and
    this reads the value out of the record and returns it.

    ``score_def`` is unused and stays in the signature because
    :data:`~gain.genomic_resources.score_def.ValueExtractor` is what
    ``GenomicScore.open`` routes to; a bigWig-shaped extractor with a different
    arity would need a call-site branch per record to invoke, which is the cost
    this removes.

    The variant that *does* consult the definition is
    :func:`extract_bigwig_value_na`, and ``open()`` picks between the two once,
    from whether the score configures any NA sentinels at all.
    """
    # A record's PAYLOAD is statically ``Any`` -- it is the backend's, and each
    # backend's is a different shape.  Naming its type here is the one place
    # this module states what a bigWig's is.
    value: ScoreValue = record[PAYLOAD]
    return value


def extract_bigwig_value_na(
    record: Record, score_def: GenomicScoreDef,
) -> ScoreValue:
    """:func:`extract_bigwig_value`, plus the configured NA-sentinel check.

    Bound instead of the identity for a score whose ``na_values`` is non-empty
    -- which, since the default is empty for bigWig, means a resource that
    explicitly configured sentinels (``na_values: "-1"`` is the deployed
    shape).  The sentinel set carries both the text and the parsed form of each
    sentinel (see ``normalize_na_values``), so a numeric payload matches by
    value.

    Still no parse: a sentinel match yields the null score, and anything else
    is the payload unchanged.  The choice between this and the identity is made
    once per open, never per record.
    """
    value: ScoreValue = record[PAYLOAD]
    if value in score_def.na_values:
        return None
    return value


def validate_bigwig_scoredefs(
    resource_id: str,
    score_defs: dict[str, GenomicScoreDef],
) -> None:
    """Refuse a bigWig score config that cannot mean what it says.

    Called from ``GenomicScore.open`` **before** the table is opened, in the
    same slot as the extractor routing: a refusal that costs no file handle
    cannot leak one, and both of this function's inputs are known at
    construction, so nothing here needs the handle.

    Deliberately NOT called from ``__init__``.
    ``GenomicScoreImplementation.__init__`` builds its score eagerly, so a
    constructor that refused would make a misconfigured bigWig resource
    impossible to *list or describe* -- and listing and describing it is
    precisely what ``grr_manage`` has to do in order to report it.  Refusing
    at open leaves the resource inspectable and stops only the read.

    What is refused, and why each one corrupts values rather than merely
    sitting there:

    * **more than one score** -- a bigWig file carries one value per interval.
      A second score has no second value to read, so both scores would read
      the same number under different names.
    * **a ``type:`` other than ``float``** -- ``pyBigWig`` hands up a Python
      ``float``, and the value read is an identity (see
      :func:`extract_bigwig_value`).  ``int`` would silently truncate every
      value; ``str``/``bool`` would let a float through wearing the wrong
      declared type, and the declared type is what the aggregators, the
      histograms and the bulk read path all branch on.
    * **a column address other than the deprecated 3** -- there are no columns
      to address.  Under the old four-element payload, ``0``/``1``/``2`` read
      the contig and the two coordinates and ``>= 4`` was out of range; either
      way a resource that names one is asking for something that is not the
      score.
    * **a column NAME** -- a bigWig has no header, so there is nothing to
      resolve the name against.

    ``index: 3`` is the one exception, accepted with a deprecation warning:
    see :data:`DEPRECATED_VALUE_INDEX`.

    A score that declares no ``type:`` at all is let through.  That is not a
    declaration of a non-float type -- it is the same absent-type config every
    backend accepts, and it corrupts nothing: the value still arrives as the
    float the file holds.
    """
    if len(score_defs) > 1:
        raise ValueError(
            f"bigWig resource {resource_id!r} configures "
            f"{len(score_defs)} scores ({sorted(score_defs)}); a bigWig "
            f"carries one value per interval, so it has exactly one score "
            f"to give")

    for score_id, score_def in score_defs.items():
        where = f"score {score_id!r} of bigWig resource {resource_id!r}"
        if score_def.value_type not in (None, "float"):
            raise ValueError(
                f"{where} is declared type {score_def.value_type!r}; a "
                f"bigWig value is a float and reading it is an identity, so "
                f"'float' is the only type it can have")
        if score_def.col_name is not None:
            raise ValueError(
                f"{where} is addressed by column name "
                f"({score_def.col_name!r}), but a bigWig table has no header "
                f"to resolve that name against -- and no columns to name. "
                f"Remove the column addressing")
        if score_def.col_index is None:
            continue
        if score_def.col_index != DEPRECATED_VALUE_INDEX:
            raise ValueError(
                f"{where} is addressed at column index "
                f"{score_def.col_index}; a bigWig record's payload is its "
                f"value, so there is no column {score_def.col_index} to "
                f"read. Remove the column addressing")
        logger.warning(
            "%s: 'index: %s' is deprecated and does nothing -- a bigWig "
            "record's payload is its value, not a %s-column row. Delete the "
            "key from the resource config (score %r)",
            resource_id, DEPRECATED_VALUE_INDEX,
            DEPRECATED_VALUE_INDEX + 1, score_id)


def build_bigwig_scoredefs(
    config: dict[str, Any],
    config_scoredefs: dict[str, GenomicScoreDef],
) -> dict[str, GenomicScoreDef]:
    """Finish a bigWig resource's score definitions: empty the NA default.

    The definitions arrive already parsed from the ``scores:`` block, exactly
    as for any other backend.  The one thing that is bigWig-specific is the
    **default** ``na_values``: for a ``float`` score that default is
    ``("", "nan", ".", "NA")`` -- four *text* sentinels, which exist because a
    tabular backend hands the score layer strings.  A bigWig hands it a
    ``float``, which can never equal any of them, so the set is dead config on
    this backend; it costs a set membership test per value, and -- because
    ``GenomicScoreImplementation.calc_statistics_hash`` folds ``na_values`` in
    -- it is four tokens of noise in every bigWig resource's statistics hash.

    Emptying it is also what makes :func:`extract_bigwig_value`, rather than
    :func:`extract_bigwig_value_na`, the path every deployed bigWig resource
    takes.

    **Only where the raw config omits the key.**  A resource that states
    ``na_values: "-1"`` means it, and keeps it -- the sentinel is a real value
    the file carries and the NA extractor still tests for it.  So the decision
    is read off the raw ``scores:`` entries rather than off the parsed
    definition, whose ``na_values`` is already the default by the time it gets
    here and no longer says whether the config asked for it.

    ``na_values=()`` is spelled as an empty tuple because
    ``normalize_na_values`` already turns a tuple into a set of its members --
    ``None`` would select the very default this exists to remove.
    """
    configured_na = {
        score_conf["id"]
        for score_conf in config.get("scores", [])
        if "na_values" in score_conf
    }
    return {
        score_id: score_def if score_id in configured_na
        else dataclasses.replace(score_def, na_values=())
        for score_id, score_def in config_scoredefs.items()
    }
