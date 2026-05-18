"""Central Jinja2 template environment for GAIn.

Provides a singleton Environment that resolves templates in two stages:

1. Physical files under gain/templates/template_files/ via PackageLoader.
2. Strings supplied by callables registered under the
   "gain.templates.providers" entry-point group.  Each callable must
   return a ``dict[str, str]`` mapping template name to template source.
   All provider dictionaries are merged lazily on first miss.

Raises ``jinja2.TemplateNotFound`` if a name is not found in either stage.
"""
from __future__ import annotations

from importlib.metadata import entry_points
from typing import TYPE_CHECKING

from jinja2 import (
    BaseLoader,
    ChoiceLoader,
    Environment,
    PackageLoader,
    Template,
    TemplateNotFound,
)

if TYPE_CHECKING:
    from collections.abc import Callable

_env: Environment | None = None
_provider_cache: dict[str, str] | None = None


def _get_provider_templates() -> dict[str, str]:
    global _provider_cache
    if _provider_cache is None:
        merged: dict[str, str] = {}
        for ep in entry_points(group="gain.templates.providers"):
            provider_fn = ep.load()
            for name, source in provider_fn().items():
                if name in merged and merged[name] != source:
                    raise ValueError(
                        f"Template name conflict: '{name}' registered by "
                        f"provider '{ep.name}' conflicts with an existing "
                        f"provider registration."
                    )
                merged[name] = source
        _provider_cache = merged
    return _provider_cache


class _ProviderLoader(BaseLoader):
    """Jinja2 loader that reads templates from entry-point provider dicts."""

    def get_source(
        self, environment: Environment, template: str,  # noqa: ARG002
    ) -> tuple[str, None, Callable[[], bool]]:
        source = _get_provider_templates().get(template)
        if source is None:
            raise TemplateNotFound(template)
        return source, None, lambda: True


def get_jinja_env() -> Environment:
    """Return the singleton GAIn Jinja2 Environment."""
    global _env
    if _env is None:
        _env = Environment(  # noqa: S701
            loader=ChoiceLoader([
                PackageLoader("gain.templates", "template_files"),
                _ProviderLoader(),
            ]),
        )
    return _env


def get_template(name: str) -> Template:
    """Convenience wrapper — raises TemplateNotFound if name is absent."""
    return get_jinja_env().get_template(name)
