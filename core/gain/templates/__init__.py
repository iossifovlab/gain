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

The environment carries one global, ``markdown``: the Markdown wrapper
from ``gain.templates.markdown_support``.  It is registered here rather
than passed by each caller as a render kwarg so that a template calling
``markdown(...)`` gets the rescuing wrapper whether or not whoever
renders it thought about the name -- gain#742 was a render site that did
not (gain#751).  A render kwarg still shadows a global, so a caller that
passes its own ``markdown=`` wins; no caller in this repo does.

The global serves templates.  The several ``gain`` modules that render a
resource's ``meta`` description or an ``about.md`` in *Python* -- before
the result enters a dict some template dumps generically -- still import
``render_markdown`` and call it themselves; a template global cannot
reach them.  Those imports are what the architecture fence governs.

``render_markdown`` is imported under its own name deliberately.  The
wrapper module renders *through* markdown2 and so re-exports the raw
function under the bare name ``markdown``: binding *that* here would
leave every template in the stack without the bogus-tag rescue while
looking correct.  ``core``'s architecture tests refuse that import; they
read imports, so reaching the same function as an attribute of an
imported module would pass them -- what catches that is the rescue being
asserted on rendered output.

The import sits inside ``get_jinja_env`` rather than at module scope so
that importing ``gain.templates`` does not drag in markdown2 for callers
that only ever fetch a template: it costs about 10ms, and the annotation
workers pay module import per spawned process.
"""
from __future__ import annotations

from dataclasses import dataclass, field
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
        # pylint: disable=import-outside-toplevel
        from gain.templates.markdown_support import render_markdown
        env = Environment(
            loader=ChoiceLoader([
                PackageLoader("gain.templates", "template_files"),
                _ProviderLoader(),
            ]),
            autoescape=_autoescape,
        )
        env.globals["markdown"] = render_markdown
        # Published last, so no caller can reach a half-configured
        # environment: assigning first and installing the global after
        # leaves a window where the singleton renders UndefinedError.
        _state.env = env
    return _state.env


def get_template(name: str) -> Template:
    """Convenience wrapper — raises TemplateNotFound if name is absent."""
    return get_jinja_env().get_template(name)
