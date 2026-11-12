"""The names that left ``repository`` still import from it, with a warning.

The ID validation, the manifest model and the search helpers moved to
sibling modules (gain#2266). Code outside gain imports some of them from
the old path, so each public name stays importable from
``gain.genomic_resources.repository`` for one release and warns there.
"""
import importlib
import warnings

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
        "SearchIndexUnavailableError",
        "SearchTermError",
        "Unread",
        "UnreadableResourceFilesError",
    )),
    ("drain_search", SEARCH),
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
