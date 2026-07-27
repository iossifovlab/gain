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

from gain.genomic_resources.genomic_position_table.record import (
    PAYLOAD,
    Record,
)
from gain.genomic_resources.genomic_position_table.table_vcf import (
    ALLELE_INDEX,
    INFO,
    INFO_META,
)
from gain.genomic_resources.score_def import GenomicScoreDef, ScoreValue

VCF_TYPE_CONVERSION_MAP = {
    "Integer": "int",
    "Float": "float",
    "String": "str",
    "Flag": "bool",
}


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
    * anything else -- handed to ``parse_value`` as pysam decoded it.

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
            value = value[allele_index]
        elif number == "R":
            value = value[
                allele_index + 1
                if allele_index is not None
                else 0  # Get reference allele value if ALT is '.'
            ]
        elif number == "." and meta.type == "String":
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
            pos_aggregator=None,
            allele_aggregator=None,
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

            pos_aggregator=config_scoredef.pos_aggregator,
            allele_aggregator=config_scoredef.allele_aggregator,

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
