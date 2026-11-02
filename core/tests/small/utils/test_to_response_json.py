# pylint: disable=C0116
"""Pin the behavior of ``to_response_json``, which gpf_web calls."""
from box import Box
from gain.utils.helpers import to_response_json


def test_to_response_json_camelizes_top_level_keys() -> None:
    result = to_response_json({"study_id": 1, "name": "x"})

    assert result == {"studyId": 1, "name": "x"}


def test_to_response_json_converts_nested_dicts() -> None:
    result = to_response_json({"outer_key": {"inner_key": 2}})

    assert result == {"outerKey": {"innerKey": 2}}


def test_to_response_json_camelizes_keys_inside_a_box_value() -> None:
    result = to_response_json({"box_value": Box({"inner_key": 3})})

    assert result == {"boxValue": {"innerKey": 3}}


def test_to_response_json_accepts_a_box_as_input() -> None:
    result = to_response_json(Box({"study_id": 4}))

    assert result == {"studyId": 4}


def test_to_response_json_converts_dicts_inside_a_list() -> None:
    result = to_response_json({"list_value": [{"item_key": 5}, 6, "seven"]})

    assert result == {"listValue": [{"itemKey": 5}, 6, "seven"]}


def test_to_response_json_turns_a_tuple_into_a_list() -> None:
    result = to_response_json({"tuple_value": ({"item_key": 8}, 9)})

    assert result == {"tupleValue": [{"itemKey": 8}, 9]}


def test_to_response_json_does_not_convert_dicts_in_a_nested_list() -> None:
    result = to_response_json({"nested_list": [[{"item_key": 10}]]})

    assert result == {"nestedList": [[{"item_key": 10}]]}
