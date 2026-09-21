"""The value-extraction seam, tested at the module that now owns it.

``value_extraction`` holds the two decisions ``GenomicScore.open`` takes about
how a record's cell becomes a value: which extractor reads the payload, and
which payload column each score def is addressed to.  Both used to be private
methods on the class (gain#1114 moved them out; gain#1027 is the epic).

What is pinned HERE is only what no resource in the tree can show: the
refusal of a backend that has not declared its payload kind, and that the
kind is the ONLY declaration the routing reads -- a stand-in carrying
nothing else is routed to the column read on the strength of
``payload_kind`` alone.  Everything the seam does for a resource somebody
can actually build is pinned from the score's side, at the higher
``open()`` seam, and is deliberately not duplicated here:

- which extractor each backend is routed to, and the bigWig NA-sentinel
  choice between the two identity reads -- test_record_value_extraction.py
  and test_bigwig_scores.py;
- the four refusals of a definition addressed to no usable column --
  test_score_def_parsing.py.  Three of them are its ``resolution_guard``
  tests, which address a def in code because the schema and the builders
  refuse those shapes in a config; the fourth, a score addressing nothing at
  all, a config does still express, and
  ``test_a_score_addressing_nothing_is_refused_by_name`` reaches it that way.
"""
from __future__ import annotations

import pytest
from gain.genomic_resources.genomic_position_table.table import PayloadKind
from gain.genomic_resources.genomic_scores.value_extraction import (
    select_value_extractor,
)
from gain.genomic_resources.score_def import extract_column_value


class _RowTable:
    """A backend whose only claim is the kind declaration.

    It carries nothing else a routing could read, so that routing it
    proves the kind is the one declaration consulted.  Every ROW backend in
    the tree yields records, and that its records are what it claims is
    pinned statically, over all of them, by test_backend_record_contract.py.
    """

    payload_kind = PayloadKind.ROW


class _UndeclaredTable:
    """A backend that has not said what its payload holds.

    Declared-not-defaulted on the base, so nothing in the tree lacks it;
    the stand-in reaches the refusal a fifth backend would hit -- the kind
    is read first, since every later decision depends on it.
    """


def test_a_table_that_has_not_declared_its_payload_kind_is_refused() -> None:
    with pytest.raises(AttributeError, match="payload_kind"):
        select_value_extractor(
            score_definitions={},
            table=_UndeclaredTable(),  # type: ignore[arg-type]
        )


def test_a_row_table_is_routed_on_its_kind_alone() -> None:
    extractor = select_value_extractor(
        score_definitions={},
        table=_RowTable(),  # type: ignore[arg-type]
    )

    assert extractor is extract_column_value
