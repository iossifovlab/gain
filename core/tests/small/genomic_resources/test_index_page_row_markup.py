# pylint: disable=W0621,C0114,C0115,C0116,W0613
"""What one published index-page row costs.

The index page is served as it is written -- the hosts that publish the
large GRRs send it uncompressed -- so every byte a row carries is paid
for once per resource.  grr-encode lists 7,923 of them, and at ~1 KB a
row the page was 8.5 MB and took 10-40 s to arrive
(iossifovlab/gain#1711).  Most of that was the same values written
again: the id five times, the summary twice, and the template's own
indentation.

So a published row carries each value once -- the id twice, as the
link's target and its text -- and anything derived from those values
(tooltips, the copy icon) is added by the page's script, which reads
the rows anyway.
"""
from __future__ import annotations

import pathlib
import re
from typing import NamedTuple

import pytest
from gain.genomic_resources.repository import GR_INDEX_FILE_NAME
from gain.genomic_resources.testing import build_filesystem_test_protocol
from gain.genomic_resources.testing.info_page_fixtures import (
    BROWSE_RESOURCE_IDS,
    a_browse_repo,
)
from markupsafe import escape

_TBODY = re.compile(r"<tbody>(.*)</tbody>", re.DOTALL)
_LINK_TEXT = re.compile(r"<a\b[^>]*>([^<]*)</a>")


class Published(NamedTuple):
    tbody: str
    summaries: dict[str, str]


@pytest.fixture(scope="module")
def published(tmp_path_factory: pytest.TempPathFactory) -> Published:
    where: pathlib.Path = tmp_path_factory.mktemp("browse")
    repo = a_browse_repo(where)
    build_filesystem_test_protocol(where).build_index_info()
    page = (where / GR_INDEX_FILE_NAME).read_text(encoding="utf8")
    match = _TBODY.search(page)
    assert match is not None
    return Published(
        tbody=match.group(1),
        summaries={
            res.get_full_id(): res.get_summary() or ""
            for res in repo.get_all_resources()
        },
    )


def _rows(tbody: str) -> list[str]:
    """Each row's raw markup, from its ``<tr`` up to the next one."""
    rows = [f"<tr{chunk}" for chunk in tbody.split("<tr")[1:]]
    # An empty body would make every per-row assertion below pass
    # vacuously.
    assert len(rows) == len(BROWSE_RESOURCE_IDS)
    return rows


def test_a_row_carries_its_summary_once(published: Published) -> None:
    for row in _rows(published.tbody):
        link = _LINK_TEXT.search(row)
        assert link is not None, row
        summary = str(escape(published.summaries[link.group(1)]))
        assert summary
        assert row.count(summary) == 1, row


def test_a_row_carries_its_id_only_as_the_link_and_its_text(
    published: Published,
) -> None:
    for row in _rows(published.tbody):
        link = _LINK_TEXT.search(row)
        assert link is not None, row
        assert row.count(link.group(1)) == 2, row
        assert f'href="{link.group(1)}/index.html"' in row, row


def test_the_rows_carry_no_indentation(published: Published) -> None:
    assert re.search(r">\s+<", published.tbody) is None
