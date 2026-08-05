"""Reading a VCF's INFO fields as genomic scores.

Everything the score layer knows about VCF, in one module: how a VCF header's
INFO metadata becomes score definitions, and how one of those scores is read
off a record.  Both encode the same thing -- INFO field semantics, and
``Number=1``/``A``/``R``/``.`` in particular -- and they used to sit 362 lines
apart in ``genomic_scores``.

**The VCF table itself is not here and does not belong here.**
``genomic_position_table.table_vcf`` produces records: it owns the payload's
shape, the pysam proxies it carries and the constants that name them.  This
module interprets those records as scores.  That is the same seam the record
contract draws everywhere else -- a backend yields records, the score layer
says what they mean -- and it is why the table layer still imports nothing
from the score layer.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from gain import logging
from gain.genomic_resources.genomic_position_table.record import (
    CHROM,
    PAYLOAD,
    POS_BEGIN,
    Record,
)
from gain.genomic_resources.genomic_position_table.table_vcf import (
    ALLELE_INDEX,
    INFO,
    INFO_META,
    VARIANT,
)
from gain.genomic_resources.score_def import GenomicScoreDef, ScoreValue

logger = logging.getLogger(__name__)

VCF_TYPE_CONVERSION_MAP = {
    "Integer": "int",
    "Float": "float",
    "String": "str",
    "Flag": "bool",
}


def _check_allele_arity(
    record: Record, score_def: GenomicScoreDef, number: str, count: int,
) -> None:
    """Warn if a per-allele INFO field's value count is not its allele count.

    ``Number=A`` declares one value per ALT allele and ``Number=R`` one per
    allele including the reference, so on a well-formed record the count is
    fixed by the ALT column.  Neither shape of mismatch is rejected anywhere
    -- not by pysam, not at resource load -- and both are silent to a reader:
    a SHORT tuple leaves the alleles past its end with no value (they read
    null, see :func:`extract_vcf_value`), an OVER-LONG one has values no
    allele will ever select.  Only the resource's author can fix either, and
    they cannot fix what nothing reports.

    **Once per field per table, not once per line.**  The flag is
    ``score_def.number_mismatch_warned``, and the definition is per-table
    (see its comment in ``score_def``).  This is the per-record score read --
    keeping it cheap is what #237 was about -- and a field whose arity is
    wrong on one row is normally wrong on every row, so per-line logging
    would bury the run in identical lines.  Testing the flag FIRST is what
    bounds the cost on a malformed table: once it has warned, every later
    record of it stops at a single attribute read.

    A WELL-FORMED table has no such short circuit and pays the ALT lookup on
    every record -- there is no cheaper way to learn how many values a row
    ought to carry, and remembering the first row's count would only be
    right for a table whose alleles never vary.  The cost lands where it can
    be afforded: only inside the ``Number=A``/``Number=R`` branch, which no
    ``Number=1`` field ever enters (it decodes to a scalar) and which already
    builds a fresh pysam ``VariantMetadata`` per read -- so it is a fraction
    added to an already-expensive branch, on a shape no production resource
    in the GRRs uses today.

    The message names the field, its number, the counts and the row that
    tripped it.  It cannot name the resource: the read is a pure function of
    ``(record, score_def)`` and neither carries a resource id.  The locus is
    the better half of that trade anyway -- it names the offending row of the
    offending file, which is what an author has to open.
    """
    if score_def.number_mismatch_warned:
        return
    alts = record[PAYLOAD][VARIANT].alts
    # ``alts`` is None for a record whose ALT is absent ('.'): zero ALT
    # alleles, so a Number=A field carries no values at all and a Number=R
    # field carries only the reference's.
    expected = len(alts) if alts is not None else 0
    if number == "R":
        expected += 1
    if count == expected:
        return
    score_def.number_mismatch_warned = True
    logger.warning(
        "INFO field %s (Number=%s) of %s:%s carries %s value(s) for %s "
        "allele(s); the VCF row is malformed and an allele with no value of "
        "its own reads as null. Reported once per table.",
        score_def.score_id, number, record[CHROM], record[POS_BEGIN],
        count, expected)


def _drop_empty_elements(
    record: Record, score_def: GenomicScoreDef, value: tuple,
) -> tuple:
    """Return ``value`` without its empty elements, reporting the first row.

    An element of a multi-valued INFO field is EMPTY when the row declares it
    and supplies nothing -- ``ORIGIN=1,``, ``ORIGIN=a,,b``, ``ORIGIN=,b`` --
    and pysam decodes such an element as ``None``.  That is malformed under
    the VCF spec, and nothing rejects it: not pysam, not resource load.  The
    read answers it by the rule #256 and #289 settled for the per-allele
    shapes -- no value, no score -- so the element contributes nothing to the
    joined value.  (A tuple left with no elements at all is turned into a
    null by the caller, the only place that knows what the join would
    otherwise have produced.)

    ``None`` is the whole test, deliberately: this is NOT a falsy filter.  A
    ``String`` field's ``.`` decodes as the literal string ``'.'`` and is a
    value dbSNP's ``CAF``/``TOPMED`` carry meaningfully, ``'0'`` is a value,
    and ``''`` is a value; only an element pysam decoded as absent is one.
    (In a numeric field ``.`` IS the spec's missing value and pysam decodes it
    to ``None`` too -- the same nothing, dropped for the same reason.)

    **Reported once per field per table**, through
    ``score_def.empty_element_warned`` -- its own flag, not the arity check's,
    so the two cannot silence each other.  Why once, and why the message
    names the field and the locus, is #289's reasoning unchanged: see
    :func:`_check_allele_arity`.

    The membership test comes FIRST, before the flag -- the reverse of that
    check's order.  It is the only work a WELL-FORMED table pays here (one
    C-level scan of a tuple that has to be walked to be joined anyway) and it
    returns the tuple itself, so the common shape allocates nothing; testing
    the flag first would put an attribute read in front of every well-formed
    record to save one on the malformed ones.
    """
    if None not in value:
        return value
    if not score_def.empty_element_warned:
        score_def.empty_element_warned = True
        logger.warning(
            "INFO field %s of %s:%s carries an empty element; the VCF row is "
            "malformed and an element with no value of its own contributes "
            "nothing to the value read. Reported once per table.",
            score_def.score_id, record[CHROM], record[POS_BEGIN])
    return tuple(element for element in value if element is not None)


def extract_vcf_value(
    record: Record, score_def: GenomicScoreDef,
) -> ScoreValue:
    """Read one score off a VCF record: an INFO field, not a column.

    VCF is the awkward backend and this function is where the whole of its
    awkwardness lives.  A VCF score is addressed by INFO **name** -- which is
    ``col_name``, the string the config gave -- looked up on the variant,
    typed by the header metadata, and for a per-allele field selected by the
    record's allele index.

    The four cases:

    * **Number=A** -- one value per ALT allele: select this record's allele.
      A record whose ALT is absent ('.') has no allele index and so no
      applicable value -- under the VCF spec such a record has *zero* ALT
      alleles, so a Number=A field on it carries zero values and a row that
      supplies one anyway is malformed.  It yields ``None``, a null score,
      however many values the field carries and whatever the score def's
      declared type (#256).  Returning the null HERE also keeps the raw tuple
      from escaping as a score value.  The check is a crash guard too: without
      it the tuple is indexed with ``None`` and the read dies with
      ``TypeError``.
    * **Number=R** -- one value per allele *including the reference*, which
      occupies offset 0: an ALT allele reads at ``allele_index + 1``, and a
      record with no ALT reads the **reference** value at offset 0.
    * **Number=. and Type=String** -- an unbounded string field, joined on
      '|' into a single value (a VCF-local convention).
    * anything else -- handed to ``parse_value``, which joins it on '|'
      through the ``converter`` ``parse_vcf_scoredefs`` installed.

    **Every shape that is not per-allele has its EMPTY elements dropped
    first** (#630), by :func:`_drop_empty_elements`.  ``ORIGIN=1,`` declares a
    value it does not carry; pysam decodes that element as ``None``, and
    joining it was the one operation whose outcome depended on nothing but
    the field's declared ``Type`` -- ``"|".join`` raised ``TypeError`` here
    and took the whole fetch, while the ``converter``'s ``map(str, ...)``
    silently annotated the text ``'3|None'`` for every other type.  The drop
    is done HERE, once, for both -- this is the layer that still has the
    record, so it is the only one that can name the row it reports, and it
    leaves the ``converter`` a tuple that cannot contain a ``None``.

    **Neither per-allele selection trusts the tuple to be the right length**
    (#289).  Nothing rejects a row whose value count does not match its ALT
    column -- not pysam, not resource load -- and indexing one bare aborted
    the whole fetch: the second allele of ``ALT=T,G  S=d11`` ran off the end
    with ``IndexError``, and the ``try`` in ``parse_value`` that turns a bad
    cell into a logged null is deliberately not around this call (it guards
    the PARSE, and #256's crash-guard test depends on that placement), so the
    crash escaped ``get_score`` and took the scan with it.  An allele past
    the end of the tuple therefore reads ``None`` -- the same null #256 gives
    the ALT-less record, by the same rule: no applicable per-ALT value, no
    score.  The mismatch itself is reported by :func:`_check_allele_arity`,
    which also catches the mirror shape (more values than alleles, the extras
    unreadable), once per table.

    A key the header **declares** but this record does not carry yields
    ``None`` rather than raising: ``info.get`` returns ``None``, ``None`` is
    not a tuple, so the number cases are skipped and ``parse_value`` turns it
    into a null score.  For a key the header does NOT declare, pysam's
    ``info.get`` raises ``ValueError: Invalid header`` -- but nothing in this
    tree can ask for one, since a VCF table's score defs are built FROM the
    header and a configured score naming an undeclared field is rejected when
    the score is opened (pinned by test_vcf_check_for_missing_score_columns).

    **The metadata lookup stays inside the tuple branch.**  ``INFO_META.get``
    builds a fresh pysam ``VariantMetadata`` for the key, per score, per
    record.  A ``Number=1`` field decodes to a scalar, never reaches that
    branch, and must not pay for a metadata object it will never read; that is
    the common shape of a score-bearing INFO field, and hoisting the lookup
    out of it took a 50-score read of a 3000-row VCF from 26.65 to
    19.83us/line.  (Pinned by
    test_vcf_reads_the_info_metadata_only_for_a_tuple_value.)

    The two pysam proxies this reads -- ``INFO`` and ``INFO_META`` -- are
    resolved once per record by the VCF backend and carried in the payload,
    because pysam allocates a fresh proxy on every ``variant.info`` access.
    See ``table_vcf`` for that measurement and why they live there.
    """
    key = score_def.col_name
    # ``col_name`` is declared ``str | None`` because a column-addressed score
    # leaves it None; ``open()`` refuses to open a VCF score without one.
    assert key is not None

    payload = record[PAYLOAD]
    value = payload[INFO].get(key)
    if isinstance(value, tuple):
        allele_index = payload[ALLELE_INDEX]
        meta = payload[INFO_META].get(key)
        number = meta.number
        if number == "A":
            if allele_index is None:
                return None
            _check_allele_arity(record, score_def, number, len(value))
            if allele_index >= len(value):
                return None
            value = value[allele_index]
        elif number == "R":
            _check_allele_arity(record, score_def, number, len(value))
            # Get reference allele value if ALT is '.'
            index = allele_index + 1 if allele_index is not None else 0
            if index >= len(value):
                return None
            value = value[index]
        else:
            # Not per-allele: every element of this tuple contributes to one
            # joined value, so an empty one has to go before either join sees
            # it.  (The per-allele branches above need no drop -- an empty
            # element they select is already the null they give an allele
            # with no value of its own.)
            value = _drop_empty_elements(record, score_def, value)
            if not value:
                # Nothing left to join, and a join of nothing is ``''`` --
                # a present, aggregatable, bin-able value.  A field carrying
                # no value at all reads as the null an absent key does.
                return None
            if number == "." and meta.type == "String":
                value = "|".join(value)
    return score_def.parse_value(value)


# How a score's value is read off a record: chosen once per opened score by
# ``GenomicScore.open``, from the table's type, and called per value.
#
# This replaces the four score-line CLASSES the score layer used to route
# between (#239 had already reduced them to records plus a wrapper; this
# removes the wrapper).  A score line existed to hold two things -- which
# payload slot a score lives in, and, for VCF, the per-record pysam proxies --
# and neither needs an object any more: the first is ``score_index`` on the
# definition, the second is in the payload.  What is left is a value read that
# is a pure function of ``(record, score_def)``, so the per-line allocation
# goes and the routing stays exactly where it was.

def parse_vcf_scoredefs(
    vcf_header_info: dict[str, Any] | None,
    config_scoredefs: dict[str, GenomicScoreDef] | None, *,
    merge: bool = False,
) -> dict[str, GenomicScoreDef]:
    """Build score definitions from a VCF header's INFO metadata.

    Every INFO field the header declares becomes a score, typed through
    ``VCF_TYPE_CONVERSION_MAP`` and described by the header's own description.
    This is why a VCF resource needs no ``scores:`` block to be usable: the
    file documents its own scores.

    ``value_parser`` is set to ``None`` for ``Number`` of 1, ``A`` or ``R``,
    because pysam already decodes those to a scalar (or to a tuple that
    :func:`extract_vcf_value` indexes by allele).  Every other shape keeps
    ``converter``, which joins a tuple on '|' -- the VCF-local convention for
    a field whose arity the header does not fix.

    ``converter`` joins with ``str`` -- a ``Number=.``/``Type=Integer`` field
    therefore reads as text, a separate question -- which is what made it the
    SILENT half of #630: an empty element would render as the four-character
    string ``'None'``.  It is not guarded here.  The tuples that reach it have
    already had their empty elements dropped by :func:`extract_vcf_value`, the
    only route to it and the only layer holding the record a report has to
    name; this parser sees a value, not a row.

    ``config_scoredefs`` is what the resource's own ``scores:`` block declared,
    and overrides the header for the fields it names: value type, description,
    aggregators and NA values all take the config's value when it gives one,
    falling back to the header's.  Column addressing is NOT overridable -- a
    VCF score is its INFO key, so ``col_name``/``col_index`` always come from
    the header side.

    ``merge`` decides what happens to header fields the config does not
    mention: ``False`` (the default) returns only the configured scores, so
    the config acts as a filter; ``True`` keeps the rest as the header
    defined them.  It is the resource's ``merge_vcf_scores`` setting.
    """
    def converter(val: Any) -> Any:
        try:
            if isinstance(val, tuple):
                return "|".join(map(str, val))
        except TypeError:
            pass

        return val

    vcf_scoredefs = {}

    assert vcf_header_info is not None

    for key, value in vcf_header_info.items():
        value_parser: Callable[[str], Any] | None = converter
        if value.number in (1, "A", "R"):
            value_parser = None

        vcf_scoredefs[key] = GenomicScoreDef(
            score_id=key,
            col_name=key,
            col_index=None,
            desc=value.description or "",
            value_type=VCF_TYPE_CONVERSION_MAP[value.type],
            value_parser=value_parser,
            na_values=(),
            aggregator=None,
            small_values_desc=None,
            large_values_desc=None,
            hist_conf=None,
        )
    if config_scoredefs is None:
        return vcf_scoredefs

    # allow overriding of vcf-generated scoredefs
    scoredefs = {}
    for score, config_scoredef in config_scoredefs.items():
        vcf_scoredef = vcf_scoredefs[score]

        value_type = config_scoredef.value_type or vcf_scoredef.value_type

        scoredef = GenomicScoreDef(
            score_id=vcf_scoredef.score_id,
            desc=config_scoredef.desc or vcf_scoredef.desc,
            value_type=value_type,

            aggregator=config_scoredef.aggregator,

            small_values_desc=config_scoredef.small_values_desc,
            large_values_desc=config_scoredef.large_values_desc,
            col_name=vcf_scoredef.col_name,
            col_index=vcf_scoredef.col_index,
            hist_conf=config_scoredef.hist_conf,
            value_parser=config_scoredef.value_parser,
            na_values=config_scoredef.na_values or vcf_scoredef.na_values,
        )
        scoredefs[score] = scoredef

    if merge:
        for score, vcf_scoredef in vcf_scoredefs.items():
            if score in scoredefs:
                continue
            scoredefs[score] = vcf_scoredef

    return scoredefs
