# pylint: disable=W0621,C0114,C0115,C0116,W0212,W0613
"""``markdown`` is supplied by the shared environment, not by each caller.

Every template that renders documentation prose calls ``markdown(...)``.
Until gain#751 that name arrived as a render kwarg: each caller imported
``render_markdown`` and passed ``markdown=markdown`` into ``.render()``,
so a *new* render site had to remember to do the same -- and gain#742 was
exactly that failure, found by hand rather than by CI.

The name now lives on the singleton environment's ``globals``, so a
template gets the wrapper whether or not its caller thought about it.
The test below renders through the ``gain.templates.providers`` entry
point -- an out-of-tree template, wired by nobody in this repo -- because
that is the sharpest statement of the promise: no caller arranged for the
rescue, and it happens anyway.

The assertion is on the *rescue*, never on the mere presence of the
global.  ``"markdown" in get_jinja_env().globals`` would pass with the
global bound to raw ``markdown2`` -- and would have passed while every
caller still shadowed it with a kwarg, which is the no-op this issue
warned about.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import gain.templates as templates_module
import pytest
from gain.templates import get_template

#: Unique to this file, so an assertion cannot be satisfied by text the
#: template ships itself (the false-signal lesson of gain#558).
MARKER = "gainglobal751"


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


def test_a_template_renders_markdown_without_the_caller_supplying_it() -> None:
    """The headline promise: no ``markdown=`` kwarg, and prose survives.

    ``values <thresh are dropped`` is the gain#736 defect: markdown2 emits
    the ``<`` raw and the browser's tokenizer then eats the rest of the
    sentence as one bogus tag.  Seeing it escaped proves the global is
    bound to the wrapper rather than to the library it renders through.
    """
    def doc_plugin():
        return {"plugin_doc.jinja": "{{ markdown(text)|safe }}"}

    ep = _make_ep("doc_plugin", doc_plugin)

    with patch("gain.templates.entry_points", return_value=[ep]):
        page = get_template("plugin_doc.jinja").render(
            text=f"values <thresh are dropped {MARKER}")

    assert f"&lt;thresh are dropped {MARKER}" in page


def test_the_environment_is_published_already_carrying_the_global(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The singleton becomes visible configured, never half-configured.

    ``get_jinja_env`` builds the environment once and caches it.  Building
    it and *then* reaching back to install the global would publish a
    usable environment without ``markdown`` for the two bytecodes in
    between: a second thread arriving in that window gets the cached
    environment and renders ``UndefinedError``.  It is a narrow window --
    an unassisted thread race does not reproduce it -- so this pins the
    invariant at the seam instead of trying to lose the race on purpose.

    ``web_api`` renders pipeline documentation off the event loop through
    ``sync_to_async``, and nothing warms the environment at import, so two
    concurrent requests on a fresh process are exactly that shape.
    """
    published: list[bool] = []

    class _RecordingCache:
        """Stands in for the module's cache, watching what gets published."""

        def __init__(self) -> None:
            self._env = None

        @property
        def env(self):
            return self._env

        @env.setter
        def env(self, value) -> None:
            published.append(value is not None and "markdown" in value.globals)
            self._env = value

    monkeypatch.setattr(templates_module, "_state", _RecordingCache())

    templates_module.get_jinja_env()

    assert published == [True]
