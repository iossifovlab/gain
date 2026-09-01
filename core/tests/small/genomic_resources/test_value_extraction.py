"""The value-extraction seam, tested at the module that now owns it.

``value_extraction`` holds the two decisions ``GenomicScore.open`` takes about
how a record's cell becomes a value: which extractor reads the payload, and
which payload column each score def is addressed to.  Both used to be private
methods on the class (gain#1114 moved them out; gain#1027 is the epic).

What is pinned HERE is only what no opened score can reach at all: the
refusal of a table that yields no records, which needs a backend that does
not exist in the tree.  Everything the seam does for a resource somebody can
actually build is pinned from the score's side, at the higher ``open()``
seam, and is deliberately not duplicated here:

- which extractor each backend is routed to, and the bigWig NA-sentinel
  choice between the two identity reads -- test_record_value_extraction.py
  and test_bigwig_scores.py;
- the four refusals of a definition addressed to no usable column --
  test_score_def_parsing.py, whose ``resolution_guard`` tests reach them by
  addressing a def in code, since the schema and the builders refuse those
  shapes in a config.
"""
from __future__ import annotations

import pytest
from gain.genomic_resources.genomic_scores.value_extraction import (
    select_value_extractor,
)


class _RecordlessTable:
    """A backend that yields no records -- the case the seam refuses.

    No table in the tree sets this: the flag guards a backend added later
    without the migration that would give it a reader.  A stand-in is the
    only way to reach the branch, and the refusal names its class.
    """

    yields_records = False


def test_a_table_that_yields_no_records_is_refused() -> None:
    with pytest.raises(TypeError) as exc_info:
        select_value_extractor(
            score_definitions={},
            table=_RecordlessTable(),
            is_vcf=False,
            is_bigwig=False,
        )

    assert str(exc_info.value) == (
        "_RecordlessTable does not yield records, so "
        "there is no score line that can read it. A genomic "
        "position table backend must set yields_records = True "
        "and yield six-slot record tuples: see the record "
        "contract in gain.genomic_resources."
        "genomic_position_table.record, and "
        "test_backend_record_contract.py for what that backend "
        "is held to.")
