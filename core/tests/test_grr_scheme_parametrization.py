# pylint: disable=W0621,C0114,C0116,W0212,W0613
import itertools
from typing import Any, cast

import pytest

from tests.conftest import grr_schemes_for_marks, pytest_generate_tests

REPRESENTATIVE_MARKS = [
    [],
    ["grr_rw"],
    ["grr_full"],
    ["grr_http"],
    ["grr_tabix"],
    ["grr_local"],
    ["grr_rw", "grr_tabix"],
    ["grr_full", "grr_http"],
]


def test_default_schemes_are_an_ordered_sequence() -> None:
    assert grr_schemes_for_marks([]) == ["file", "inmemory"]


def test_grr_full_mark_narrows_to_the_full_rw_schemes() -> None:
    assert grr_schemes_for_marks(["grr_full"]) == ["file"]


def _registered_grr_markers(config: pytest.Config) -> list[str]:
    """Return the ``grr_*`` marker names pytest actually registered.

    ``getini("markers")`` is the registry itself rather than a re-parse of
    ``pytest.ini``, so this cannot drift from what pytest believes. It also
    carries plugin-registered markers, none of which use the ``grr_`` prefix.
    """
    return [
        entry.split(":", 1)[0].strip()
        for entry in config.getini("markers")
        if entry.startswith("grr_")
    ]


def test_every_registered_grr_marker_narrows_the_schemes(
    pytestconfig: pytest.Config,
) -> None:
    """A registered ``grr_*`` marker must select strictly fewer schemes.

    Registering a marker that ``grr_schemes_for_marks`` does not recognise is
    silently harmful rather than an error: the unrecognised name survives none
    of the ``if`` blocks, is dropped by the intersection with
    ``ALL_GRR_SCHEMES``, and leaves ``marked_schemes`` empty -- so the
    narrowing is skipped and the test broadens to every *enabled* scheme. A
    read-only marker then selects the paid ``s3`` read-write arm, which is how
    ``grr_ro`` sat dead in the registry (gain#946).

    Asserted on behaviour rather than against a table of known names, because
    ``grr_http`` is not recognised by an ``if`` block at all -- it works only
    because the stripped name is itself a member of ``ALL_GRR_SCHEMES``. A
    membership check would flag it as unknown; narrowing is the property that
    actually matters.

    Both gates are forced on, and that is load-bearing rather than incidental
    thoroughness: with them off the baseline is just the free schemes, which
    ``grr_rw`` also selects in full, so a strict-subset check would fail it.
    Only against the widest baseline does "narrows" mean what it says.
    """
    markers = _registered_grr_markers(pytestconfig)
    assert markers, (
        "no grr_* marker is registered -- pytest.ini was not loaded, so this "
        "test would pass without checking anything"
    )

    baseline = set(grr_schemes_for_marks(
        [], enable_s3=True, enable_http=True))

    narrows_nothing = []
    for name in markers:
        selected = set(grr_schemes_for_marks(
            [name], enable_s3=True, enable_http=True))
        if not selected < baseline:
            narrows_nothing.append(f"{name} -> {sorted(selected)}")

    assert not narrows_nothing, (
        "registered but selecting no fewer schemes than an unmarked test "
        f"({sorted(baseline)}): {narrows_nothing}. Either give the mark a "
        "branch in grr_schemes_for_marks, or do not spell it grr_* if it "
        "does not select schemes -- every grr_* mark on a test is fed to "
        "the selector."
    )


@pytest.mark.parametrize(
    ("mark_names", "enable_s3", "enable_http", "expected"),
    [
        ([], False, False, ["file", "inmemory"]),
        ([], True, False, ["file", "inmemory", "s3"]),
        ([], False, True, ["file", "http", "inmemory"]),
        ([], True, True, ["file", "http", "inmemory", "s3"]),
        # An unmarked test broadens to every *enabled* scheme, so deleting a
        # mark cannot de-tier a test; grr_local is what narrows one, under
        # every gate combination. See docs/adr/0021-*.md and gain#875.
        (["grr_local"], False, False, ["file", "inmemory"]),
        (["grr_local"], True, False, ["file", "inmemory"]),
        (["grr_local"], False, True, ["file", "inmemory"]),
        (["grr_local"], True, True, ["file", "inmemory"]),
        # It contributes schemes like the rest of the family, so combining it
        # with a paid mark widens rather than narrows. Nothing does that today.
        (["grr_local", "grr_tabix"], True, True, ["file", "http", "inmemory",
                                                  "s3"]),
        (["grr_rw"], False, False, ["file", "inmemory"]),
        (["grr_rw"], True, True, ["file", "inmemory", "s3"]),
        (["grr_full"], True, False, ["file", "s3"]),
        (["grr_tabix"], True, True, ["file", "http", "s3"]),
        (["grr_http"], False, True, ["http"]),
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
