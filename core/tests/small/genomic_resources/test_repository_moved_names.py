"""The names that left ``repository`` still import from it, with a warning.

The ID validation, the manifest model and the search helpers moved to
sibling modules (gain#2266). Code outside gain imports some of them from
the old path, so each public name stays importable from
``gain.genomic_resources.repository`` for one release and warns there.
"""
import ast
import importlib
import pathlib
import warnings

import gain
import pytest
from gain.genomic_resources import repository

IDS = "gain.genomic_resources.repository_ids"
MANIFEST = "gain.genomic_resources.repository_manifest"
SEARCH = "gain.genomic_resources.repository_search"

ALIASED = [
    *((name, IDS) for name in (
        "GR_CONF_FILE_NAME",
        "GR_CONTENTS_FILE_NAME",
        "GR_ENCODING",
        "GR_GENERATED_INFO_PAGES",
        "GR_INDEX_FILE_NAME",
        "GR_INDEX_NON_LABEL_COLUMNS",
        "GR_INDEX_RESOURCE_FIELDS",
        "GR_INDEX_SCORE_FIELDS",
        "GR_LEGACY_CONTENTS_FILE_NAME",
        "GR_MANIFEST_FILE_NAME",
        "GR_SQLITE_META_FILE_NAME",
        "GR_STATISTICS_FOLDER_NAME",
        "GR_STATISTICS_INDEX_FILE_NAME",
        "INDEX_COLUMN_PATTERN",
        "INDEX_COLUMN_RE",
        "RESOURCE_ID_CHARACTER_CLASS",
        "VERSION_CONSTRAINT_RE",
        "is_generated_info_page",
        "is_gr_id_token",
        "is_safe_repo_id",
        "is_version_constraint_satisfied",
        "malformed_resource_id_reason",
        "parse_gr_id_version_token",
        "parse_resource_id_version",
        "uncontained_resource_file_name_reason",
        "uncontained_resource_id_reason",
        "validate_resource_file_name",
        "validate_resource_id",
        "version_tuple_to_string",
        "version_tuple_to_suffix",
    )),
    *((name, MANIFEST) for name in (
        "Manifest",
        "ManifestEntry",
        "ManifestUpdate",
        "ResourceFileState",
        "ResourceScan",
        "Unread",
        "UnreadableResourceFilesError",
    )),
    *((name, SEARCH) for name in (
        "SearchIndexUnavailableError",
        "SearchTermError",
        "drain_search",
    )),
]


@pytest.mark.parametrize(("name", "new_module"), ALIASED)
def test_old_path_gives_the_moved_object_and_warns(
    name: str, new_module: str,
) -> None:
    moved = getattr(importlib.import_module(new_module), name)

    with pytest.warns(DeprecationWarning, match=new_module) as record:
        aliased = getattr(repository, name)

    assert aliased is moved
    assert [str(warning.message) for warning in record] == [
        f"{name} moved to {new_module}"]


def test_from_import_of_a_name_the_old_module_uses_warns() -> None:
    ids = importlib.import_module(IDS)

    with pytest.warns(DeprecationWarning, match=f"moved to {IDS}"):
        # The import statement is the seam: gpf spells it this way.
        from gain.genomic_resources.repository import GR_CONF_FILE_NAME

    assert GR_CONF_FILE_NAME is ids.GR_CONF_FILE_NAME


def test_a_name_that_stayed_does_not_warn() -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        resource_class = repository.GenomicResource

    assert resource_class.__module__ == repository.__name__


def test_an_unknown_name_raises_attribute_error() -> None:
    with pytest.raises(AttributeError):
        _ = repository.no_such_name  # type: ignore[attr-defined]


def moved_name_imports(root: pathlib.Path, package: str) -> list[str]:
    """List each import of a moved name from the old module under ``root``.

    ``package`` is the dotted name of ``root``. A relative import resolves
    against the package of its file. Each entry is ``<file>: <name>``.
    """
    offenders: list[str] = []
    for py in sorted(root.rglob("*.py")):
        rel = py.relative_to(root)
        own_package = [package, *rel.with_suffix("").parts][:-1]
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            module = node.module or ""
            if node.level:
                base = own_package[:len(own_package) - node.level + 1]
                module = ".".join([*base, module])
            if module != repository.__name__:
                continue
            offenders.extend(
                f"{rel.as_posix()}: {alias.name}"
                for alias in node.names if alias.name in repository._MOVED)
    return offenders


def test_the_fence_finds_absolute_and_relative_imports(
    tmp_path: pathlib.Path,
) -> None:
    pkg = tmp_path / "gain" / "genomic_resources"
    pkg.mkdir(parents=True)
    (pkg / "absolute.py").write_text(
        "from gain.genomic_resources.repository import Manifest\n")
    (pkg / "relative.py").write_text(
        "from .repository import GR_CONF_FILE_NAME, GenomicResource\n")
    (pkg / "new_path.py").write_text(
        "from .repository_ids import GR_CONF_FILE_NAME\n")

    offenders = moved_name_imports(tmp_path / "gain", "gain")

    assert offenders == [
        "genomic_resources/absolute.py: Manifest",
        "genomic_resources/relative.py: GR_CONF_FILE_NAME",
    ]


def test_nothing_in_gain_imports_a_moved_name_from_the_old_module() -> None:
    gain_pkg = pathlib.Path(gain.__file__).parent
    assert (gain_pkg / "genomic_resources" / "repository.py").exists()

    offenders = moved_name_imports(gain_pkg, "gain")

    assert offenders == [], (
        f"import these names from their new modules: {offenders}")
