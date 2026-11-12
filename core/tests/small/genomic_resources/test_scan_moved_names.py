"""The names that left ``scan`` still import from it, with a warning.

The min/max pass, the histogram pass and the one-task statistics build
moved out of ``genomic_scores_impl.scan`` to sibling modules (gain#2269).
Each public name stays importable from the old module for one release
and warns there. Private names get no alias.
"""
import ast
import importlib
import pathlib
import warnings

import gain
import pytest
from gain.genomic_resources.implementations.genomic_scores_impl import scan

PKG = "gain.genomic_resources.implementations.genomic_scores_impl"
MIN_MAX = f"{PKG}.scan_min_max"
HISTOGRAMS = f"{PKG}.scan_histograms"
NOREGION = f"{PKG}.scan_noregion"

ALIASED = [
    *((name, MIN_MAX) for name in (
        "do_min_max",
        "do_min_max_bulk",
        "do_min_max_task",
        "merge_min_max",
    )),
    *((name, HISTOGRAMS) for name in (
        "do_histogram",
        "do_histogram_bulk",
        "do_histogram_task",
        "merge_and_save_histograms",
        "merge_histograms",
        "update_hist_confs",
    )),
    ("do_noregion_histograms", NOREGION),
]

#: Every name that moved, the private ones included, with its new module.
#: No gain code imports any of them from the old module.
MOVED = {
    **dict(ALIASED),
    "_accumulate_min_max": MIN_MAX,
    "_add_record_values": HISTOGRAMS,
    "_build_nullified_histograms": HISTOGRAMS,
    "_save_histograms": HISTOGRAMS,
}


def test_the_alias_map_names_exactly_the_public_moved_names() -> None:
    aliases = {
        name: f"{scan.__package__}.{module}"
        for name, module in scan._MOVED.items()}

    assert aliases == dict(ALIASED)


@pytest.mark.parametrize(("name", "new_module"), sorted(MOVED.items()))
def test_each_moved_name_is_defined_in_its_new_module(
    name: str, new_module: str,
) -> None:
    source = importlib.import_module(new_module).__file__
    assert source is not None
    tree = ast.parse(pathlib.Path(source).read_text(encoding="utf-8"))

    defined = {
        node.name for node in tree.body
        if isinstance(node, ast.FunctionDef | ast.ClassDef)
    }

    assert name in defined


@pytest.mark.parametrize(("name", "new_module"), ALIASED)
def test_each_new_module_publishes_its_public_moved_names(
    name: str, new_module: str,
) -> None:
    published = importlib.import_module(new_module).__all__

    assert name in published
    assert name not in scan.__all__


@pytest.mark.parametrize(("name", "new_module"), ALIASED)
def test_old_path_gives_the_moved_object_and_warns(
    name: str, new_module: str,
) -> None:
    moved = getattr(importlib.import_module(new_module), name)

    with pytest.warns(DeprecationWarning, match=new_module) as record:
        aliased = getattr(scan, name)

    assert aliased is moved
    assert [str(warning.message) for warning in record] == [
        f"{name} moved to {new_module}"]


def test_from_import_of_a_moved_name_warns() -> None:
    histograms = importlib.import_module(HISTOGRAMS)

    with pytest.warns(DeprecationWarning, match=f"moved to {HISTOGRAMS}"):
        # The import statement is the seam that a caller outside gain uses.
        # mypy does not see the names that the alias serves.
        from gain.genomic_resources.implementations.genomic_scores_impl.scan import (  # type: ignore[attr-defined]  # ruff: ignore[line-too-long]
            do_histogram_task,
        )

    assert do_histogram_task is histograms.do_histogram_task


@pytest.mark.parametrize(
    "name", ["bulk_region_scan", "unpack_score_defs", "_SCAN_BATCH_SIZE"])
def test_a_name_that_stayed_does_not_warn(name: str) -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        value = getattr(scan, name)

    assert value is not None


@pytest.mark.parametrize(
    "name", sorted(name for name in MOVED if name.startswith("_")))
def test_a_private_moved_name_is_gone_from_the_old_module(name: str) -> None:
    with pytest.raises(AttributeError):
        getattr(scan, name)


def test_an_unknown_name_raises_attribute_error() -> None:
    unknown = "no_such_name"

    with pytest.raises(AttributeError):
        getattr(scan, unknown)


#: The workspace members of gain, relative to the workspace root.
MEMBERS = [
    "core",
    "demo_annotator",
    "spliceai_annotator",
    "vep_annotator",
    "web_api",
]


def _from_module(node: ast.ImportFrom, own_package: list[str]) -> str:
    """Resolve the module of a from-import against the file's package."""
    module = node.module or ""
    if not node.level:
        return module
    base = own_package[:len(own_package) - node.level + 1]
    return ".".join([*base, module] if module else base)


def _module_aliases(tree: ast.Module, own_package: list[str]) -> set[str]:
    """List the local names that a file binds to the old module."""
    old_package, _, old_name = scan.__name__.rpartition(".")
    aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            aliases.update(
                alias.asname for alias in node.names
                if alias.name == scan.__name__ and alias.asname)
        elif (isinstance(node, ast.ImportFrom)
              and _from_module(node, own_package) == old_package):
            aliases.update(
                alias.asname or alias.name for alias in node.names
                if alias.name == old_name)
    return aliases


def _dotted(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        head = _dotted(node.value)
        return None if head is None else f"{head}.{node.attr}"
    return None


#: The calls that name an attribute of their first argument as a string.
_SETATTR_CALLS = {"setattr", "delattr", "object"}


def _string_target(node: ast.AST, aliases: set[str]) -> str | None:
    """Return the moved name that a string-named patch target names.

    A target is ``setattr(scan, "<name>", ...)``,
    ``delattr(scan, "<name>")`` or ``patch.object(scan, "<name>")``, with
    ``scan`` bound to the old module, or a string
    ``"<old module>.<name>"``.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        prefix = f"{scan.__name__}."
        if not node.value.startswith(prefix):
            return None
        head = node.value.removeprefix(prefix).partition(".")[0]
        return head if head in MOVED else None
    if not isinstance(node, ast.Call) or len(node.args) < 2:
        return None
    func = node.func
    called = func.attr if isinstance(func, ast.Attribute) else (
        func.id if isinstance(func, ast.Name) else None)
    owner, name = node.args[0], node.args[1]
    if (called in _SETATTR_CALLS
            and _dotted(owner) in {*aliases, scan.__name__}
            and isinstance(name, ast.Constant)
            and name.value in MOVED):
        return str(name.value)
    return None


def moved_name_imports(root: pathlib.Path, package: str = "") -> list[str]:
    """List each use of a moved name through the old module under ``root``.

    A use is an import of the name from the old module, a read of the
    name as an attribute of the old module, or a patch target that names
    the name on the old module as a string. ``package`` is the dotted name
    of ``root``, empty when ``root`` holds top-level packages. A relative
    import resolves against the package of its file. Each entry is
    ``<file>: <name>``.
    """
    offenders: list[str] = []
    for py in sorted(root.rglob("*.py")):
        rel = py.relative_to(root)
        own_package = [
            *([package] if package else []), *rel.with_suffix("").parts,
        ][:-1]
        tree = ast.parse(py.read_text(encoding="utf-8"))
        aliases = _module_aliases(tree, own_package)
        for node in ast.walk(tree):
            target = _string_target(node, aliases)
            if target is not None:
                offenders.append(f"{rel.as_posix()}: {target}")
                continue
            if isinstance(node, ast.Attribute):
                owner = _dotted(node.value)
                if node.attr in MOVED and (
                        owner in aliases or owner == scan.__name__):
                    offenders.append(f"{rel.as_posix()}: {node.attr}")
                continue
            if not isinstance(node, ast.ImportFrom):
                continue
            if _from_module(node, own_package) != scan.__name__:
                continue
            offenders.extend(
                f"{rel.as_posix()}: {alias.name}"
                for alias in node.names if alias.name in MOVED)
    return offenders


def _plant(root: pathlib.Path, name: str, text: str) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_the_fence_finds_absolute_and_relative_imports(
    tmp_path: pathlib.Path,
) -> None:
    impl = "genomic_resources/implementations/genomic_scores_impl"
    _plant(tmp_path, f"{impl}/absolute.py",
           f"from {PKG}.scan import do_min_max\n")
    _plant(tmp_path, f"{impl}/relative.py",
           "from .scan import _save_histograms, scan_region\n")
    _plant(tmp_path, f"{impl}/new_path.py",
           "from .scan_min_max import do_min_max\n")

    offenders = moved_name_imports(tmp_path, "gain")

    assert offenders == [
        f"{impl}/absolute.py: do_min_max",
        f"{impl}/relative.py: _save_histograms",
    ]


def test_the_fence_finds_attribute_reads_of_the_old_module(
    tmp_path: pathlib.Path,
) -> None:
    _plant(tmp_path, "tests/from_package.py",
           f"from {PKG} import scan\n"
           "scan.do_histogram_bulk\n"
           "scan.bulk_region_scan\n")
    _plant(tmp_path, "tests/renamed.py",
           f"import {PKG}.scan as stats_scan\n"
           "stats_scan.merge_histograms\n")
    _plant(tmp_path, "tests/dotted.py",
           f"import {PKG}.scan\n"
           f"{PKG}.scan.do_noregion_histograms\n")
    _plant(tmp_path, "tests/other_module.py",
           f"from {PKG} import scan_histograms\n"
           "scan_histograms.do_histogram_bulk\n")

    offenders = moved_name_imports(tmp_path)

    assert offenders == [
        "tests/dotted.py: do_noregion_histograms",
        "tests/from_package.py: do_histogram_bulk",
        "tests/renamed.py: merge_histograms",
    ]


def test_the_fence_finds_string_named_patch_targets(
    tmp_path: pathlib.Path,
) -> None:
    _plant(tmp_path, "tests/test_setattr.py",
           f"from {PKG} import scan\n"
           "def test(monkeypatch):\n"
           "    monkeypatch.setattr(scan, 'do_histogram_bulk', None)\n"
           "    monkeypatch.setattr(scan, '_SCAN_BATCH_SIZE', 1)\n")
    _plant(tmp_path, "tests/test_patch_object.py",
           f"import {PKG}.scan as stats_scan\n"
           "def test(mocker):\n"
           "    mocker.patch.object(stats_scan, 'do_min_max_task')\n")
    _plant(tmp_path, "tests/test_patch_string.py",
           "from unittest import mock\n"
           f"mock.patch('{PKG}.scan.do_histogram_task')\n"
           f"mock.patch('{PKG}.scan.scan_region')\n"
           f"mock.patch('{PKG}.scan_histograms.do_histogram_task')\n")

    offenders = moved_name_imports(tmp_path)

    assert offenders == [
        "tests/test_patch_object.py: do_min_max_task",
        "tests/test_patch_string.py: do_histogram_task",
        "tests/test_setattr.py: do_histogram_bulk",
    ]


def gain_workspace(gain_file: pathlib.Path) -> pathlib.Path | None:
    """Return the workspace root of a source checkout of gain.

    Return ``None`` when gain is an installed package, for example in the
    conda integration test bed. The workspace members are then absent.
    """
    workspace = gain_file.parent.parent.parent
    if (workspace / "core" / "gain").resolve() != gain_file.parent.resolve():
        return None
    return workspace


@pytest.mark.parametrize("member", MEMBERS)
def test_nothing_in_gain_imports_a_moved_name_from_the_old_module(
    member: str,
) -> None:
    workspace = gain_workspace(pathlib.Path(gain.__file__))
    if workspace is None:
        pytest.skip("gain is installed, not a source checkout")
    root = workspace / member
    assert root.is_dir(), f"no workspace member {member}"

    # This file imports and reads through the alias on purpose.
    this_file = pathlib.Path(__file__).resolve().relative_to(workspace)
    offenders = [
        offender for offender in moved_name_imports(root)
        if not f"{member}/{offender}".startswith(
            f"{this_file.as_posix()}: ")]

    assert offenders == [], (
        f"import these names from their new modules: {offenders}")
