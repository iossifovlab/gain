# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""Architecture tests for gain package using pytestarch."""
import ast
import functools
import os
import pathlib

import pytest
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


def test_the_table_layer_imports_nothing_from_the_score_layer(
    gain_arch: EvaluableArchitecture,
) -> None:
    """``genomic_position_table`` must not depend on the score modules.

    The seam the record contract draws: a backend yields records and owns its
    payload's shape; the score layer says what those records MEAN.  Every
    backend-specific score module -- ``vcf_scores``, ``bigwig_scores`` -- sits
    on the score side of it and imports the table's constants, never the
    reverse.  An import back the other way would make the payload's shape and
    its interpretation mutually dependent, which is exactly what the two
    modules exist to keep apart.

    Written as a module-name scan rather than through pytestarch's rule DSL so
    that the failure names the offending module and the import it made.
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
    """
    allowed = {
        pathlib.Path(GAIN_SRC) / "templates" / "markdown_support.py",
    }
    offenders = [
        f"{py.relative_to(GAIN_SRC)}: {imported}"
        for py in pathlib.Path(GAIN_SRC).rglob("*.py")
        if py not in allowed
        for imported in sorted(_imported_modules(py))
        if imported == "markdown2" or imported.startswith("markdown2.")
    ]
    assert offenders == [], (
        f"these gain modules import markdown2 directly instead of "
        f"`from gain.templates.markdown_support import render_markdown`: "
        f"{offenders}. Direct markdown2 output leaves prose like "
        f"'values <thresh are dropped' to be eaten as a bogus tag (#736)"
    )


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
    tree = ast.parse(py.read_text(encoding="utf8"))
    # The package that contains this module, as a dotted path: `gain` plus
    # the directories between GAIN_SRC and the file.
    package = ["gain", *py.relative_to(GAIN_SRC).parts[:-1]]

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
