# pylint: disable=C0116
"""What a ``search.yaml`` at the root of a GRR puts on its root page.

ADR 0045: the file names the **facets** of the **extended search**.
``build_index_info`` reads it as it reads ``about.md`` and writes the
facet list into ``index.html`` as JSON.  A GRR without the file gets
no extended search: no facet list and no button (gain#2501).
"""
from __future__ import annotations

import json
import logging
import pathlib
import re
import textwrap

import pytest
from gain.genomic_resources.cli import cli_manage
from gain.genomic_resources.repository_ids import (
    GR_CONF_FILE_NAME,
    GR_INDEX_FILE_NAME,
)
from gain.genomic_resources.testing import (
    build_filesystem_test_protocol,
    setup_directories,
)

_FACETS = re.compile(
    r'<script type="application/json" id="search-facets">(.*?)</script>',
    re.DOTALL)
_BUTTON = 'id="extended-search-btn"'


def _labelled(**labels: str) -> dict[str, str]:
    lines = "".join(f"    {key}: {value}\n" for key, value in labels.items())
    return {
        GR_CONF_FILE_NAME: f"type: basic\nmeta:\n  labels:\n{lines}",
        "data.txt": "alabala",
    }


def _a_repo(root: pathlib.Path, search_yaml: str | None) -> pathlib.Path:
    content: dict = {
        "one": _labelled(assay="ATAC", species="human"),
        "two": _labelled(assay="RNA", brain_region="m1"),
    }
    if search_yaml is not None:
        content["search.yaml"] = search_yaml
    setup_directories(root, content)
    return root


def _root_page(root: pathlib.Path) -> str:
    build_filesystem_test_protocol(root).build_index_info()
    return (root / GR_INDEX_FILE_NAME).read_text(encoding="utf8")


def _facets_on(page: str) -> list[dict[str, str]] | None:
    match = _FACETS.search(page)
    return None if match is None else json.loads(match.group(1))


def test_the_root_page_carries_the_facets_in_file_order_with_titles(
    tmp_path: pathlib.Path,
) -> None:
    root = _a_repo(tmp_path, textwrap.dedent("""\
        facets:
          - label: species
            title: Species
          - label: assay
          - label: brain_region
            title: Brain region
        """))

    page = _root_page(root)

    assert _facets_on(page) == [
        {"label": "species", "title": "Species"},
        {"label": "assay", "title": "assay"},
        {"label": "brain_region", "title": "Brain region"},
    ]
    assert _BUTTON in page


def test_without_search_yaml_the_root_page_has_no_facets_and_no_button(
    tmp_path: pathlib.Path,
) -> None:
    root = _a_repo(tmp_path, None)

    page = _root_page(root)

    assert _facets_on(page) is None
    assert _BUTTON not in page


def test_a_facet_on_a_key_no_resource_carries_is_skipped_with_a_warning(
    tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture,
) -> None:
    root = _a_repo(tmp_path, textwrap.dedent("""\
        facets:
          - label: assay
          - label: tissue
        """))

    with caplog.at_level(logging.WARNING):
        page = _root_page(root)

    assert _facets_on(page) == [{"label": "assay", "title": "assay"}]
    assert any(
        "tissue" in record.getMessage()
        and record.levelno == logging.WARNING
        for record in caplog.records), caplog.text


def test_invalid_yaml_stops_grr_manage_with_a_message_naming_the_file(
    tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture,
) -> None:
    root = _a_repo(tmp_path, "facets: [label: assay\n")

    with pytest.raises(SystemExit) as stopped:
        cli_manage(["repo-index", "-R", str(root)])

    assert stopped.value.code != 0
    assert str(root / "search.yaml") in caplog.text


def test_a_label_in_two_facets_stops_grr_manage_with_a_message_naming_the_file(
    tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture,
) -> None:
    root = _a_repo(tmp_path, textwrap.dedent("""\
        facets:
          - label: assay
          - label: species
          - label: assay
        """))

    with pytest.raises(SystemExit) as stopped:
        cli_manage(["repo-index", "-R", str(root)])

    assert stopped.value.code != 0
    assert str(root / "search.yaml") in caplog.text
    assert "'assay'" in caplog.text
