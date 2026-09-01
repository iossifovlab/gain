"""How a record's cell becomes a value: the two decisions taken at open.

The seam between a score and its table's payload.  Both decisions are taken
once per open, from the table's TYPE and the score definitions, and neither
needs a ``GenomicScore``:

- :func:`select_value_extractor` picks the per-record read, *before*
  ``table.open()``;
- :func:`resolve_score_indices` addresses each definition to a payload
  column, *after* it.

That order is load-bearing -- each function's docstring says what forces its
half -- and it belongs to the caller, :meth:`~.base.GenomicScore.open`.

The extractors themselves are not here: they live with the backends that
know what a payload IS -- :mod:`~gain.genomic_resources.vcf_scores`,
:mod:`~gain.genomic_resources.bigwig_scores`, and
:mod:`~gain.genomic_resources.score_def` for the column read, which also
owns the :data:`ValueExtractor` alias.  This module only chooses between
them, which is why it sits ABOVE all three rather than inside one of them:
hosting the choice in ``score_def``, where gain#1044 put the scoredef
lifecycle, would have it import ``vcf_scores`` and ``bigwig_scores``, both
of which import ``score_def`` -- the same cycle that kept
``GenomicScore._build_scoredefs`` on the class.  ``resolve_score_indices``
alone could have gone there; it is here so that the seam reads as one
module rather than two homes.

:func:`resolve_score_indices` **mutates the definitions in place** and
returns nothing; its docstring says who reads what it wrote.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from gain.genomic_resources.bigwig_scores import (
    BIGWIG_VALUE_COLUMN,
    extract_bigwig_value,
    extract_bigwig_value_na,
)
from gain.genomic_resources.score_def import (
    GenomicScoreDef,
    ValueExtractor,
    extract_column_value,
)
from gain.genomic_resources.vcf_scores import extract_vcf_value

if TYPE_CHECKING:
    # Annotation-only, and NOT a layering fence: ``bigwig_scores`` above
    # pulls the table package in at runtime anyway.  Same statement
    # ``score_def`` makes about the same symbol -- nothing here constructs a
    # table or calls into one, so nothing at runtime needs the name.
    from gain.genomic_resources.genomic_position_table.table import (
        GenomicPositionTable,
    )


def select_value_extractor(
    *,
    score_definitions: dict[str, GenomicScoreDef],
    table: GenomicPositionTable,
    is_vcf: bool,
    is_bigwig: bool,
) -> ValueExtractor:
    """Pick the per-record value read for this table's payload.

    ONE decision, per table, taken at open rather than per line.  What it
    turns on is what a record's PAYLOAD *is*, which is whatever the backend
    that built it says it is:

    * a **VCF** record's payload carries the variant and the pysam INFO
      proxies, and a VCF score is an INFO field addressed by name --
      :func:`extract_vcf_value`;
    * a **bigWig** record's payload IS the interval's value, so the read is
      an identity (:func:`extract_bigwig_value`) -- or, for the rare
      resource that configures NA sentinels, an identity plus one
      membership test (:func:`extract_bigwig_value_na`).  Which of the two
      is settled here, from the score definitions, and never per record:
      the sentinel set is fixed for the life of the open score.  A bigWig
      declares exactly one score (``validate_bigwig_scoredefs`` refuses
      more), so there is a single answer to give; ``any`` states that
      without depending on it;
    * any other record-yielding table's payload is a raw row, read by
      integer column -- :func:`extract_column_value`.

    The table's ``yields_records`` claim is simply believed: that every
    backend's claim matches what it really yields is pinned statically,
    over all four of them, by test_backend_record_contract.py, so the fetch
    path pays nothing for it.

    A table that yields no records is a programming error, not a data
    error: there is no fallback reader, so a backend leaving the flag
    False has nothing that can read it and we refuse rather than guess.
    (Nothing in the tree reaches it: it guards a backend added later
    without its migration.)
    """
    if is_vcf:
        return extract_vcf_value
    if is_bigwig:
        # A bigWig value is a float, so only a NUMERIC sentinel can
        # ever match it -- the four text tokens a float score defaults
        # to ("", "nan", ".", "NA") cannot.  Testing for those rather
        # than for a non-empty set is what lets the definition keep its
        # default (and so its statistics hash) while every unconfigured
        # bigWig still takes the identity read.
        if any(
            not isinstance(sentinel, str)
            for score_def in score_definitions.values()
            for sentinel in score_def.na_values
        ):
            return extract_bigwig_value_na
        return extract_bigwig_value
    if table.yields_records:
        return extract_column_value
    raise TypeError(
        f"{type(table).__name__} does not yield records, so "
        f"there is no score line that can read it. A genomic "
        f"position table backend must set yields_records = True "
        f"and yield six-slot record tuples: see the record "
        f"contract in gain.genomic_resources."
        f"genomic_position_table.record, and "
        f"test_backend_record_contract.py for what that backend "
        f"is held to.")


def resolve_score_indices(
    score_definitions: dict[str, GenomicScoreDef],
    *,
    is_vcf: bool,
    is_bigwig: bool,
    table: GenomicPositionTable,
    resource_id: str,
) -> None:
    """Resolve each score's configured address to a payload column.

    Runs after ``table.open()``, because the by-NAME case is the one thing
    here that has to consult the table's header.

    Writes ``score_index`` onto the definitions it is handed, and returns
    nothing: "the defs are finished in place at open" is the contract
    :mod:`.base` states.  Two paths read what it wrote --
    :func:`~gain.genomic_resources.score_def.extract_column_value`, on every
    record of the tabular per-record read, and so the very extractor this
    module's other half binds; and ``fetch_region_value_arrays``, which the
    statistics scan reaches it through.  Handing back a mapping would only
    mean every caller writes it back.

    These raise rather than assert: an assert reported a misconfigured
    resource with a message-less AssertionError naming neither the resource
    nor the score, and ``python -O`` strips it altogether, leaving the
    by-name branch to call ``header.index(None)`` on a table whose header
    may itself be ``None``.  A resource config is data, and bad data is
    reported, not asserted away.
    """
    if is_vcf:
        # A VCF score has no column to resolve: it is addressed by INFO
        # KEY, which is ``col_name``, and :func:`extract_vcf_value` reads
        # that attribute directly.  All this enforces is that the key is
        # actually there.
        for score_def in score_definitions.values():
            if score_def.col_name is None:
                raise ValueError(
                    f"score {score_def.score_id!r} of VCF resource "
                    f"{resource_id!r} has no INFO key; a VCF score "
                    f"is addressed by name")
        return

    if is_bigwig:
        # A bigWig has exactly one column -- the payload, which IS the
        # value -- so there is nothing to resolve: the answer is 0, and it
        # is the same 0 for the canonical config (which addresses no column
        # at all) and for the deprecated ``index: 3`` that
        # ``validate_bigwig_scoredefs`` has already warned about.  Only
        # ``fetch_region_value_arrays`` reads it; the per-record path
        # indexes nothing.
        for score_def in score_definitions.values():
            score_def.score_index = BIGWIG_VALUE_COLUMN
        return

    # Index first, because it needs nothing from the table.
    for score_def in score_definitions.values():
        if score_def.col_index is not None:
            if score_def.col_name is not None:
                raise ValueError(
                    f"score {score_def.score_id!r} of resource "
                    f"{resource_id!r} configures both a column "
                    f"name ({score_def.col_name!r}) and a column "
                    f"index ({score_def.col_index}); they are "
                    f"mutually exclusive")
            score_def.score_index = score_def.col_index
        elif score_def.col_name is not None:
            if table.header is None:
                raise ValueError(
                    f"score {score_def.score_id!r} of resource "
                    f"{resource_id!r} is addressed by column "
                    f"name ({score_def.col_name!r}), but its table "
                    f"has no header to resolve that name against; "
                    f"address it by column_index instead")
            score_def.score_index = table.header.index(
                score_def.col_name)
        else:
            raise ValueError(
                f"score {score_def.score_id!r} of resource "
                f"{resource_id!r} configures neither "
                f"column_name nor column_index; one is required")
