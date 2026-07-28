# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""Architecture tests for gain package using pytestarch."""
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
