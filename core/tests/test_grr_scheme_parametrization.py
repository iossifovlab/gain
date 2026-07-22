# pylint: disable=W0621,C0114,C0116,W0212,W0613
import itertools
from typing import Any, cast

import pytest

from tests.conftest import grr_schemes_for_marks, pytest_generate_tests

REPRESENTATIVE_MARKS = [
    [],
    ["grr_rw"],
    ["grr_ro"],
    ["grr_full"],
    ["grr_http"],
    ["grr_tabix"],
    ["grr_rw", "grr_tabix"],
    ["grr_full", "grr_http"],
]


def test_default_schemes_are_an_ordered_sequence() -> None:
    assert grr_schemes_for_marks([]) == ["file", "inmemory"]


def test_grr_full_mark_narrows_to_the_full_rw_schemes() -> None:
    assert grr_schemes_for_marks(["grr_full"]) == ["file"]


@pytest.mark.parametrize(
    ("mark_names", "enable_s3", "enable_http", "expected"),
    [
        ([], False, False, ["file", "inmemory"]),
        ([], True, False, ["file", "inmemory", "s3"]),
        ([], False, True, ["file", "http", "inmemory"]),
        ([], True, True, ["file", "http", "inmemory", "s3"]),
        (["grr_rw"], False, False, ["file", "inmemory"]),
        (["grr_rw"], True, True, ["file", "inmemory", "s3"]),
        (["grr_full"], True, False, ["file", "s3"]),
        (["grr_tabix"], True, True, ["file", "http", "s3"]),
        (["grr_http"], False, True, ["http"]),
        (["grr_ro"], False, False, ["file", "inmemory"]),
        (["grr_rw", "grr_tabix"], True, True, ["file", "http", "inmemory",
                                               "s3"]),
    ],
)
def test_schemes_selected_for_mark_combination(
    mark_names: list[str],
    enable_s3: bool,
    enable_http: bool,
    expected: list[str],
) -> None:
    assert grr_schemes_for_marks(
        mark_names, enable_s3=enable_s3, enable_http=enable_http) == expected


@pytest.mark.parametrize("mark_names", REPRESENTATIVE_MARKS)
@pytest.mark.parametrize(("enable_s3", "enable_http"), [
    (False, False), (True, False), (False, True), (True, True),
])
def test_schemes_are_a_sequence_in_a_defined_order(
    mark_names: list[str],
    enable_s3: bool,
    enable_http: bool,
) -> None:
    """Guard the fix for #349: an unordered container must not come back.

    ``metafunc.parametrize`` preserves the order it is given, and xdist
    compares the ordered collection lists of its workers, so anything whose
    iteration order varies between processes (a ``set``) aborts the run.
    """
    schemes = grr_schemes_for_marks(
        mark_names, enable_s3=enable_s3, enable_http=enable_http)

    assert isinstance(schemes, list)
    assert schemes == sorted(schemes)


@pytest.mark.parametrize("mark_names", REPRESENTATIVE_MARKS)
def test_schemes_do_not_depend_on_the_order_of_the_marks(
    mark_names: list[str],
) -> None:
    expected = grr_schemes_for_marks(mark_names)

    for permutation in itertools.permutations(mark_names):
        assert grr_schemes_for_marks(list(permutation)) == expected


class _StubConfig:
    def __init__(self, *, enable_s3: bool, enable_http: bool) -> None:
        self._options = {"enable_s3": enable_s3, "enable_http": enable_http}

    def getoption(self, name: str) -> bool:
        return self._options[name]


class _StubMetafunc:
    """Minimal stand-in for ``pytest.Metafunc`` recording the parametrization.

    ``pytest_generate_tests`` is a pytest hook; the only observable effect it
    has is the call it makes to ``metafunc.parametrize``.
    """

    def __init__(self, marks: list[str], *, enable_s3: bool = False,
                 enable_http: bool = False) -> None:
        self.fixturenames = ["grr_scheme"]
        self.config = _StubConfig(
            enable_s3=enable_s3, enable_http=enable_http)
        self.function = _StubMetafunc._marked_function(marks)
        self.parametrized: list[tuple[str, Any]] = []

    @staticmethod
    def _marked_function(marks: list[str]) -> Any:
        def a_test() -> None:
            """Stand-in for a marked test function."""

        a_test.pytestmark = [  # type: ignore[attr-defined]
            getattr(pytest.mark, name).mark for name in marks]
        return a_test

    def parametrize(self, argnames: str, argvalues: Any, **_kwargs: Any,
                    ) -> None:
        self.parametrized.append((argnames, argvalues))


def test_hook_parametrizes_grr_scheme_with_an_ordered_sequence() -> None:
    metafunc = _StubMetafunc(["grr_rw"], enable_s3=True, enable_http=True)

    pytest_generate_tests(cast("pytest.Metafunc", metafunc))

    assert metafunc.parametrized == [
        ("grr_scheme", ["file", "inmemory", "s3"]),
    ]
