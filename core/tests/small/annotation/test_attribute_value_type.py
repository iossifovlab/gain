# pylint: disable=C0114,C0116
"""``Attribute.get_value_type``: the type an attribute's value comes back as.

The aggregator changes it -- a ``list`` aggregator turns a float score
into a list, ``count`` into an int -- and the attribute answers that from
the aggregator's NAME, which is the only thing it holds (gain#1133).  It
used to answer from an aggregator instance built beside the name; the
instance is gone and nothing that reads a type has to construct an
accumulator to get one.
"""

import pytest
from gain.annotation.annotation_config import Attribute
from gain.annotation.annotation_pipeline import AttributeSpec


def _an_attribute(aggregator: str | None, value_type: str) -> Attribute:
    return Attribute(
        name="score",
        source="score_id",
        aggregator=aggregator,
        spec=AttributeSpec(
            source="score_id", value_type=value_type, description="",
        ),
    )


def test_the_spec_type_answers_when_no_aggregator_is_named() -> None:
    attr = _an_attribute(aggregator=None, value_type="float")

    assert attr.get_value_type() == "float"


@pytest.mark.parametrize("aggregator,expected", [
    ("list", "list"),
    ("mean", "float"),
    ("count", "int"),
    ("join(;)", "str"),
    ("bool", "bool"),
])
def test_a_named_aggregator_answers_with_its_own_output_type(
    aggregator: str, expected: str,
) -> None:
    # The spec says ``object``, which no aggregator here declares, so a
    # passing assertion cannot be the spec type coming back by accident.
    attr = _an_attribute(aggregator=aggregator, value_type="object")

    assert attr.get_value_type() == expected


def test_an_aggregator_that_keeps_the_input_type_answers_the_spec_type() -> \
        None:
    """``mode`` declares no output type of its own: the value stays a str."""
    attr = _an_attribute(aggregator="mode", value_type="str")

    assert attr.get_value_type() == "str"


def test_the_spec_type_answers_when_aggregation_was_skipped() -> None:
    attr = _an_attribute(aggregator="list", value_type="float")

    assert attr.get_value_type(aggregated=False) == "float"
