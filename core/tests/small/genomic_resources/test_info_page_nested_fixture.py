# pylint: disable=C0116
"""The shape of the nested fixture.

The folder tests of ``info_pages_e2e`` (``extended-search-folders.spec.ts``)
copy the counts of each folder by hand.  The CI image does not run that
suite, so this pins the counts against the built repository: an edit of
``NESTED_RESOURCES`` fails here first.
"""
import collections
import pathlib

import pytest
from gain.genomic_resources.testing.info_page_fixtures import (
    a_nested_labelled_repo,
)


def _folder_counts(
    tmp_path: pathlib.Path, folder: str,
) -> dict[str, dict[str, int]]:
    repo = a_nested_labelled_repo(tmp_path)
    counts: dict[str, collections.Counter[str]] = {
        "tissue": collections.Counter(),
        "modality": collections.Counter(),
    }
    for res in repo.get_all_resources():
        if not res.resource_id.startswith(folder):
            continue
        labels = res.get_labels()
        counts["tissue"][labels["tissue"]] += 1
        counts["modality"].update(labels["modality"])
    return {label: dict(counter) for label, counter in counts.items()}


@pytest.mark.parametrize("folder, expected", [
    ("", {
        "tissue": {"cortex": 2, "hippocampus": 1, "liver": 1, "medulla": 1},
        "modality": {"ATAC": 3, "RNA": 3},
    }),
    ("brain/", {
        "tissue": {"cortex": 2, "hippocampus": 1},
        "modality": {"ATAC": 2, "RNA": 2},
    }),
    ("brain/cortex/", {
        "tissue": {"cortex": 2},
        "modality": {"RNA": 2, "ATAC": 1},
    }),
])
def test_the_counts_of_a_folder_match_the_e2e_constants(
    tmp_path: pathlib.Path,
    folder: str,
    expected: dict[str, dict[str, int]],
) -> None:
    assert _folder_counts(tmp_path, folder) == expected


def test_brainstem_starts_with_brain_but_is_not_under_it(
    tmp_path: pathlib.Path,
) -> None:
    repo = a_nested_labelled_repo(tmp_path)

    ids = {res.resource_id for res in repo.get_all_resources()}

    assert "brainstem/medulla" in ids
    assert not any(
        rid.startswith("brain/") and rid.endswith("/medulla")
        for rid in ids)
