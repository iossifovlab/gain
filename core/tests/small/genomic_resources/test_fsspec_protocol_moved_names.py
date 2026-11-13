"""The names that left ``fsspec_protocol`` still import from it, with a warning.

The copy and publish errors and the credential redaction helpers moved to
sibling modules (gain#2267). Code outside gain imports some of the public
names from the old path, so each public name stays importable from
``gain.genomic_resources.fsspec_protocol`` for one release and warns there.
Private names get no alias.
"""
import ast
import importlib
import pathlib
import warnings

import gain
import pytest
from gain.genomic_resources import fsspec_protocol

ERRORS = "gain.genomic_resources.fsspec_errors"
REDACTION = "gain.genomic_resources.fsspec_redaction"

ALIASED = [
    (name, ERRORS) for name in (
        "ChecksumMismatchError",
        "CorruptedPublishError",
        "FileCacheVerdict",
        "GRR_INTERNAL_DIR",
        "RetryableCopyError",
        "TruncatedDownloadError",
    )
]

#: Every name that moved, the private ones included. No gain code imports
#: any of them from the old module.
MOVED = {
    *(name for name, _ in ALIASED),
    "_COPY_BACKOFF_BASE",
    "_COPY_MAX_ATTEMPTS",
    "_RETRYABLE_COPY_ERRORS",
    "_aiohttp_errors",
    "_botocore_errors",
    "_HTSLIB_VERBOSITY_LOCK",
    "_OPTIONAL_REDACTED_OPS",
    "_RedactingFile",
    "_STDERR_FILENO",
    "_STDERR_SUPPRESSION_LOCK",
    "_display_url",
    "_error_without_url_credentials",
    "_fetch_url_form",
    "_htslib_silenced",
    "_open_htslib_file",
    "_open_libbigwig_file",
    "_rebuild_error_without_url_credentials",
    "_redacting_call",
    "_run_redacting_url_credentials",
    "_strip_netloc_userinfo",
    "_url_carries_credential",
    "_url_carries_userinfo",
}


def test_the_alias_map_names_exactly_the_public_moved_names() -> None:
    assert dict(ALIASED) == fsspec_protocol._MOVED


@pytest.mark.parametrize(("name", "new_module"), ALIASED)
def test_old_path_gives_the_moved_object_and_warns(
    name: str, new_module: str,
) -> None:
    moved = getattr(importlib.import_module(new_module), name)

    with pytest.warns(DeprecationWarning, match=new_module) as record:
        aliased = getattr(fsspec_protocol, name)

    assert aliased is moved
    assert [str(warning.message) for warning in record] == [
        f"{name} moved to {new_module}"]


def test_from_import_of_a_name_the_old_module_uses_warns() -> None:
    errors = importlib.import_module(ERRORS)

    with pytest.warns(DeprecationWarning, match=f"moved to {ERRORS}"):
        # The import statement is the seam that a caller outside gain uses.
        from gain.genomic_resources.fsspec_protocol import (
            ChecksumMismatchError,
        )

    assert ChecksumMismatchError is errors.ChecksumMismatchError


def test_a_name_that_stayed_does_not_warn() -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        protocol_class = fsspec_protocol.FsspecReadWriteProtocol

    assert protocol_class.__module__ == fsspec_protocol.__name__


def test_a_private_moved_name_does_not_warn() -> None:
    redaction = importlib.import_module(REDACTION)

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        display_url = fsspec_protocol._display_url

    assert display_url is redaction._display_url


def test_an_unknown_name_raises_attribute_error() -> None:
    unknown = "no_such_name"

    with pytest.raises(AttributeError):
        getattr(fsspec_protocol, unknown)


#: The private moved names that ``fsspec_protocol`` imports back and calls
#: through its own namespace. A test patches them there on purpose, so a
#: read of ``fsspec_protocol.<name>`` is the seam, not a stale import.
PATCH_SEAMS = {"_display_url", "_fetch_url_form"}

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
    old_package, _, old_name = fsspec_protocol.__name__.rpartition(".")
    aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            aliases.update(
                alias.asname for alias in node.names
                if alias.name == fsspec_protocol.__name__ and alias.asname)
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


def moved_name_imports(root: pathlib.Path, package: str = "") -> list[str]:
    """List each use of a moved name through the old module under ``root``.

    A use is an import of the name from the old module, or a read of the
    name as an attribute of the old module. ``package`` is the dotted name of
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
            if isinstance(node, ast.Attribute):
                owner = _dotted(node.value)
                if node.attr in MOVED and node.attr not in PATCH_SEAMS and (
                        owner in aliases
                        or owner == fsspec_protocol.__name__):
                    offenders.append(f"{rel.as_posix()}: {node.attr}")
                continue
            if not isinstance(node, ast.ImportFrom):
                continue
            if _from_module(node, own_package) != fsspec_protocol.__name__:
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
        "from gain.genomic_resources.fsspec_protocol import "
        "GRR_INTERNAL_DIR\n")
    (pkg / "relative.py").write_text(
        "from .fsspec_protocol import _RedactingFile, build_local_resource\n")
    (pkg / "new_path.py").write_text(
        "from .fsspec_errors import GRR_INTERNAL_DIR\n")

    offenders = moved_name_imports(tmp_path / "gain", "gain")

    assert offenders == [
        "genomic_resources/absolute.py: GRR_INTERNAL_DIR",
        "genomic_resources/relative.py: _RedactingFile",
    ]


def test_the_fence_finds_attribute_reads_of_the_old_module(
    tmp_path: pathlib.Path,
) -> None:
    pkg = tmp_path / "web_annotation"
    pkg.mkdir()
    (pkg / "from_package.py").write_text(
        "from gain.genomic_resources import fsspec_protocol\n"
        "fsspec_protocol.GRR_INTERNAL_DIR\n"
        "fsspec_protocol.build_local_resource\n")
    (pkg / "renamed.py").write_text(
        "import gain.genomic_resources.fsspec_protocol as fp\n"
        "fp._RedactingFile\n")
    (pkg / "dotted.py").write_text(
        "import gain.genomic_resources.fsspec_protocol\n"
        "gain.genomic_resources.fsspec_protocol.RetryableCopyError\n")
    (pkg / "seam.py").write_text(
        "from gain.genomic_resources import fsspec_protocol\n"
        "fsspec_protocol._display_url\n")
    (pkg / "other_module.py").write_text(
        "from gain.genomic_resources import fsspec_errors\n"
        "fsspec_errors.GRR_INTERNAL_DIR\n")

    offenders = moved_name_imports(tmp_path)

    assert offenders == [
        "web_annotation/dotted.py: RetryableCopyError",
        "web_annotation/from_package.py: GRR_INTERNAL_DIR",
        "web_annotation/renamed.py: _RedactingFile",
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
            f"{this_file.as_posix()}: ")]

    assert offenders == [], (
        f"import these names from their new modules: {offenders}")
