# pylint: disable=W0621,C0114,C0115,C0116,W0613
"""The order the published index page lists its rows in.

The page rewrites its rows once the search index has loaded, in the
order its own ID sorter produces; until then, and for good if the index
never loads, the reader has the rows as published.  So they are
published in that order (iossifovlab/gain#1351).  ``info_pages_e2e``
pins the two against each other in a browser; this pins the published
half on its own, where a change to the publisher fails fast.
"""
from __future__ import annotations

import pathlib
import re

from gain.genomic_resources.repository import GR_INDEX_FILE_NAME
from gain.genomic_resources.testing import build_filesystem_test_protocol
from gain.genomic_resources.testing.info_page_fixtures import (
    BROWSE_COVERAGE_RESOURCE_ID,
    BROWSE_GENOME_RESOURCE_ID,
    BROWSE_ID_ONLY_RESOURCE_ID,
    BROWSE_ORDERING_RESOURCE_IDS,
    BROWSE_PHASTCONS_RESOURCE_ID,
    BROWSE_SUMMARY_ONLY_RESOURCE_ID,
    a_browse_repo,
)

_TBODY = re.compile(r"<tbody>(.*)</tbody>", re.DOTALL)
# A row's id, read off its link's target -- the one link a row carries.
# Only the target is matched, so that what else the row's markup says is
# the business of the tests about that markup, not of this one.
_ROW_ID = re.compile(r'href="([^"]+)/index\.html"')


def _published_row_ids(tmp_path: pathlib.Path) -> list[str]:
    a_browse_repo(tmp_path)
    proto = build_filesystem_test_protocol(tmp_path)
    proto.build_index_info()
    page = (tmp_path / GR_INDEX_FILE_NAME).read_text(encoding="utf8")
    tbody = _TBODY.search(page)
    assert tbody is not None
    return _ROW_ID.findall(tbody.group(1))


def test_the_published_rows_put_the_capitalised_folder_last(
    tmp_path: pathlib.Path,
) -> None:
    """Not code-point order, which would list it first.

    The fixture's one capitalised folder is what makes the two orders
    distinguishable; a fixture of lowercase ids passes either way.
    Spelled out: ``genomes/…``, ``hg19/…``, the three ``hg38/…`` in
    their own order, then ``Zoo/alpha`` before ``Zoo/Track``.
    """
    rows = _published_row_ids(tmp_path)

    assert rows == [
        BROWSE_GENOME_RESOURCE_ID,
        BROWSE_SUMMARY_ONLY_RESOURCE_ID,
        BROWSE_PHASTCONS_RESOURCE_ID,
        BROWSE_ID_ONLY_RESOURCE_ID,
        BROWSE_COVERAGE_RESOURCE_ID,
        *BROWSE_ORDERING_RESOURCE_IDS,
    ]
