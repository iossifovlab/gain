# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""Architecture tests for the ``web_api`` project.

**Why this fence lives here and not in ``core``.**  ``core`` already has
``test_markdown_rendering_goes_through_the_one_wrapper_module``, and
widening it to cover ``web_api`` is the obvious move -- and a silent one.
Each project's CI image copies only its own project: ``core/Dockerfile``
brings in ``core/`` alone, so a sweep aimed at ``web_api`` from there
matches no files, collects no offenders, and *passes*.  Green in CI, green
in a dev checkout, policing nothing.  ``web_api/Dockerfile`` copies both
``core/gain/`` and ``web_api/``, so a fence sitting here can see the tree
it names.  A fence belongs in the project whose tree it polices.

The same split is why this module carries its own import extraction rather
than importing ``core``'s: ``core/tests/`` is not in the ``web_api`` image
(only the ``gain`` package is), so that import would work locally and fail
in CI.

Paths are resolved from this module's own location, never from the working
directory -- CI runs pytest from the project directory, not the repo root.

What the rule forbids is *reaching markdown2's un-rescued output*, which is
a wider net than "imports markdown2": the wrapper module renders through
the library and so re-exports it under the bare name ``markdown``, and
``from gain.templates.markdown_support import markdown`` reopens gain#742
in full while looking like the fix.  ``core``'s fence does not cover that
spelling.
"""
from __future__ import annotations

import ast
import pathlib

import pytest
from gain.annotation import pipeline_doc

#: The ``web_api`` project root: this file is ``web_annotation/tests/``.
WEB_API_SRC = pathlib.Path(__file__).resolve().parents[2]

#: The import every Markdown render in ``web_api`` must use.
WRAPPER_IMPORT = "from gain.templates.markdown_support import render_markdown"

#: Names that reach markdown2's un-rescued output.  The second is the
#: wrapper module's own re-export: it renders *through* the library, so it
#: binds the raw function under the bare name ``markdown``, and importing
#: that is one word away from importing the wrapper.
_UNWRAPPED = frozenset({
    "markdown2",
    "gain.templates.markdown_support.markdown",
})

#: Callables that import by name at runtime, so their string argument is an
#: import and not merely data.
_DYNAMIC_IMPORTERS = frozenset({"import_module", "__import__"})


def _renders_markdown_unwrapped(dotted: str) -> bool:
    """Does importing ``dotted`` reach markdown2 without the rescue?

    Compared on dotted segments rather than characters, so a package that
    merely starts with the same letters (``markdown2_extras``) is not swept
    up, while a submodule (``markdown2.markdown``) is.
    """
    return any(
        dotted == name or dotted.startswith(f"{name}.")
        for name in _UNWRAPPED
    )


@pytest.mark.parametrize(("dotted", "unwrapped"), [
    ("markdown2", True),
    ("markdown2.markdown", True),
    # The wrapper module re-exports the raw library under the bare name
    # ``markdown`` (it renders through it).  Importing *that* is one word
    # away from the correct import and reopens gain#742 in full.
    ("gain.templates.markdown_support.markdown", True),
    ("gain.templates.markdown_support.render_markdown", False),
    ("gain.templates.markdown_support", False),
    ("gain.templates", False),
    # Prefix-matching must be on dotted segments, not on characters.
    ("markdown2_extras", False),
    ("mymarkdown2", False),
])
def test_which_imports_count_as_rendering_markdown_unwrapped(
    dotted: str, *, unwrapped: bool,
) -> None:
    """The rule the sweep applies, stated on its own.

    Table-driven rather than by planting modules: the sweep's job is to
    find imports, this predicate's job is to judge them, and a bug in the
    judgement is invisible in an empty offender list.
    """
    assert _renders_markdown_unwrapped(dotted) is unwrapped


def test_no_web_api_module_imports_the_markdown_library_directly() -> None:
    """Every Markdown render in ``web_api`` goes through the wrapper.

    ``gain.templates.markdown_support.render_markdown`` post-processes
    markdown2's output so documentation prose survives a bogus tag --
    ``values <thresh are dropped`` reaches the reader whole instead of
    being swallowed by the browser (gain#736).  A module that imports the
    library directly re-opens that defect at its own sink, which is
    precisely what gain#742 was: the pipeline-documentation view kept its
    direct import when the nine ``core`` sinks moved to the wrapper.

    This is not a sanitizer and the wrapper is not a security boundary --
    GRR content is trusted by authorship (ADR 0016).  What is pinned here
    is that there is one Markdown sink, not that its output is safe.
    """
    offenders = [
        f"{py.relative_to(WEB_API_SRC)}: {imported}"
        for py in sorted(WEB_API_SRC.rglob("*.py"))
        for imported in sorted(_imported_modules(py))
        if _renders_markdown_unwrapped(imported)
    ]

    assert offenders == [], (
        f"these web_api modules reach markdown2's un-rescued output "
        f"instead of using `{WRAPPER_IMPORT}`: {offenders}. That output "
        f"leaves prose like 'values <thresh are dropped' to be eaten by "
        f"the browser as a bogus tag (gain#736, gain#742)"
    )


@pytest.mark.parametrize("source", [
    "import markdown2",
    "import markdown2 as md",
    "from markdown2 import markdown",
    "from markdown2 import markdown as _m",
    "from markdown2.extras import thing",
    "from importlib import import_module\nx = import_module('markdown2')",
    "import importlib\nx = importlib.import_module('markdown2')",
    "x = __import__('markdown2')",
    "from gain.templates.markdown_support import markdown",
])
def test_the_sweep_sees_every_spelling_that_reaches_the_library(
    source: str,
) -> None:
    """Each spelling a regression could plausibly take must be caught."""
    imported = _imported_names(source, package=["web_annotation"])

    assert any(_renders_markdown_unwrapped(name) for name in imported), (
        f"not caught: {source!r} -> {sorted(imported)}")


@pytest.mark.parametrize("source", [
    '"""A docstring mentioning markdown2 and markdown2.markdown."""',
    'RENDERERS = {"markdown2": "the library"}',
    # The row that pins the narrowing: a string constant that IS a call
    # argument, but to a call that imports nothing.
    'print("markdown2")',
    "from gain.templates.markdown_support import render_markdown",
])
def test_naming_the_library_without_importing_it_is_not_an_offence(
    source: str,
) -> None:
    """Data is not an import.

    Worth pinning rather than assuming: reading string constants is how
    the dynamic spelling is caught, and reading them indiscriminately --
    which is what ``core``'s extractor does -- turns any module that
    merely names the library into an offender.
    """
    imported = _imported_names(source, package=["web_annotation"])

    assert not any(_renders_markdown_unwrapped(name) for name in imported), (
        f"false positive: {source!r} -> {sorted(imported)}")


def test_a_relative_import_climbing_past_the_root_yields_no_phantom_name(
) -> None:
    """Clamp, do not slice negatively.

    A negative slice counts from the *end* of the package path, so an
    over-deep ``from ....x import y`` silently resolves to a name built
    from the wrong end -- plausible-looking, and wrong.  Such an import is
    illegal Python anyway; what matters is that the extractor does not
    invent a name for it.
    """
    package = ["web_annotation", "pipelines"]

    imported = _imported_names("from ....deep import thing", package=package)

    assert imported == {"deep", "deep.thing"}, (
        f"expected the climb to bottom out at the root, got {imported}")


def test_the_fence_can_see_the_project_it_polices() -> None:
    """The sweep above must not pass by finding nothing.

    An empty-set assertion is satisfied just as well by a tree that is not
    there, which is the failure mode this whole file is placed to avoid.
    Pinned against the module the fence exists for, and against one at the
    project root -- the second is what would go red if ``WEB_API_SRC``
    resolved to the package instead of the project.

    A sibling test file is deliberately *not* pinned: this module lives in
    the swept tree, so finding it proves nothing about the sweep's reach.
    """
    swept = {py.resolve() for py in WEB_API_SRC.rglob("*.py")}

    assert (WEB_API_SRC / "web_annotation" / "pipelines"
            / "views.py").resolve() in swept
    assert (WEB_API_SRC / "manage.py").resolve() in swept


def _imported_modules(py: pathlib.Path) -> frozenset[str]:
    """Absolute dotted names ``py`` imports, however it spells them.

    Resolved from the AST rather than matched against the source text, so
    that ``import markdown2``, ``from markdown2 import markdown`` and an
    ``importlib.import_module("markdown2")`` are all seen -- and so that a
    mention inside a docstring, which imports nothing, is not.

    The dynamic spelling is read only from the argument of a call to
    ``import_module``/``__import__``.  ``core``'s equivalent takes *every*
    string constant instead, which reports a module that merely names the
    library in data -- and forces the rule's own module to exempt itself,
    since it has to name what it forbids.  Narrowing to the call argument
    costs nothing here: a name assembled at runtime is out of reach of a
    static sweep either way.

    Relative imports are resolved against the containing package, taken as
    the directories between ``WEB_API_SRC`` and the file.  A level that
    climbs past the project root is clamped rather than allowed to slice
    negatively, which would silently yield a name from the wrong end of the
    package path.
    """
    return _imported_names(
        py.read_text(encoding="utf8"),
        package=list(py.relative_to(WEB_API_SRC).parts[:-1]),
    )


def _imported_names(source: str, package: list[str]) -> frozenset[str]:
    """``_imported_modules`` over a source string in a known package.

    Split out so the extraction can be exercised on a literal source rather
    than on a file planted inside the very tree the sweep walks -- CI runs
    this suite under ``pytest -n 5``, where such a file would race the
    sweep and fail it from another worker.
    """
    tree = ast.parse(source)

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                # `from . import x` stays in the containing package; each
                # extra dot climbs one above it.
                base = package[:max(0, len(package) - (node.level - 1))]
            else:
                base = []
            prefix = [*base, node.module] if node.module else base
            module = ".".join(prefix)
            if module:
                imported.add(module)
            imported.update(
                f"{module}.{alias.name}" if module else alias.name
                for alias in node.names
            )
        elif isinstance(node, ast.Call):
            imported.update(_dynamically_imported_names(node))
    return frozenset(imported)


def _dynamically_imported_names(call: ast.Call) -> set[str]:
    """The module ``call`` imports by name, if it is such a call."""
    func = call.func
    name = func.attr if isinstance(func, ast.Attribute) else (
        func.id if isinstance(func, ast.Name) else None)
    if name not in _DYNAMIC_IMPORTERS or not call.args:
        return set()
    first = call.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return {first.value}
    return set()


#: The pipeline documentation template, spelled out rather than imported
#: from ``gain``.  A fence that scans for a name its own subject supplies
#: goes blind the moment the subject renames it.  Kept honest by
#: ``test_the_doc_template_fence_names_what_the_renderer_binds``.
DOC_TEMPLATE = "annotate_doc_pipeline_template.jinja"


def test_the_doc_template_fence_names_what_the_renderer_binds() -> None:
    """The literal above must be the name ``gain``'s renderer asks for."""
    assert pipeline_doc.DOC_TEMPLATE_NAME == DOC_TEMPLATE


def test_no_web_api_module_binds_the_pipeline_doc_template() -> None:
    """The pipeline documentation page is rendered by ``gain``, not here.

    This project used to hold a second renderer of that template, in the
    download view, with its own copy of the resource/histogram address
    pair.  It drifted: ``d8624b787`` moved ``gain``'s addresses onto the
    GRR's public mirror and left this copy on the repository's own url,
    where it stayed for two months (#841).  Since #952 the view calls
    ``gain.annotation.pipeline_doc.render_pipeline_doc`` and names no
    template of its own.

    This is the half of the fence that ``core`` cannot reach: its CI image
    copies only ``core/``, so the sweep there sees ``gain`` alone -- and
    ``gain`` is not where the drift happened.

    Test modules are not swept.  Binding the template in a test builds a
    fixture, not a second renderer; what the rule forbids is shipping one.
    ``test_the_fence_can_see_the_project_it_polices`` above is what stops
    this sweep passing by finding nothing.
    """
    offenders = sorted(
        str(py.relative_to(WEB_API_SRC))
        for py in WEB_API_SRC.rglob("*.py")
        if "tests" not in py.parts and DOC_TEMPLATE in py.read_text()
    )

    assert offenders == [], (
        f"these web_api modules name {DOC_TEMPLATE} instead of calling "
        f"`from gain.annotation.pipeline_doc import render_pipeline_doc`: "
        f"{offenders}. A second binder here is exactly how this project's "
        f"copy of the address policy drifted from gain's (#841, #952)"
    )
