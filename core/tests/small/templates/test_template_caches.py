"""``reset_caches`` forgets the environment and the provider templates.

The Jinja environment and the merged provider-template dict are
process-wide singletons, built on first use.  Every test in this package
resets them before and after itself (see ``conftest.py``), and this is
what that reset is asked to do: the next call builds afresh, so a
provider registered after the reset is seen.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from gain.templates import get_jinja_env, get_template, reset_caches
from jinja2 import TemplateNotFound


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
    ep = MagicMock()
    ep.name = "late"
    ep.load.return_value = lambda: {"late.jinja": "late {{ v }}"}

    reset_caches()

    with patch("gain.templates.entry_points", return_value=[ep]):
        assert get_template("late.jinja").render(v="one") == "late one"
