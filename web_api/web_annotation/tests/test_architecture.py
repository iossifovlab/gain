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
import functools
import pathlib
from collections.abc import Iterator

import gain
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


@functools.cache
def _imported_modules(py: pathlib.Path) -> frozenset[str]:
    """Absolute dotted names ``py`` imports, however it spells them.

    Cached per file: two rules sweep the whole project -- the Markdown
    one above and the import-time-deprecation one below -- and the
    sources do not change within a test run.

    Resolved from the AST rather than matched against the source text, so
    that ``import markdown2``, ``from markdown2 import markdown`` and an
    ``importlib.import_module("markdown2")`` are all seen -- and so that a
    mention inside a docstring, which imports nothing, is not.

    The dynamic spelling is read only from the argument of a call to
    ``import_module``/``__import__``.  ``core``'s equivalent takes *every*
    string constant instead, which reports a module that merely names the
    library in data.  Narrowing to the call argument costs nothing here:
    a name assembled at runtime is out of reach of a static sweep either
    way.

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


#: The ``gain`` package this project builds on.  Resolved from the
#: imported package rather than by walking up from this file: the CI
#: image lays ``core/gain`` and ``web_api`` out differently from the
#: checkout, and a path that missed would derive an empty set and police
#: nothing.  ``test_the_derived_set_finds_the_shim_it_is_anchored_to`` is
#: what would go red if it ever missed.
GAIN_PKG = pathlib.Path(gain.__file__).parent

#: The deprecated alias for ``gain.annotation.annotate_tabular``, which
#: warns from its module body.  Named so the rule below has an anchor
#: that cannot pass on an empty scan.
ANNOTATE_COLUMNS_SHIM = "gain.annotation.annotate_columns"


@functools.cache
def _modules_warning_at_import() -> frozenset[str]:
    """Dotted names of the ``gain`` modules that warn on import.

    The subject set of the fence below, read out of the tree rather than
    listed, so a shim added to ``gain`` later is covered by the rule that
    already exists.  gain#1153 wrote a fence naming ``score_annotator``:
    it protected that one module, for exactly as long as it existed, and
    gain#1154 deleted it again along with the shim.

    Cached because two rules here ask for the set, and deriving it reads
    and parses every module in the ``gain`` package.
    """
    found = set()
    for py in GAIN_PKG.rglob("*.py"):
        if not _warns_at_import(py.read_text(encoding="utf8")):
            continue
        parts = list(py.relative_to(GAIN_PKG).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts.pop()
        found.add(".".join(["gain", *parts]))
    return frozenset(found)


def _warns_at_import(source: str) -> bool:
    """Does importing a module with this source emit a DeprecationWarning?

    Only what importing *runs* counts.  A module body, a class body and
    any ``if``/``try``/``with``/``for`` nesting inside them all execute on
    import; a ``def`` body executes when it is called, which may be never.
    """
    return any(
        _is_deprecation_warn(node)
        for node in _import_time_nodes(ast.parse(source))
    )


def _import_time_nodes(node: ast.AST) -> Iterator[ast.AST]:
    """Every node under ``node`` that importing the module would execute.

    Descends into class bodies -- those run at import -- and stops at the
    *body* of every ``def`` and ``lambda``, which does not.  A ``def`` is
    not skipped whole: its decorators and its argument defaults are
    evaluated where the ``def`` is written, so they run at import like
    any other module-level expression.  Skipping the node entirely would
    also make a decorator count on a ``class`` and not on a ``def``,
    which is a distinction nothing could justify.
    """
    children = (
        _signature_nodes(node) if isinstance(node, _DEFERRED_BODIES)
        else ast.iter_child_nodes(node))
    for child in children:
        yield child
        yield from _import_time_nodes(child)


#: Nodes whose body import does not run.
_DEFERRED_BODIES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)


def _signature_nodes(
    node: ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda,
) -> Iterator[ast.expr]:
    """The parts of a ``def``/``lambda`` that import evaluates anyway.

    The annotations are deliberately not among them.  Under ``from
    __future__ import annotations`` -- which more than half of ``gain``'s
    modules carry -- an annotation is never evaluated at all, so reading
    one proves nothing about what importing the module does.
    """
    yield from node.args.defaults
    yield from (d for d in node.args.kw_defaults if d is not None)
    if not isinstance(node, ast.Lambda):
        yield from node.decorator_list


def _is_deprecation_warn(node: ast.AST) -> bool:
    """Is ``node`` a ``warnings.warn(...)`` whose category is deprecation?

    The callee must be named ``warn``: a module that merely *names* the
    category -- ``filterwarnings("ignore", category=DeprecationWarning)``
    is the one that matters -- warns nobody, and sweeping it in would
    fence a module for silencing the thing this rule is about.
    """
    if not isinstance(node, ast.Call) or _tail_name(node.func) != "warn":
        return False
    # ``warn(DeprecationWarning("gone"))`` -- the category *is* the
    # message, and it warns exactly as loudly as the two-argument form.
    message = node.args[0] if node.args else None
    if isinstance(message, ast.Call) \
            and _tail_name(message.func) == "DeprecationWarning":
        return True
    by_keyword = {kw.arg: kw.value for kw in node.keywords}
    category = (node.args[1] if len(node.args) > 1
                else by_keyword.get("category"))
    return _tail_name(category) == "DeprecationWarning"


def _tail_name(node: ast.AST | None) -> str | None:
    """The last segment of a dotted name, or ``None`` if it is not one.

    Both halves of the judgement above are this question: the callee must
    end in ``warn`` and the category in ``DeprecationWarning``, whether
    either is reached bare or through a module.  An alias bound at import
    (``from warnings import warn as _w``) is out of reach of a static
    read and is not covered; every deprecation in this repository spells
    both the call and the category out.
    """
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


#: What a module must do to be a subject of the rule below, and what it
#: may do without becoming one.  The same rows ``core`` pins, against
#: this project's own copy of the predicate -- it cannot import
#: ``core``'s, because ``core/tests`` is not in this project's CI image.
WARNS_AT_IMPORT_CASES = (
    # The shim shape: a warn in the module body.
    (("import warnings\n"
      "warnings.warn('gone', DeprecationWarning, stacklevel=2)\n"), True),
    # The category named by keyword rather than by position.
    (("import warnings\n"
      "warnings.warn('gone', category=DeprecationWarning)\n"), True),
    # Module-level nesting still runs on import.
    (("import warnings\n"
      "if True:\n"
      "    warnings.warn('gone', DeprecationWarning)\n"), True),
    # A class body runs on import too.
    (("import warnings\n"
      "class C:\n"
      "    warnings.warn('gone', DeprecationWarning)\n"), True),
    # The category as the message, which is idiomatic and warns just as
    # loudly: `warn(DeprecationWarning("gone"))`.
    (("import warnings\n"
      "warnings.warn(DeprecationWarning('gone'))\n"), True),
    # The category reached through a module rather than by bare name.
    (("import builtins, warnings\n"
      "warnings.warn('gone', builtins.DeprecationWarning)\n"), True),
    # A def body waits to be called, but its decorators and its argument
    # defaults are evaluated where the def is written -- at import.
    (("import warnings\n"
      "def g(x=warnings.warn('gone', DeprecationWarning)):\n"
      "    pass\n"), True),
    (("import warnings\n"
      "@(warnings.warn('gone', DeprecationWarning) or (lambda f: f))\n"
      "def g():\n"
      "    pass\n"), True),
    # The same decorator on a class -- the two must not disagree.
    (("import warnings\n"
      "@(warnings.warn('gone', DeprecationWarning) or (lambda c: c))\n"
      "class C:\n"
      "    pass\n"), True),
    # A keyword-only default is evaluated at import like any other.
    (("import warnings\n"
      "def g(*, x=warnings.warn('gone', DeprecationWarning)):\n"
      "    pass\n"), True),
    # And a lambda's default, whose body is otherwise deferred.
    (("import warnings\n"
      "f = lambda x=warnings.warn('gone', DeprecationWarning): x\n"), True),
    # A function body does not run on import.
    (("import warnings\n"
      "def f():\n"
      "    warnings.warn('gone', DeprecationWarning, stacklevel=2)\n"), False),
    # Nor a method body -- the shape every deprecated method in gain uses.
    (("import warnings\n"
      "class C:\n"
      "    def m(self):\n"
      "        warnings.warn('gone', DeprecationWarning)\n"), False),
    # Another category is not a deprecation.
    (("import warnings\n"
      "warnings.warn('careful', UserWarning)\n"), False),
    # No category at all is a UserWarning.
    ("import warnings\nwarnings.warn('careful')\n", False),
    # Naming the category without warning is not a warning.  A module
    # that silences the category at import must not be swept up.
    (("import warnings\n"
      "warnings.filterwarnings('ignore', category=DeprecationWarning)\n"),
     False),
)


def test_what_counts_as_a_warning_at_import() -> None:
    """The rule the derived set applies, stated on its own.

    Table-driven rather than by planting modules: extraction and
    judgement are separate jobs, and a bug in the judgement is invisible
    in an empty offender list.

    The two ``def``-body rows are the ones this pins.  A warn inside a
    function fires when it is *called*, which may be never -- and
    ``gain`` deprecates methods exactly that way, on modules half of that
    package imports.  Reading the tree with ``ast.walk`` instead of
    descending only what import executes puts every one of them in the
    derived set, and the fence then fails on the modules it was never
    about.

    One test over the whole table rather than a ``parametrize``, which is
    how ``core`` spells it: every test item in this project pays the
    autouse fixtures in ``conftest.py``, including one that opens a
    database transaction and creates a user -- about a third of a second
    each, against a judgement that takes no measurable time at all.  The
    loop reports every row that disagrees, so a failure still names them.
    """
    wrong = [
        source for source, at_import in WARNS_AT_IMPORT_CASES
        if _warns_at_import(source) is not at_import
    ]

    assert wrong == [], (
        f"the predicate disagrees with the table on: {wrong}")


def test_the_derived_set_finds_the_shim_it_is_anchored_to() -> None:
    """The derived set must not be empty, and must name a known shim.

    An empty offender list satisfies the fence below just as well when
    the derived set is empty -- and here that is the likelier accident of
    the two, because the set is derived from a tree in *another* project.
    ``GAIN_PKG`` resolving somewhere unexpected would be silent without
    this.
    """
    derived = _modules_warning_at_import()

    assert ANNOTATE_COLUMNS_SHIM in derived, (
        f"the anchor shim is not in the derived set: {sorted(derived)}. "
        f"Either GAIN_PKG no longer points at the gain package, or the "
        f"shim is gone and this needs anchoring on another module-level "
        f"DeprecationWarning -- an unanchored rule passes on an empty scan"
    )


def test_no_web_api_module_imports_a_module_that_warns_at_import() -> None:
    """No module here imports one whose import warns.

    A deprecated alias kept for outside callers -- ``annotate_columns``
    is kept for the CLI name -- warns from its module body, so
    *importing* it warns.  An in-tree caller makes every process that
    loads that module emit the warning, and pins the shim past the
    release meant to remove it, with nothing going red: the import
    works, and this project does not turn warnings into errors.

    The subject set is derived from ``gain`` rather than listed, so the
    next shim is covered by the rule that already exists.

    ``core``'s copy sweeps the ``gain`` package and cannot see this tree,
    the same split as the Markdown rule above: this project's CI image is
    the only one that contains it.
    """
    shims = _modules_warning_at_import()
    offenders = sorted(
        f"{py.relative_to(WEB_API_SRC)}: {imported}"
        for py in WEB_API_SRC.rglob("*.py")
        for imported in shims & _imported_modules(py)
    )

    assert offenders == [], (
        f"these web_api modules import a module that warns at import: "
        f"{offenders}. Import the module the shim forwards to -- "
        f"importing the shim warns in every process that loads the "
        f"importer, and keeps the shim alive past its removal"
    )


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
