"""The names that left ``cli`` still import from it, with a warning.

The shared helpers, the manifest and index commands, the statistics
commands, the info page commands and ``grr_browse`` moved to sibling
modules (gain#2268). Each public name stays importable from
``gain.genomic_resources.cli`` for one release and warns there. Private
names get no alias. ``cli_browse`` stays importable from ``cli`` without
a warning, because the ``grr_browse`` console script names it there.
"""
import ast
import importlib
import pathlib
import warnings

import gain
import pytest
from gain.genomic_resources import cli

COMMON = "gain.genomic_resources.cli_common"
MANIFEST = "gain.genomic_resources.cli_manifest"
STATS = "gain.genomic_resources.cli_stats"
INFO = "gain.genomic_resources.cli_info"
BROWSE = "gain.genomic_resources.cli_browse"

ALIASED = [
    ("CommandResult", COMMON),
    ("ManifestOutcome", MANIFEST),
]

#: Every name that moved, the private ones included, with its new module.
#: No gain code imports any of them from the old module.
MOVED = {
    **dict(ALIASED),
    **dict.fromkeys((
        "_add_dry_run_and_force_parameters_group",
        "_add_dvc_parameters_group",
        "_add_hist_parameters_group",
        "_add_repository_resource_parameters_group",
        "_create_proto",
        "_find_resources",
        "_non_negative_int",
        "_note_stale_repository_index",
    ), COMMON),
    **dict.fromkeys((
        "_INCOMPLETE_INDEX_SUFFIX",
        "_IndexInfo",
        "_build_content_file",
        "_build_contents_db_image",
        "_collect_index_infos",
        "_configure_repo_index_subparser",
        "_configure_repo_manifest_subparser",
        "_configure_resource_manifest_subparser",
        "_contents_db_is_current",
        "_create_contents_db",
        "_do_resource_manifest_command",
        "_index_contents_md5",
        "_merge_index_headers",
        "_publish_repository_contents",
        "_run_manifest_core",
        "_run_repo_index_command",
        "_run_repo_manifest_command",
        "_run_repo_manifest_command_internal",
        "_run_resource_manifest_command",
    ), MANIFEST),
    **dict.fromkeys((
        "_StatsSelection",
        "_build_selected_stats",
        "_collect_impl_stats_tasks",
        "_count_dry_run_stats",
        "_dry_run_stats_result",
        "_read_stats_hash",
        "_report_schema_stale_statistics",
        "_run_repo_stats_command",
        "_run_resource_stats_command",
        "_run_stats_core",
        "_run_stats_graph",
        "_select_resource_stats",
        "_select_stats",
        "_start_resource_stats",
        "_statistics_not_built",
        "_stats_hash_path",
        "_stats_need_rebuild",
        "_store_stats_hash",
        "_warn_schema_stale",
    ), STATS),
    **dict.fromkeys((
        "_do_resource_info_command",
        "_regenerate_resource_pages",
        "_run_repo_info_command",
        "_run_resource_info_command",
        "_write_resource_file_if_changed",
    ), INFO),
    "_build_browse_argument_parser": BROWSE,
}


def test_the_alias_map_names_exactly_the_public_moved_names() -> None:
    assert dict(ALIASED) == cli._MOVED


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
    } | {
        target.id for node in tree.body if isinstance(node, ast.Assign)
        for target in node.targets if isinstance(target, ast.Name)
    }

    assert name in defined


@pytest.mark.parametrize(("name", "new_module"), ALIASED)
def test_old_path_gives_the_moved_object_and_warns(
    name: str, new_module: str,
) -> None:
    moved = getattr(importlib.import_module(new_module), name)

    with pytest.warns(DeprecationWarning, match=new_module) as record:
        aliased = getattr(cli, name)

    assert aliased is moved
    assert [str(warning.message) for warning in record] == [
        f"{name} moved to {new_module}"]


def test_from_import_of_a_name_the_old_module_uses_warns() -> None:
    common = importlib.import_module(COMMON)

    with pytest.warns(DeprecationWarning, match=f"moved to {COMMON}"):
        # The import statement is the seam that a caller outside gain uses.
        from gain.genomic_resources.cli import CommandResult

    assert CommandResult is common.CommandResult


def test_from_import_of_a_name_the_old_module_does_not_use_warns() -> None:
    manifest = importlib.import_module(MANIFEST)

    with pytest.warns(DeprecationWarning, match=f"moved to {MANIFEST}"):
        # mypy does not see the names that the alias serves.
        from gain.genomic_resources.cli import (  # type: ignore[attr-defined]
            ManifestOutcome,
        )

    assert ManifestOutcome is manifest.ManifestOutcome


@pytest.mark.parametrize("name", ["cli_manage", "VerbosityConfiguration"])
def test_a_name_that_gpf_imports_does_not_warn(name: str) -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        value = getattr(cli, name)

    assert callable(value)


def test_cli_browse_imports_from_the_old_path_without_a_warning() -> None:
    browse = importlib.import_module(BROWSE)

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        from gain.genomic_resources.cli import cli_browse

    assert cli_browse is browse.cli_browse


def test_an_unknown_name_raises_attribute_error() -> None:
    unknown = "no_such_name"

    with pytest.raises(AttributeError):
        getattr(cli, unknown)


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
    old_package, _, old_name = cli.__name__.rpartition(".")
    aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            aliases.update(
                alias.asname for alias in node.names
                if alias.name == cli.__name__ and alias.asname)
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


#: The string-named patch targets on the old module that are correct,
#: because only the dispatcher in ``cli.py`` calls the name, through the
#: binding that ``cli.py`` imports back.
DISPATCHER_TARGETS = {
    ("core/tests/small/genomic_resources/test_cli_repair.py: "
     "_run_repo_info_command"),
}

#: The calls that name an attribute of their first argument as a string.
_SETATTR_CALLS = {"setattr", "delattr", "object"}


def _string_target(node: ast.AST, aliases: set[str]) -> str | None:
    """Return the moved name that a string-named patch target names.

    A target is ``setattr(cli, "<name>", ...)``, ``delattr(cli, "<name>")``
    or ``patch.object(cli, "<name>")``, with ``cli`` bound to the old
    module, or a string ``"gain.genomic_resources.cli.<name>"``.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        prefix = f"{cli.__name__}."
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
            and _dotted(owner) in {*aliases, cli.__name__}
            and isinstance(name, ast.Constant)
            and name.value in MOVED):
        return str(name.value)
    return None


def moved_name_imports(root: pathlib.Path, package: str = "") -> list[str]:
    """List each use of a moved name through the old module under ``root``.

    A use is an import of the name from the old module, a read of the
    name as an attribute of the old module, or a patch target that names
    the name on the old module as a string. ``package`` is the dotted name of
    ``root``, empty when ``root`` holds top-level packages. A relative
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
                        owner in aliases
                        or owner == cli.__name__):
                    offenders.append(f"{rel.as_posix()}: {node.attr}")
                continue
            if not isinstance(node, ast.ImportFrom):
                continue
            if _from_module(node, own_package) != cli.__name__:
                continue
            offenders.extend(
                f"{rel.as_posix()}: {alias.name}"
                for alias in node.names if alias.name in MOVED)
    return offenders


def test_the_fence_finds_absolute_and_relative_imports(
    tmp_path: pathlib.Path,
) -> None:
    pkg = tmp_path / "gain" / "genomic_resources"
    pkg.mkdir(parents=True)
    (pkg / "absolute.py").write_text(
        "from gain.genomic_resources.cli import CommandResult\n")
    (pkg / "relative.py").write_text(
        "from .cli import _create_contents_db, cli_manage\n")
    (pkg / "new_path.py").write_text(
        "from .cli_common import CommandResult\n")

    offenders = moved_name_imports(tmp_path / "gain", "gain")

    assert offenders == [
        "genomic_resources/absolute.py: CommandResult",
        "genomic_resources/relative.py: _create_contents_db",
    ]


def test_the_fence_finds_attribute_reads_of_the_old_module(
    tmp_path: pathlib.Path,
) -> None:
    pkg = tmp_path / "web_annotation"
    pkg.mkdir()
    (pkg / "from_package.py").write_text(
        "from gain.genomic_resources import cli\n"
        "cli._run_stats_core\n"
        "cli.cli_manage\n")
    (pkg / "renamed.py").write_text(
        "import gain.genomic_resources.cli as grr_cli\n"
        "grr_cli._build_browse_argument_parser\n")
    (pkg / "dotted.py").write_text(
        "import gain.genomic_resources.cli\n"
        "gain.genomic_resources.cli.ManifestOutcome\n")
    (pkg / "other_module.py").write_text(
        "from gain.genomic_resources import cli_stats\n"
        "cli_stats._run_stats_core\n")

    offenders = moved_name_imports(tmp_path)

    assert offenders == [
        "web_annotation/dotted.py: ManifestOutcome",
        "web_annotation/from_package.py: _run_stats_core",
        "web_annotation/renamed.py: _build_browse_argument_parser",
    ]


def test_the_fence_finds_string_named_patch_targets(
    tmp_path: pathlib.Path,
) -> None:
    pkg = tmp_path / "tests"
    pkg.mkdir()
    (pkg / "test_setattr.py").write_text(
        "from gain.genomic_resources import cli\n"
        "def test(monkeypatch):\n"
        "    monkeypatch.setattr(cli, '_store_stats_hash', None)\n"
        "    monkeypatch.setattr(cli, 'cli_manage', None)\n")
    (pkg / "test_patch_object.py").write_text(
        "import gain.genomic_resources.cli as grr_cli\n"
        "def test(mocker):\n"
        "    mocker.patch.object(grr_cli, '_run_resource_stats_command')\n")
    (pkg / "test_patch_string.py").write_text(
        "from unittest import mock\n"
        "mock.patch('gain.genomic_resources.cli._create_contents_db')\n"
        "mock.patch('gain.genomic_resources.cli.gzip.compress')\n"
        "mock.patch('gain.genomic_resources.cli_manifest.gzip.compress')\n")
    (pkg / "test_other_module.py").write_text(
        "from gain.genomic_resources import cli_stats\n"
        "def test(monkeypatch):\n"
        "    monkeypatch.setattr(cli_stats, '_store_stats_hash', None)\n")

    offenders = moved_name_imports(tmp_path)

    assert offenders == [
        "tests/test_patch_object.py: _run_resource_stats_command",
        "tests/test_patch_string.py: _create_contents_db",
        "tests/test_setattr.py: _store_stats_hash",
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


def test_the_workspace_is_absent_for_an_installed_gain(
    tmp_path: pathlib.Path,
) -> None:
    site_gain = tmp_path / "lib" / "python3.12" / "site-packages" / "gain"
    site_gain.mkdir(parents=True)
    (site_gain / "__init__.py").write_text("")

    assert gain_workspace(site_gain / "__init__.py") is None


def test_the_workspace_is_found_for_a_source_checkout(
    tmp_path: pathlib.Path,
) -> None:
    source_gain = tmp_path / "core" / "gain"
    source_gain.mkdir(parents=True)
    (source_gain / "__init__.py").write_text("")

    assert gain_workspace(source_gain / "__init__.py") == tmp_path


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
            f"{this_file.as_posix()}: ")
        and f"{member}/{offender}" not in DISPATCHER_TARGETS]

    assert offenders == [], (
        f"import these names from their new modules: {offenders}")
