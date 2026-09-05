# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""Architecture tests for gain package using pytestarch."""
import ast
import functools
import os
import pathlib

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
    allowed = {
        grr_pkg / "implementations" / "annotation_pipeline_impl.py",
        grr_pkg / "cli_cache_repo.py",
    }
    offenders = []
    for py in grr_pkg.rglob("*.py"):
        if py in allowed:
            continue
        offenders.extend(
            f"{py.relative_to(GAIN_SRC)}: {imported}"
            for imported in sorted(_imported_modules(py))
            if imported == "gain.annotation"
            or imported.startswith("gain.annotation.")
        )
    assert offenders == [], (
        f"the GRR imports the annotation layer: {offenders}. "
        f"genomic_resources sits below annotation -- move the shared code "
        f"down into genomic_resources instead, as resource_query does"
    )


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
    kinds, and the factory -- so the rule is read off the package
    directory: ``scan`` imports nothing from any sibling module, nor the
    facade, and a kind added later is covered without coming here.

    Read from the AST rather than off the module object, because the
    import that would reintroduce the cycle is most likely a
    function-local one -- the shape ``cached_repository`` and
    ``repository_factory`` already use to reach this package -- and a
    module-attribute check cannot see it.
    """
    pkg = "gain.genomic_resources.implementations.genomic_scores_impl"
    scan_py = (pathlib.Path(GAIN_SRC) / "genomic_resources"
               / "implementations" / "genomic_scores_impl" / "scan.py")
    siblings = {
        path.stem for path in scan_py.parent.glob("*.py")
    } - {"scan", "__init__"}
    assert siblings >= {"base", "allele", "fragment", "builders"}
    forbidden = {pkg, *(f"{pkg}.{sibling}" for sibling in siblings)}
    offenders = sorted(
        imported for imported in _imported_modules(scan_py)
        if imported in forbidden
    )
    assert offenders == [], (
        f"genomic_scores_impl.scan imports {offenders}, which closes the "
        f"package into a cycle. The machinery needs a GenomicScore, not an "
        f"implementation -- use build_score_from_resource, as the rest of "
        f"scan does"
    )


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

    Cached per file: several rules here sweep the whole package, and the
    sources do not change within a test run.

    Resolved from the AST rather than matched against the source text, so
    that ``from gain import annotation``, a relative ``from ..annotation
    import x`` and an ``importlib.import_module("gain.annotation.x")`` are
    all seen -- a text scan for ``from gain.annotation`` catches none of
    the three, and matches a line inside a docstring that imports nothing.
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
