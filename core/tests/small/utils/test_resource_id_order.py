# pylint: disable=W0621,C0114,C0115,C0116,W0613
"""The index page's resource-id order, on the Python side.

The page's ID sorter and tree order ids with ``localeCompare``; the
published rows have to be in the same order, so the key that publishes
them mirrors the root collation on the id alphabet.  The two halves are
pinned against each other in ``info_pages_e2e`` -- these tests pin the
rules the key itself implements, on inputs a fixture would not
naturally carry.
"""
from __future__ import annotations

import locale
import re
import string

import pytest
from gain.genomic_resources.repository import RESOURCE_ID_CHARACTER_CLASS
from gain.utils.resource_id_order import resource_id_collation_key


def _sorted(ids: list[str]) -> list[str]:
    return sorted(ids, key=resource_id_collation_key)


def test_punctuation_sorts_before_digits_before_letters() -> None:
    """The root collation on the id alphabet, in one line.

    Code-point order has ``/`` before digits and ``_`` after every
    uppercase letter; the collation puts all four punctuation marks
    ahead of the digits, in this order, and the digits ahead of the
    letters.
    """
    ids = ["aab", "a0b", "a/b", "a.b", "a-b", "a_b"]

    assert _sorted(ids) == ["a_b", "a-b", "a.b", "a/b", "a0b", "aab"]


def test_case_does_not_rank_and_lowercase_wins_a_tie() -> None:
    """``Zoo`` after ``genomes``; ``alpha`` before ``Alpha``.

    Code-point order puts every capitalised id ahead of every lowercase
    one, which is the disagreement iossifovlab/gain#564 opened with.
    The collation reads letters without regard to case, and only where
    two ids differ in nothing else does the lowercase one go first --
    at the first letter that differs, not by counting capitals.
    """
    ids = ["Zoo/Track", "Aab", "aAb", "genomes", "aab", "Zoo/alpha"]

    assert _sorted(ids) == [
        "aab", "aAb", "Aab", "genomes", "Zoo/alpha", "Zoo/Track",
    ]


def test_the_key_ignores_the_process_locale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rebuild on a differently configured host publishes the same page.

    ``locale.strxfrm`` (or ``strcoll`` behind ``cmp_to_key``) would be
    the obvious way to get a locale-aware key, and both read the process
    locale -- so two hosts would publish two orders.  Fence the key from
    them: it must not even consult either.

    The names are fenced rather than the invariant (``setlocale`` to
    something else, same order) because CI's image carries no second
    collating locale to switch to -- under ``C.UTF-8`` that test passes
    against ``strxfrm`` too.
    """
    def refuse(*_values: str) -> str:
        raise AssertionError("the key consulted the process locale")

    monkeypatch.setattr(locale, "strxfrm", refuse)
    monkeypatch.setattr(locale, "strcoll", refuse)

    assert _sorted(["Zoo", "genomes"]) == ["genomes", "Zoo"]


def test_the_key_ranks_every_character_an_id_can_carry() -> None:
    """The alphabet the key orders is the one the scan admits.

    The key spells its alphabet by hand, because the *order* it puts
    the marks in is the root collation's and not the grammar's.  The
    grammar is the single source of the *membership* (gain#1352), so a
    character it gains and the key does not rank would fall to its code
    point silently; this is what notices.
    """
    admitted = {
        c for c in string.printable
        if re.fullmatch(f"[{RESOURCE_ID_CHARACTER_CLASS}]", c)
    }

    assert admitted == set(string.ascii_letters + string.digits + "_-./")
