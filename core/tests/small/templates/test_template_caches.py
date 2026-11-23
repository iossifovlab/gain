"""``reset_caches`` forgets the environment, the provider templates and
the digest of the page templates.

The Jinja environment and the merged provider-template dict are
process-wide singletons, built on first use.  Every test in this package
resets them before and after itself (see ``conftest.py``), and this is
what that reset is asked to do: the next call builds afresh, so a
provider registered after the reset is seen.
"""
from __future__ import annotations

import pathlib
import shutil
from unittest.mock import patch

import gain.templates as templates
import pytest
from gain.templates import (
    get_jinja_env,
    get_template,
    reset_caches,
    templates_digest,
)
from jinja2 import TemplateNotFound

from tests.small.templates.conftest import make_entry_point


def test_the_environment_is_rebuilt_after_a_reset() -> None:
    before = get_jinja_env()

    reset_caches()

    assert get_jinja_env() is not before


def test_a_provider_registered_after_a_reset_is_seen() -> None:
    """The provider dict is re-read, not just the environment rebuilt.

    The dict is filled on the first *lookup* that falls through to the
    providers, not when the environment is built -- so one such lookup,
    with no providers, is what leaves a stale cache for the reset to
    clear.
    """
    with patch("gain.templates.entry_points", return_value=[]), \
            pytest.raises(TemplateNotFound):
        get_template("late.jinja")
    late = make_entry_point("late", lambda: {"late.jinja": "late {{ v }}"})

    reset_caches()

    with patch("gain.templates.entry_points", return_value=[late]):
        assert get_template("late.jinja").render(v="one") == "late one"


@pytest.fixture
def template_files(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
) -> pathlib.Path:
    """A copy of the page templates that the digest reads in their place."""
    copy = tmp_path / "template_files"
    shutil.copytree(
        str(templates._PAGE_INPUT_DIRS["template_files"]), copy)
    monkeypatch.setitem(templates._PAGE_INPUT_DIRS, "template_files", copy)
    return copy


def test_the_digest_reads_the_templates_once_until_a_reset(
    template_files: pathlib.Path,
) -> None:
    first = templates_digest()
    page = template_files / "basic.jinja"
    page.write_text(page.read_text() + "<!-- changed -->\n")

    second = templates_digest()
    reset_caches()
    third = templates_digest()

    assert second == first
    assert third != first


def test_the_digest_does_not_depend_on_the_listing_order(
    template_files: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    one = make_entry_point("one", lambda: {"one.jinja": "one"})
    two = make_entry_point("two", lambda: {"two.jinja": "two"})
    with patch("gain.templates.entry_points", return_value=[one, two]):
        first = templates_digest()
    reset_caches()
    listed = pathlib.Path.iterdir
    monkeypatch.setattr(
        pathlib.Path, "iterdir",
        lambda self: iter(sorted(listed(self), reverse=True)))

    with patch("gain.templates.entry_points", return_value=[two, one]):
        second = templates_digest()

    assert sorted(listed(template_files), reverse=True) != sorted(
        listed(template_files))
    assert second == first


def test_a_renamed_template_file_changes_the_digest(
    template_files: pathlib.Path,
) -> None:
    """The new name keeps the file in its place in the sorted order."""
    first = templates_digest()
    page = template_files / "basic.jinja"
    page.rename(template_files / "basicx.jinja")

    reset_caches()

    assert templates_digest() != first


def test_a_renamed_provider_template_changes_the_digest() -> None:
    old = make_entry_point("p", lambda: {"old.jinja": "same source"})
    new = make_entry_point("p", lambda: {"new.jinja": "same source"})
    with patch("gain.templates.entry_points", return_value=[old]):
        first = templates_digest()

    reset_caches()

    with patch("gain.templates.entry_points", return_value=[new]):
        assert templates_digest() != first
