# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""Architecture tests for gain package using pytestarch."""
import ast
import functools
import importlib
import os
import pathlib
import tomllib
from collections.abc import Container, Iterator

import pytest
from gain.annotation import pipeline_doc
from pytestarch import EvaluableArchitecture, get_evaluable_architecture

GAIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GAIN_SRC = os.path.join(GAIN_ROOT, "gain")
TESTS_SRC = os.path.dirname(os.path.abspath(__file__))


@pytest.fixture(scope="module")
def gain_arch() -> EvaluableArchitecture:
    return get_evaluable_architecture(
        GAIN_SRC,
        GAIN_SRC,
        exclude_external_libraries=False,
    )


@pytest.fixture(scope="module")
def gain_tests_arch() -> EvaluableArchitecture:
    return get_evaluable_architecture(
        TESTS_SRC,
        TESTS_SRC,
        exclude_external_libraries=False,
    )


def test_gain_core_does_not_import_from_gpf_core(
    gain_arch: EvaluableArchitecture,
) -> None:
    """gain_core (gain package) must not import from gpf_core (gpf package)."""
    gpf_imports = [
        module
        for module in gain_arch.modules
        if module == "gpf" or module.startswith("gpf.")
    ]
    assert gpf_imports == [], (
        f"gain_core must not import from gpf_core, but found: {gpf_imports}"
    )


def test_gain_core_tests_do_not_import_from_gpf_core(
    gain_tests_arch: EvaluableArchitecture,
) -> None:
    """gain_core tests must not import from gpf_core (gpf package)."""
    gpf_imports = [
        module
        for module in gain_tests_arch.modules
        if module == "gpf" or module.startswith("gpf.")
    ]
    assert gpf_imports == [], (
        f"gain_core tests must not import from gpf_core, "
        f"but found: {gpf_imports}"
    )


def test_the_table_layer_imports_nothing_from_the_score_layer() -> None:
    """``genomic_position_table`` must not depend on the score modules.

    The seam the record contract draws: a backend yields records and owns its
    payload's shape; the score layer says what those records MEAN.  Every
    backend-specific score module -- ``vcf_scores``, ``bigwig_scores`` -- sits
    on the score side of it and imports the table's constants, never the
    reverse.  An import back the other way would make the payload's shape and
    its interpretation mutually dependent, which is exactly what the two
    modules exist to keep apart.

    Written as a module-name scan rather than through pytestarch's rule DSL so
    that the failure names the offending module and the import it made. It
    therefore needs no ``EvaluableArchitecture`` -- do not add ``gain_arch``
    for symmetry with the two tests above. Unread, it only builds the gain
    import graph a second time, on whichever ``pytest -n`` worker happens to
    take this item (gain#863).
    """
    table_pkg = os.path.join(GAIN_SRC, "genomic_resources",
                             "genomic_position_table")
    score_modules = {
        "gain.genomic_resources.score_def",
        "gain.genomic_resources.vcf_scores",
        "gain.genomic_resources.bigwig_scores",
        "gain.genomic_resources.genomic_scores",
        "gain.genomic_resources.score_resource",
        "gain.genomic_resources.score_implementation",
    }
    offenders = []
    for py in pathlib.Path(table_pkg).rglob("*.py"):
        text = py.read_text(encoding="utf8")
        offenders.extend(
            f"{py}: {module}"
            for module in score_modules
            if f"import {module}" in text or f"from {module}" in text
        )
    assert offenders == [], (
        f"the genomic position table layer imports from the score layer: "
        f"{offenders}. A backend owns its payload's shape; the score layer "
        f"owns what the payload means. Move the constant down into the table "
        f"package and re-export it upward, as bigwig_scores does with "
        f"VALUE_COLUMN"
    )


def test_the_statistics_layer_resolves_contig_length_through_the_table(
) -> None:
    """No caller outside the table package reaches for the tabix length probe.

    ``get_chromosome_length_tabix`` is a tabix implementation detail.  The
    statistics layer used to call it directly, behind an ``isinstance`` ladder
    over concrete backend classes that also read the backend's pysam handle and
    its ``unmap_chromosome`` -- so the layer knew which backends existed and how
    each stored a length, a new backend could not be added without editing the
    ladder, and the ladder's ``else`` turned that omission into an
    ``AssertionError``.  Contig length is now asked of the table itself, through
    ``find_chromosome_length`` (gain#509).

    ``annotate_utils`` is the one legitimate caller left: it probes a pysam
    handle it opened itself, with no table involved.

    Written as an import scan rather than a call scan so it also catches the
    import being re-added ahead of its first use -- which is what silently
    re-points the mocks in the statistics tests at a symbol nothing calls.
    """
    probe = "get_chromosome_length_tabix"
    table_pkg = pathlib.Path(GAIN_SRC) / "genomic_resources" \
        / "genomic_position_table"
    allowed = {
        # defines it
        pathlib.Path(GAIN_SRC) / "utils" / "regions.py",
        # probes a handle it opened itself, not a table's
        pathlib.Path(GAIN_SRC) / "annotation" / "annotate_utils.py",
    }
    offenders = [
        str(py.relative_to(GAIN_SRC))
        for py in pathlib.Path(GAIN_SRC).rglob("*.py")
        if py not in allowed
        and table_pkg not in py.parents
        and probe in py.read_text(encoding="utf8")
    ]
    assert offenders == [], (
        f"{probe} is reached for outside the table package by: {offenders}. "
        f"Ask the table for a contig's length instead -- "
        f"find_chromosome_length reports either a length or a ContigExtent "
        f"saying why there is not one, for every backend"
    )


def test_no_gain_module_uses_stdlib_logging_directly() -> None:
    """Every gain module logs through `from gain import logging`.

    stdlib `import logging` skips the TRACE / USER_INFO level bootstrap that
    `gain.logging` performs on import. Only that bootstrap module and the
    `logging` shim itself may reach for the stdlib module by name (#373).
    """
    allowed = {
        os.path.join(GAIN_SRC, "logging.py"),
        os.path.join(GAIN_SRC, "utils", "log_levels.py"),
    }
    offenders = []
    for py in pathlib.Path(GAIN_SRC).rglob("*.py"):
        if str(py) in allowed:
            continue
        for line in py.read_text(encoding="utf8").splitlines():
            stripped = line.strip()
            if stripped == "import logging" \
                    or stripped.startswith(
                        ("import logging as", "import logging.")):
                offenders.append(str(py))
                break
    assert offenders == [], (
        "these gain modules use stdlib logging instead of "
        f"`from gain import logging`: {offenders}"
    )


def test_the_grr_does_not_import_the_annotation_layer(
) -> None:
    """``genomic_resources`` sits below ``annotation`` and stays there.

    The annotation config depends on the GRR -- the resource query language
    lives in ``genomic_resources.resource_query`` precisely so that the
    pipeline config, the repositories and the CLIs cannot disagree about
    what ``*`` means (gain#441).  An import back the other way would close
    that into a cycle and put the query language above the repositories it
    filters.

    Two upward imports predate the rule and are allowed by name rather than
    by pattern, so that a NEW one fails here instead of quietly joining
    them:

    * ``implementations/annotation_pipeline_impl`` implements the
      ``annotation_pipeline`` resource *type* -- the resource it describes
      is an annotation pipeline, so it cannot be described without the
      annotation layer.
    * ``cli_cache_repo`` is a CLI that composes the two layers rather than
      a part of either.

    Neither is a repository, a protocol, or the query language, which are
    the modules the layering is actually about.
    """
    grr_pkg = pathlib.Path(GAIN_SRC) / "genomic_resources"
    offenders = _imports_of_layer(grr_pkg, "gain.annotation", allowed={
        grr_pkg / "implementations" / "annotation_pipeline_impl.py",
        grr_pkg / "cli_cache_repo.py",
    })
    assert offenders == [], (
        f"the GRR imports the annotation layer: {offenders}. "
        f"genomic_resources sits below annotation -- move the shared code "
        f"down into genomic_resources instead, as resource_query does"
    )


def test_binning_does_not_import_the_annotation_layer() -> None:
    """``binning`` is a peer of ``annotation``, not a client of it.

    Both sit above ``genomic_resources`` and ``task_graph``; ``binning_tool``
    reads scores and writes a matrix, and nothing in it is an annotation.
    It used to import its work-dir convention and its two
    parsed-arguments-to-GRR steps from ``annotation.annotate_utils`` -- a
    statement that binning depends on annotation, which it does not -- until
    gain#1234 moved that shared code down to ``task_graph.work_dir`` and
    ``genomic_resources.genomic_context``, where a peer can reach it.  The
    direction is enforced here so it cannot quietly regress.
    """
    offenders = _imports_of_layer(
        pathlib.Path(GAIN_SRC) / "binning", "gain.annotation")
    assert offenders == [], (
        f"binning imports the annotation layer: {offenders}. "
        f"binning is a peer of annotation -- move the shared code down into "
        f"task_graph or genomic_resources instead"
    )


def _imports_of_layer(
    pkg: pathlib.Path, layer: str, *,
    allowed: Container[pathlib.Path] = (),
) -> list[str]:
    """``<file>: <module>`` for every import of ``layer`` under ``pkg``.

    The sweep the layering fences share: every module of the package
    ``pkg``, except the files in ``allowed``, must import nothing from the
    package ``layer`` or below it.  Resolution is :func:`_imported_modules`.
    """
    return [
        f"{py.relative_to(GAIN_SRC)}: {imported}"
        for py in sorted(pkg.rglob("*.py"))
        if py not in allowed
        for imported in sorted(_imported_modules(py))
        if imported == layer or imported.startswith(layer + ".")
    ]


def test_the_statistics_scan_does_not_import_the_implementation_classes(
) -> None:
    """``genomic_scores_impl.scan`` sits below the classes and stays there.

    The split in gain#1007 is only acyclic because the machinery stopped
    needing an implementation object: every one of its uses of
    ``build_score_implementation_from_resource`` was reaching for the
    ``.score`` that every class builds with ``build_score_from_resource``,
    so ``scan`` asks for that directly.  The base imports ``scan`` to
    schedule its task bodies; an import the other way would close the
    package into a cycle and put the task bodies back above the classes
    they were lifted out of.

    The classes are one module per kind since gain#1210 -- the base, the
    kinds, and the factory -- so the rule is the package prefix: ``scan``
    imports nothing from its own package, sibling or facade, and a kind
    added later is covered without coming here.

    Read from the AST rather than off the module object, because the
    import that would reintroduce the cycle is most likely a
    function-local one -- the shape ``cached_repository`` and
    ``repository_factory`` already use to reach this package -- and a
    module-attribute check cannot see it.
    """
    pkg = "gain.genomic_resources.implementations.genomic_scores_impl"
    scan_py = (pathlib.Path(GAIN_SRC) / "genomic_resources"
               / "implementations" / "genomic_scores_impl" / "scan.py")
    offenders = sorted(
        imported for imported in _imported_modules(scan_py)
        if imported == pkg or imported.startswith(f"{pkg}.")
    )
    assert offenders == [], (
        f"genomic_scores_impl.scan imports {offenders}, which closes the "
        f"package into a cycle. The machinery needs a GenomicScore, not an "
        f"implementation -- use build_score_from_resource, as the rest of "
        f"scan does"
    )


#: The deprecated alias for ``gain.annotation.annotate_tabular``, which
#: warns from its module body.  Named so the rule below has an anchor
#: that cannot pass on an empty scan.
ANNOTATE_COLUMNS_SHIM = "gain.annotation.annotate_columns"


@functools.cache
def _modules_warning_at_import() -> frozenset[str]:
    """Dotted names of the ``gain`` modules that warn on import.

    The subject set of the fence below, read out of the tree rather than
    listed, so a shim added later is covered by the rule that already
    exists.  gain#1153 wrote a fence naming ``score_annotator``: it
    protected that one module, for exactly as long as it existed, and
    gain#1154 deleted it again along with the shim.

    Cached, and the tree is fixed rather than a parameter, for the same
    reasons as :func:`_imported_modules`: two rules here ask for this
    set, and every rule sweeps the ``gain`` package.
    """
    found = set()
    for py in pathlib.Path(GAIN_SRC).rglob("*.py"):
        if not _warns_at_import(py.read_text(encoding="utf8")):
            continue
        parts = list(py.relative_to(GAIN_SRC).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts.pop()
        found.add(".".join(["gain", *parts]))
    return frozenset(found)


def _warns_at_import(source: str) -> bool:
    """Does importing a module with this source emit a DeprecationWarning?

    Only what importing *runs* counts.  A module body, a class body and
    any ``if``/``try``/``with``/``for`` nesting inside them all execute on
    import; a ``def`` body executes when it is called, which may be never.

    A module-level ``__getattr__`` is the one ``def`` that exception does
    not cover.  PEP 562 has the import system call it for any name the
    module does not itself define -- which is what ``from <module> import
    <name>`` does -- so a shim written that way warns every module that
    imports a name from it, at *that* module's import.  Reading only the
    module body would make the fence's coverage depend on which
    deprecation idiom the shim's author happened to pick.

    Matching that one name is the whole of the exception, not the first
    entry in a list that will grow: PEP 562 gives a module exactly two
    hooks, and the other, ``__dir__``, answers ``dir()`` rather than an
    attribute lookup, so no import reaches it.

    ``__getattr__`` bound by assignment rather than by ``def`` --
    ``__getattr__ = _make_shim()`` -- is out of reach of a static read
    and is not covered, like the aliased ``warn`` in :func:`_tail_name`.
    """
    tree = ast.parse(source)
    return _warns(_import_time_nodes(tree)) or any(
        _warns(_call_time_nodes(fn))
        for fn in _module_level_defs(tree)
        if fn.name == "__getattr__"
    )


def _warns(nodes: Iterator[ast.AST]) -> bool:
    """Does any of ``nodes`` warn a deprecation?"""
    return any(_is_deprecation_warn(node) for node in nodes)


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


def _module_level_defs(node: ast.AST) -> Iterator[ast.FunctionDef]:
    """Every ``def`` that running the module body binds as a global.

    Module-level nesting is followed, by the same reading
    :func:`_import_time_nodes` applies: a ``def`` under an ``if`` or a
    ``try`` runs and binds exactly like one at the top, and PEP 562 calls
    whatever ended up bound.  ``if not TYPE_CHECKING:`` is where a shim
    that wants to stay legible to a type checker puts it.

    A ``class`` and a ``def`` are not descended into.  What they contain
    binds an attribute or a local, never a module global -- which is what
    keeps a ``__getattr__`` method, and one nested in a function, out of
    the fence's subject set, structurally rather than by name.
    """
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.FunctionDef):
            yield child
        if not isinstance(child, _BINDS_ITS_OWN_SCOPE):
            yield from _module_level_defs(child)


#: Nodes whose contents bind somewhere other than the module's globals:
#: the deferred bodies, which bind their own locals whenever they do run,
#: plus the one node that is not one of them.  A class body *does* run at
#: import -- which is why it is absent above -- but what it binds is an
#: attribute of the class, never a module global.  Derived rather than
#: re-typed so the two cannot drift apart: a node kind added to the
#: deferred bodies later binds its own scope too.
_BINDS_ITS_OWN_SCOPE = (ast.ClassDef, *_DEFERRED_BODIES)


def _call_time_nodes(fn: ast.FunctionDef) -> Iterator[ast.AST]:
    """Every node under ``fn`` that *calling* it would execute.

    The body statements and whatever nesting runs with them, by the same
    rule the module body is read under: a ``def`` inside ``fn`` is
    deferred again, since calling ``fn`` only binds it.  One traversal
    rule, applied to the two places something can run from, rather than
    two rules that would have to be reconciled.
    """
    for stmt in fn.body:
        yield stmt
        yield from _import_time_nodes(stmt)


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
#: may do without becoming one.  Mirrored by ``web_api``'s copy of the
#: rule, which cannot import this one -- ``core/tests`` is not in that
#: project's CI image.
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
    # A module-level `__getattr__` is the one def body import can reach:
    # PEP 562 has the import system call it for any name the module does
    # not define, which is what `from <module> import <name>` does.
    (("import warnings\n"
      "def __getattr__(name):\n"
      "    warnings.warn('gone', DeprecationWarning, stacklevel=2)\n"), True),
    # The shape such a shim is actually written in: the warn guarded by
    # the name being one the shim moved.  Nesting inside the body runs
    # with it, exactly as module-level nesting runs on import.
    (("import warnings\n"
      "_MOVED = {'cli': 'gain.annotation.annotate_tabular'}\n"
      "def __getattr__(name):\n"
      "    if name in _MOVED:\n"
      "        warnings.warn('gone', DeprecationWarning, stacklevel=2)\n"
      "    raise AttributeError(name)\n"), True),
    # The category as the message reads the same inside `__getattr__`.
    (("import warnings\n"
      "def __getattr__(name):\n"
      "    warnings.warn(DeprecationWarning('gone'))\n"), True),
    # Module-level nesting binds the module global just as well, and PEP
    # 562 calls whatever is bound.  `if not TYPE_CHECKING:` is how such a
    # shim is written so a type checker does not have to make sense of
    # the dynamic name -- the likelier spelling of a real one, not the
    # rarer.  The read does not evaluate the condition, so the mirrored
    # `if TYPE_CHECKING:` counts too; that is the safe direction, since
    # being named here only asks the importer for the canonical module.
    (("import warnings\n"
      "from typing import TYPE_CHECKING\n"
      "if not TYPE_CHECKING:\n"
      "    def __getattr__(name):\n"
      "        warnings.warn('gone', DeprecationWarning, stacklevel=2)\n"),
     True),
    # The same one level down a `try`, where an import fallback puts it.
    (("import warnings\n"
      "try:\n"
      "    from gain.annotation.annotate_tabular import cli\n"
      "except ImportError:\n"
      "    def __getattr__(name):\n"
      "        warnings.warn('gone', DeprecationWarning)\n"), True),
    # A function body does not run on import.
    (("import warnings\n"
      "def f():\n"
      "    warnings.warn('gone', DeprecationWarning, stacklevel=2)\n"), False),
    # Nor a method body -- the shape every deprecated method in gain uses.
    (("import warnings\n"
      "class C:\n"
      "    def m(self):\n"
      "        warnings.warn('gone', DeprecationWarning)\n"), False),
    # `__getattr__` on a *class* is attribute access on an instance, not
    # on the module, and the import system never calls it.  Three classes
    # in `gain` define one -- the annotator decorator's, the fsspec
    # handle's and the faulty-filesystem test double's.  All three only
    # delegate, so matching the name anywhere in the tree would not widen
    # the derived set today; it would make all three subjects the moment
    # one of them warned.  What pins it now is this row: such a match
    # judges this source `True`, and the rule says `False`.
    (("import warnings\n"
      "class C:\n"
      "    def __getattr__(self, name):\n"
      "        warnings.warn('gone', DeprecationWarning)\n"), False),
    # PEP 562 looks up `__getattr__` and calls it; it does not await one.
    # An `async def` returns a coroutine that warns only when awaited,
    # and nothing awaits an attribute lookup.
    (("import warnings\n"
      "async def __getattr__(name):\n"
      "    warnings.warn('gone', DeprecationWarning)\n"), False),
    # Only a *module-level* `__getattr__` is the module's.  One nested in
    # a function is an ordinary local def that import never binds.
    (("import warnings\n"
      "def make():\n"
      "    def __getattr__(name):\n"
      "        warnings.warn('gone', DeprecationWarning)\n"
      "    return __getattr__\n"), False),
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


@pytest.mark.parametrize(("source", "at_import"), WARNS_AT_IMPORT_CASES)
def test_what_counts_as_a_warning_at_import(
    source: str, *, at_import: bool,
) -> None:
    """The rule the derived set applies, stated on its own.

    Table-driven rather than by planting modules: extraction and
    judgement are separate jobs, and a bug in the judgement is invisible
    in an empty offender list.

    The ``def``-body rows are the ones this pins.  A warn inside a
    function fires when it is *called*, which may be never -- and
    ``gain`` deprecates methods exactly that way: ``GeneScore._to_dict``,
    ``GenomicScore``'s two fetch aliases and the allele score's segment
    alias all warn from a method body, on modules half the package
    imports.  Reading the tree with ``ast.walk`` instead of descending
    only what import executes puts every one of them in the derived set,
    and the fence then fails on the modules it was never about.

    A module-level ``__getattr__`` is the one exception to that, so the
    rows around it carve the exception back down: a ``__getattr__`` on a
    class, an ``async def`` one and one nested in a function are all
    ``False``, because the import system calls only what the module body
    bound.  Without them the exception swallows the rule it belongs to.
    """
    assert _warns_at_import(source) is at_import


def test_the_derived_set_finds_the_shim_it_is_anchored_to() -> None:
    """The derived set must not be empty, and must name a known shim.

    An empty offender list satisfies the fence below just as well when
    the derived set is empty -- a scan that found no candidates polices
    nothing and says so in green.  Anchored on ``annotate_columns``,
    which warns from its module body and is registered as a console
    script, so it is not going away quietly.

    Stated as ``contains`` rather than ``equals`` so that a shim added
    later needs no edit here.
    """
    derived = _modules_warning_at_import()

    assert ANNOTATE_COLUMNS_SHIM in derived, (
        f"the anchor shim is not in the derived set: {sorted(derived)}. "
        f"If it was removed, anchor this on another module-level "
        f"DeprecationWarning -- an unanchored rule passes on an empty scan"
    )


def test_nothing_in_gain_imports_a_module_that_warns_at_import() -> None:
    """No ``gain`` module imports one whose import warns.

    A deprecated alias kept for outside callers -- ``annotate_columns``
    is kept for the CLI name -- warns from its module body, so
    *importing* it warns.  An in-tree caller makes every process that
    loads that module emit the warning, and pins the shim past the
    release that was meant to remove it.  Nothing goes red on its own:
    the import works, and neither project turns warnings into errors, so
    the shim's own test keeps passing while saying nothing about who
    imports it.

    This is not a rule against re-exports.  ``gain`` has permanent
    facades that callers are meant to import through -- the
    ``genomic_scores`` and ``genomic_scores_impl`` package ``__init__``
    modules both say so.  Neither warns on import, so neither is in the
    derived set, by construction rather than by exemption.

    Sweeps the ``gain`` package, which is the tree this rule is about and
    the one ``_imported_modules`` resolves names against.  A test that
    imports a shim is not swept: it warns in its own process and pins
    nothing that ships.  ``web_api`` is fenced by its own copy of the
    rule, for the reason its ``test_architecture.py`` docstring gives.
    """
    shims = _modules_warning_at_import()
    offenders = sorted(
        f"{py.relative_to(GAIN_SRC)}: {imported}"
        for py in pathlib.Path(GAIN_SRC).rglob("*.py")
        for imported in shims & _imported_modules(py)
    )
    assert offenders == [], (
        f"these modules import a module that warns at import: {offenders}. "
        f"Import the module the shim forwards to -- importing the shim "
        f"warns in every process that loads the importer, and keeps the "
        f"shim alive past its removal"
    )


def test_every_annotator_entry_point_names_the_module_that_defines_it(
) -> None:
    """No annotator is registered through a re-export.

    An entry point is an import the AST sweep above cannot see: a string
    in ``pyproject.toml``, resolved when the pipeline loads its
    annotators.  One that names a facade rather than the factory's home
    keeps the facade alive, and -- for a deprecating one -- warns on
    every pipeline load.  Stated for the whole group, so the next split
    does not need a fence of its own.
    """
    with open(os.path.join(GAIN_ROOT, "pyproject.toml"), "rb") as infile:
        project = tomllib.load(infile)
    assert project["project"]["name"] == "gain-core", \
        "a wrong path that still parsed would pin nothing"
    annotators = project["project"]["entry-points"][
        "gain.annotation.annotators"]
    offenders = {}
    for name, target in annotators.items():
        module_name, _, attr = target.partition(":")
        factory = getattr(importlib.import_module(module_name), attr, None)
        if factory is None or factory.__module__ != module_name:
            offenders[name] = target
    assert not offenders, (
        f"these annotator entry points name a module other than the one "
        f"that defines their factory: {offenders}"
    )
    assert {
        "allele_score", "allele_score_annotator",
        "position_score", "position_score_annotator",
    } <= set(annotators), "the score annotators are no longer registered"


#: Names that reach markdown2's un-rescued output.  The second is the
#: wrapper module's own re-export: it renders *through* the library, so it
#: binds the raw function under the bare name ``markdown``, and importing
#: that is one word away from importing the wrapper.  Since gain#751 the
#: template package itself imports the wrapper, so that slip would leave
#: every template in the stack rendering un-rescued.
#:
#: Note that ``_imported_names`` reports every string constant in a module
#: as well, so that dynamic imports are seen.  A ``gain`` module naming
#: the second entry in a docstring or a constant is therefore reported as
#: an offender; spell it in two pieces if one ever needs to.
_UNWRAPPED = frozenset({
    "markdown2",
    "gain.templates.markdown_support.markdown",
})


def _renders_markdown_unwrapped(dotted: str) -> bool:
    """Does importing ``dotted`` reach markdown2 without the rescue?

    Compared on dotted segments rather than characters, so a package that
    merely starts with the same letters (``markdown2_extras``) is not
    swept up, while a submodule (``markdown2.markdown``) is.
    """
    return any(
        dotted == name or dotted.startswith(f"{name}.")
        for name in _UNWRAPPED
    )


@pytest.mark.parametrize(("dotted", "unwrapped"), [
    ("markdown2", True),
    ("markdown2.markdown", True),
    # The wrapper's own re-export -- see _UNWRAPPED.
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

    Table-driven rather than by planting modules: extraction and judgement
    are separate jobs, and a bug in the judgement is invisible in an empty
    offender list.
    """
    assert _renders_markdown_unwrapped(dotted) is unwrapped


@pytest.mark.parametrize("source", [
    "import markdown2",
    "import markdown2 as md",
    "from markdown2 import markdown",
    "from markdown2 import markdown as _m",
    "from markdown2.extras import thing",
    "import importlib\nx = importlib.import_module('markdown2')",
    "from gain.templates.markdown_support import markdown",
    "from gain.templates.markdown_support import markdown as render_markdown",
])
def test_the_sweep_sees_every_spelling_that_reaches_the_library(
    source: str,
) -> None:
    """The other half of the fence: extraction, not judgement.

    A correct predicate over names the extractor never produces polices
    nothing, and says so with an empty offender list -- green, and
    vacuous.  The last two rows are the ones this pins: the wrapper's
    re-export is caught only because the extractor emits the imported
    *name* alongside its module, and renaming it on the way in must not
    launder it.
    """
    imported = _imported_names(source, package=["gain", "templates"])

    assert any(_renders_markdown_unwrapped(name) for name in imported), (
        f"not caught: {source!r} -> {sorted(imported)}")


def test_the_wrapper_module_itself_is_what_the_sweep_exempts() -> None:
    """The allow-list entry must name a file that is really there.

    ``allowed`` is compared by path, so a moved or renamed wrapper would
    silently stop being exempt -- and the fence would then fail on the one
    module that is *supposed* to import the library, sending the next
    reader after the wrong thing.
    """
    wrapper = pathlib.Path(GAIN_SRC) / "templates" / "markdown_support.py"

    assert wrapper.is_file()
    assert any(
        _renders_markdown_unwrapped(name)
        for name in _imported_modules(wrapper)
    ), "the wrapper no longer imports markdown2 -- has the rescue moved?"


def test_markdown_rendering_goes_through_the_one_wrapper_module() -> None:
    """Every gain-core module renders Markdown through ``render_markdown``.

    ``gain.templates.markdown_support`` post-processes markdown2's output
    so documentation prose survives a bogus tag -- ``values <thresh are
    dropped`` reaches the reader whole instead of being swallowed by the
    browser (gain#736).  A module importing ``markdown2`` directly
    re-opens that defect at its own sink; within ``core/gain`` -- the
    fence this test can see -- only the wrapper module itself may touch
    the library.

    ``web_api`` renders the same template from its own project and is not
    swept here.  It is fenced by its own copy of this rule, in
    ``web_api/web_annotation/tests/test_architecture.py`` (gain#742):
    widening the sweep below to reach it would match no files in the
    ``core`` CI image, which copies only ``core/`` -- collecting no
    offenders and passing while policing nothing.

    What the rule forbids is *reaching markdown2's un-rescued output*,
    which is wider than "imports markdown2" -- see ``_UNWRAPPED``.
    """
    allowed = {
        pathlib.Path(GAIN_SRC) / "templates" / "markdown_support.py",
    }
    offenders = [
        f"{py.relative_to(GAIN_SRC)}: {imported}"
        for py in pathlib.Path(GAIN_SRC).rglob("*.py")
        if py not in allowed
        for imported in sorted(_imported_modules(py))
        if _renders_markdown_unwrapped(imported)
    ]
    assert offenders == [], (
        f"these gain modules reach markdown2's un-rescued output instead "
        f"of `from gain.templates.markdown_support import render_markdown`: "
        f"{offenders}. Un-rescued output leaves prose like "
        f"'values <thresh are dropped' to be eaten as a bogus tag (#736)"
    )


#: The pipeline documentation template, spelled out rather than imported
#: from the module under test.  A fence that scans for a name its own
#: subject supplies goes blind the moment the subject renames it: the scan
#: would stop matching an unconverted copy still binding the old name, and
#: pass.  ``test_the_fence_names_the_template_the_renderer_binds`` keeps
#: this literal honest.
DOC_TEMPLATE = "annotate_doc_pipeline_template.jinja"


def test_the_fence_names_the_template_the_renderer_binds() -> None:
    """The literal above must be the name the renderer actually asks for.

    Without this, renaming the template would leave the fence scanning for
    a string nothing spells any more -- collecting no offenders, and
    passing while policing nothing.
    """
    assert pipeline_doc.DOC_TEMPLATE_NAME == DOC_TEMPLATE


def test_the_pipeline_doc_template_is_bound_in_one_module() -> None:
    """One module renders the pipeline documentation page (#952).

    The page had three renderers, each binding this template and building
    its own resource/histogram address pair.  They drifted: ``d8624b787``
    moved the CLI's addresses onto the GRR's public mirror and left the
    web API's on the repository's own url, where they stayed for two
    months (#841).  Since #952 ``gain.annotation.pipeline_doc`` is the one
    binder, and the three callers differ only in the address policy they
    pass it.

    Two limits, so this is not read as stronger than it is.  It matches
    the *literal* name, so a second binder spelled
    ``get_template(pipeline_doc.DOC_TEMPLATE_NAME)`` would slip past --
    though that spelling still routes through the one module, so it is
    not the drift being guarded against.  And it sweeps ``core/gain``
    only: the copy that caused #841 lived in ``web_api``, which the
    ``core`` CI image does not contain.  That half is fenced by
    ``web_api/web_annotation/tests/test_architecture.py``.
    """
    allowed = {
        pathlib.Path(GAIN_SRC) / "annotation" / "pipeline_doc.py",
    }
    offenders = sorted(
        str(py.relative_to(GAIN_SRC))
        for py in pathlib.Path(GAIN_SRC).rglob("*.py")
        if py not in allowed and DOC_TEMPLATE in py.read_text()
    )
    assert offenders == [], (
        f"these gain modules name {DOC_TEMPLATE} instead of calling "
        f"`from gain.annotation.pipeline_doc import render_pipeline_doc`: "
        f"{offenders}. A second binder is how the CLI's and the web API's "
        f"copies of the address policy drifted apart (#841, #952)"
    )
    assert allowed == {
        py for py in allowed if py.is_file()
    }, f"the one permitted binder is not where the fence expects it: {allowed}"


@functools.cache
def _imported_modules(py: pathlib.Path) -> set[str]:
    """Absolute dotted names ``py`` imports, however it spells them.

    Cached per file: the package-wide sweep and the narrower ones
    overlap -- every file under ``genomic_resources`` is read by two
    rules, ``scan.py`` by three -- and the sources do not change within
    a test run.

    Resolved from the AST rather than matched against the source text, so
    that ``from gain import annotation``, a relative ``from ..annotation
    import x`` and an ``importlib.import_module("gain.annotation.x")`` are
    all seen -- a text scan for ``from gain.annotation`` catches none of
    the three, and matches a line inside a docstring that imports nothing.

    Every rule here sweeps the ``gain`` package, so the containing tree
    is fixed rather than a parameter.
    """
    # The package that contains this module, as a dotted path: `gain` plus
    # the directories between GAIN_SRC and the file.
    package = ["gain", *py.relative_to(GAIN_SRC).parts[:-1]]
    return _imported_names(py.read_text(encoding="utf8"), package)


def _imported_names(source: str, package: list[str]) -> set[str]:
    """``_imported_modules`` over a source string in a known package.

    Split out so the extraction can be exercised on a literal source
    rather than on a file planted inside the very tree the sweeps walk:
    this suite runs under ``pytest -n 10``, where such a file would race
    the sweeps and fail them from another worker.
    """
    tree = ast.parse(source)

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                # `from . import x` stays in the containing package;
                # each extra dot climbs one above it.
                base = package[:len(package) - (node.level - 1)]
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
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            # `importlib.import_module("gain.annotation.x")` and friends.
            imported.add(node.value)
    return imported
