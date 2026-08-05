"""Central Jinja2 template environment for GAIn.

Provides a singleton Environment that resolves templates in two stages:

1. Physical files under gain/templates/template_files/ via PackageLoader.
2. Strings supplied by callables registered under the
   "gain.templates.providers" entry-point group.  Each callable must
   return a ``dict[str, str]`` mapping template name to template source.
   All provider dictionaries are merged lazily on first miss.

Raises ``jinja2.TemplateNotFound`` if a name is not found in either stage.

The environment autoescapes, so a template interpolating a value that is
already markup -- a nested render, ``markdown2`` output, a pandas
``to_html`` table -- has to say so with ``|safe``.  Autoescaping is HTML
escaping, which is wrong for the few templates in MARKDOWN_TEMPLATES:
they emit Markdown that GPF renders downstream, and escaping there
mangles the Markdown syntax and the ``&`` in histogram image URLs.  The
decision is by template name rather than by extension because every
template here is named ``*.jinja``, HTML and Markdown alike.

Templates supplied by an out-of-tree provider load through this same
environment and are autoescaped along with the rest.

``|safe`` on a rendered Markdown string opts that string out of
autoescaping, so the ``markdown`` callable a template renders with is a
sink for whatever HTML the source text carried.  Render GRR-supplied
text with :func:`render_untrusted_markdown` rather than with
``markdown2.markdown`` itself.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from importlib.metadata import entry_points
from typing import TYPE_CHECKING, cast

from jinja2 import (
    BaseLoader,
    ChoiceLoader,
    Environment,
    PackageLoader,
    Template,
    TemplateNotFound,
)
from markdown2 import markdown

if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass
class _TemplateCache:
    env: Environment | None = field(default=None)
    provider_cache: dict[str, str] | None = field(default=None)


_state = _TemplateCache()

MARKDOWN_TEMPLATES = frozenset({
    "gene_score_help.jinja",
    "genomic_score_help.jinja",
    "score_histogram.jinja",
})


def _get_provider_templates() -> dict[str, str]:
    if _state.provider_cache is None:
        merged: dict[str, str] = {}
        for ep in entry_points(group="gain.templates.providers"):
            provider_fn = ep.load()
            for name, source in provider_fn().items():
                if name in merged and merged[name] != source:
                    raise ValueError(
                        f"Template name conflict: '{name}' registered by "
                        f"provider '{ep.name}' conflicts with an existing "
                        f"provider registration.",
                    )
                merged[name] = source
        _state.provider_cache = merged
    return _state.provider_cache


class _ProviderLoader(BaseLoader):
    """Jinja2 loader that reads templates from entry-point provider dicts."""

    def get_source(
        self, environment: Environment, template: str,  # noqa: ARG002
    ) -> tuple[str, None, Callable[[], bool]]:
        source = _get_provider_templates().get(template)
        if source is None:
            raise TemplateNotFound(template)
        return source, None, lambda: True


def _autoescape(template_name: str | None) -> bool:
    """Autoescape every template but the Markdown-emitting ones."""
    return template_name not in MARKDOWN_TEMPLATES


def get_jinja_env() -> Environment:
    """Return the singleton GAIn Jinja2 Environment."""
    if _state.env is None:
        _state.env = Environment(
            loader=ChoiceLoader([
                PackageLoader("gain.templates", "template_files"),
                _ProviderLoader(),
            ]),
            autoescape=_autoescape,
        )
    return _state.env


def get_template(name: str) -> Template:
    """Convenience wrapper — raises TemplateNotFound if name is absent."""
    return get_jinja_env().get_template(name)


def render_untrusted_markdown(text: str) -> str:
    """Render GRR-supplied Markdown, escaping any HTML embedded in it.

    A template interpolating Markdown has to mark the rendered result
    ``|safe`` -- it is markup by then, and autoescaping would show the
    tags Markdown just produced.  That makes the ``markdown`` callable
    the sink: whatever HTML the source text carried passes through
    ``markdown2`` untouched by default and lands in the page as markup.
    Score descriptions and annotator documentation come from a resource's
    own ``genomic_resource.yaml``, which this project does not author, so
    that HTML is not ours to trust.

    ``safe_mode="escape"`` escapes embedded HTML while leaving Markdown
    syntax alone: emphasis, links, lists and code spans still render, a
    ``<script>`` becomes visible text, and prose comparing values
    (``p < 0.05``) survives instead of being eaten by the tokenizer.
    Bind this once into a template's render context rather than passing
    ``markdown2.markdown`` itself, so a call site added to the template
    later cannot reintroduce the hole.
    """
    return cast(str, markdown(text, safe_mode="escape"))
