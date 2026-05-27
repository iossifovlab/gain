# pylint: disable=W0621,C0114,C0115,C0116,W0212,W0613
"""Tests for third-party plugin template registration via entry points."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import gain.templates as templates_module
import jinja2
import pytest
from gain.templates import get_template


@pytest.fixture(autouse=True)
def reset_template_caches():
    """Reset singleton caches before and after each test."""
    templates_module._state.env = None
    templates_module._state.provider_cache = None
    yield
    templates_module._state.env = None
    templates_module._state.provider_cache = None


def _make_ep(name: str, provider_fn):
    ep = MagicMock()
    ep.name = name
    ep.load.return_value = provider_fn
    return ep


class TestPluginTemplateRegistration:
    def test_plugin_template_is_accessible(self):
        def my_plugin():
            return {"my_plugin.jinja": "Hello {{ name }}!"}

        ep = _make_ep("my_plugin", my_plugin)
        with patch("gain.templates.entry_points", return_value=[ep]):
            result = get_template("my_plugin.jinja").render(name="world")

        assert result == "Hello world!"

    def test_multiple_plugins_register_distinct_templates(self):
        def plugin_a():
            return {"plugin_a.jinja": "A: {{ v }}"}

        def plugin_b():
            return {"plugin_b.jinja": "B: {{ v }}"}

        eps = [_make_ep("plugin_a", plugin_a), _make_ep("plugin_b", plugin_b)]

        with patch("gain.templates.entry_points", return_value=eps):
            result_a = get_template("plugin_a.jinja").render(v="x")
            result_b = get_template("plugin_b.jinja").render(v="y")

        assert result_a == "A: x"
        assert result_b == "B: y"

    def test_unregistered_template_raises_not_found(self):
        with (
            patch("gain.templates.entry_points", return_value=[]),
            pytest.raises(jinja2.TemplateNotFound),
        ):
            get_template("nonexistent_plugin.jinja")

    def test_provider_callable_invoked_only_once(self):
        """Provider is called at most once regardless of templates fetched."""
        call_count = 0

        def counting_plugin():
            nonlocal call_count
            call_count += 1
            return {"cached.jinja": "content"}

        ep = _make_ep("p", counting_plugin)
        with patch("gain.templates.entry_points", return_value=[ep]):
            get_template("cached.jinja")
            get_template("cached.jinja")

        assert call_count == 1

    def test_plugin_cannot_shadow_builtin_file_template(self):
        """PackageLoader wins; built-in file templates cannot be overridden."""
        builtin_name = "resource_template.jinja"

        def sneaky_plugin():
            return {builtin_name: "OVERRIDDEN"}

        ep = _make_ep("sneaky", sneaky_plugin)
        with patch("gain.templates.entry_points", return_value=[ep]):
            env = templates_module.get_jinja_env()
            assert env.loader is not None
            source, _, _ = env.loader.get_source(env, builtin_name)

        assert source != "OVERRIDDEN"
        assert len(source) > 20  # confirms a real template was loaded


class TestPluginNameCollisions:
    def test_identical_content_from_two_plugins_is_accepted(self):
        """Same name + same content from two providers is idempotent."""
        source = "Shared: {{ x }}"

        eps = [
            _make_ep("p1", lambda: {"shared.jinja": source}),
            _make_ep("p2", lambda: {"shared.jinja": source}),
        ]

        with patch("gain.templates.entry_points", return_value=eps):
            result = get_template("shared.jinja").render(x="ok")

        assert result == "Shared: ok"

    def test_conflicting_content_raises_value_error(self):
        """Conflicting content for the same name raises ValueError."""
        eps = [
            _make_ep("p1", lambda: {"conflict.jinja": "Version A"}),
            _make_ep("p2", lambda: {"conflict.jinja": "Version B"}),
        ]

        with (
            patch("gain.templates.entry_points", return_value=eps),
            pytest.raises(ValueError, match="Template name conflict"),
        ):
            get_template("conflict.jinja")

    def test_conflict_error_names_the_offending_provider(self):
        """ValueError message names the provider that caused the conflict."""
        eps = [
            _make_ep("first_plugin", lambda: {"dupe.jinja": "Original"}),
            _make_ep("second_plugin", lambda: {"dupe.jinja": "Different"}),
        ]

        with (
            patch("gain.templates.entry_points", return_value=eps),
            pytest.raises(ValueError, match="second_plugin"),
        ):
            get_template("dupe.jinja")
