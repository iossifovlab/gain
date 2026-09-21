"""How a record's cell becomes a value: the two decisions taken at open.

The seam between a score and its table's payload.  Both decisions are taken
once per open, from the table's declared ``payload_kind`` and the score
definitions, and neither needs a ``GenomicScore``:

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

import functools
from typing import TYPE_CHECKING

from gain.genomic_resources.bigwig_scores import (
    BIGWIG_VALUE_COLUMN,
    extract_bigwig_value,
    extract_bigwig_value_na,
)
from gain.genomic_resources.genomic_position_table import PayloadKind
from gain.genomic_resources.resource_errors import score_configuration_error
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
) -> ValueExtractor:
    """Pick the per-record value read for this table's payload.

    ONE decision, per table, taken at open rather than per line.  What it
    turns on is what a record's PAYLOAD *is*, which the backend declares
    in its :class:`~.table.PayloadKind`:

    * a **VARIANT** payload carries the variant and the pysam INFO
      proxies, and its score is an INFO field addressed by name --
      :func:`extract_vcf_value`;
    * a **VALUE** payload IS the interval's value, so the read is
      an identity (:func:`extract_bigwig_value`) -- or, for the rare
      resource that configures NA sentinels, an identity plus one
      membership test (:func:`extract_bigwig_value_na`).  Which of the two
      is settled here, from the score definitions, and never per record:
      the sentinel set is fixed for the life of the open score.  A bigWig
      declares exactly one score (``validate_bigwig_scoredefs`` refuses
      more), so there is a single answer to give; ``any`` states that
      without depending on it;
    * a **ROW** payload is a raw row, read by integer column --
      :func:`extract_column_value`.

    The declaration is the ONE claim read, and it is read first, because
    every later decision depends on it: a backend that has not made one is
    refused with the ``AttributeError`` the undefaulted ClassVar raises,
    before any other check.  That a backend's records are what its kind
    says -- a ROW backend really yields six-slot records with a raw row in
    the payload slot -- is pinned statically, over every backend in the
    tree, by test_backend_record_contract.py, so the fetch path pays
    nothing for it.
    """
    kind = table.payload_kind
    if kind is PayloadKind.VARIANT:
        return extract_vcf_value
    if kind is PayloadKind.VALUE:
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
    return extract_column_value


def resolve_score_indices(
    score_definitions: dict[str, GenomicScoreDef],
    *,
    table: GenomicPositionTable,
    resource_id: str,
) -> None:
    """Resolve each score's configured address to a payload column.

    Runs after ``table.open()``, because the by-NAME case is the one thing
    here that has to consult the table's header.  What a definition's
    address means -- an INFO key, nothing, or a column -- is the table's
    :class:`~.table.PayloadKind`, read off the table.

    Writes ``score_index`` onto the definitions it is handed, and returns
    nothing: "the defs are finished in place at open" is the contract
    :mod:`.base` states.  Two paths read what it wrote --
    :func:`~gain.genomic_resources.score_def.extract_column_value`, on every
    record of the tabular per-record read, and so the very extractor this
    module's other half binds; and ``fetch_region_value_arrays``, which the
    statistics scan reaches it through.  Handing back a mapping would only
    mean every caller writes it back.

    A definition it cannot resolve -- no address, two addresses, a name over
    a table with no header, a name the header does not have, a VCF
    definition with no INFO key -- is refused through
    :func:`~gain.genomic_resources.resource_errors.score_configuration_error`,
    so the refusal names the resource and the score in the shape every
    other definition refusal shares and falls inside
    ``cli_errors.RESOURCE_ERRORS`` by type.  Every check is an explicit
    test, never an ``assert`` (``python -O`` strips those) and never a
    lookup left to raise for itself: a resource config is data, and bad
    data is reported.  For a tabular table, ``validate_scoredefs`` holds
    the CONFIG to the address rules before this runs, but for a headerless
    table it checks only that no name is stated -- so a definition with no
    address at all reaches this from a real config, while the other
    refusals are reached through :meth:`~.base.GenomicScore.open` only by
    a definition edited after its config passed.
    """
    kind = table.payload_kind
    if kind is PayloadKind.VARIANT:
        # A VCF score has no column to resolve: it is addressed by INFO
        # KEY, which is ``col_name``, and :func:`extract_vcf_value` reads
        # that attribute directly.  All this enforces is that the key is
        # actually there.
        for score_def in score_definitions.values():
            if score_def.col_name is None:
                raise score_configuration_error(
                    resource_id, score_def.score_id,
                    "has no INFO key; a VCF score is addressed by name.")
        return

    if kind is PayloadKind.VALUE:
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
        refuse = functools.partial(
            score_configuration_error, resource_id, score_def.score_id)
        if score_def.col_index is not None:
            if score_def.col_name is not None:
                raise refuse(
                    f"configures both a column name "
                    f"({score_def.col_name!r}) and a column index "
                    f"({score_def.col_index}); they are mutually "
                    f"exclusive.")
            score_def.score_index = score_def.col_index
        elif score_def.col_name is not None:
            if table.header is None:
                raise refuse(
                    f"is addressed by column name "
                    f"{score_def.col_name!r}, but its table has no "
                    f"header to resolve that name against; address it "
                    f"by column_index instead.")
            if score_def.col_name not in table.header:
                raise refuse(
                    f"is addressed by column name "
                    f"{score_def.col_name!r}, which the table's header "
                    f"does not have; its columns are: "
                    f"{', '.join(table.header)}.")
            score_def.score_index = table.header.index(
                score_def.col_name)
        else:
            raise refuse(
                "configures neither column_name nor column_index; one "
                "is required.")
