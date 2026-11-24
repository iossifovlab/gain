# pylint: disable=C0116
"""What the search term of the labelled fixture matches.

The download tests of ``info_pages_e2e`` search the labelled fixture for
one term and filter it to one type.  Their records are only worth
anything while the term matches a known set of resources, so this pins
the set against the real index.
"""
import json
import pathlib
import re

from gain.genomic_resources.cli_manifest import _create_contents_db
from gain.genomic_resources.repository_ids import GR_INDEX_FILE_NAME
from gain.genomic_resources.testing import build_filesystem_test_protocol
from gain.genomic_resources.testing.info_page_fixtures import (
    LABELLED_GENOME_RESOURCE_ID,
    LABELLED_LIST_LABEL,
    LABELLED_NUMBER_LABEL,
    LABELLED_ONE_ITEM_RESOURCE_ID,
    LABELLED_SCALAR_LABEL,
    LABELLED_SEARCH_TERM,
    LABELLED_VERSIONED_RESOURCE_ID,
    a_labelled_repo,
)


def test_the_search_term_matches_two_scores_and_the_genome(
    tmp_path: pathlib.Path,
) -> None:
    repo = a_labelled_repo(tmp_path)
    _create_contents_db(build_filesystem_test_protocol(tmp_path))

    found = {
        res.resource_id
        for res in repo.search_resources(search_term=LABELLED_SEARCH_TERM)
    }

    assert found == {
        LABELLED_VERSIONED_RESOURCE_ID,
        LABELLED_ONE_ITEM_RESOURCE_ID,
        LABELLED_GENOME_RESOURCE_ID,
    }


def test_the_root_page_of_the_fixture_carries_its_three_facets(
    tmp_path: pathlib.Path,
) -> None:
    a_labelled_repo(tmp_path)
    build_filesystem_test_protocol(tmp_path).build_index_info()
    page = (tmp_path / GR_INDEX_FILE_NAME).read_text(encoding="utf8")

    match = re.search(
        r'<script type="application/json" id="search-facets">(.*?)'
        r"</script>", page, re.DOTALL)

    assert match is not None
    assert json.loads(match.group(1)) == [
        {"label": LABELLED_SCALAR_LABEL, "title": "Tissue"},
        {"label": LABELLED_LIST_LABEL, "title": "Modality"},
        {"label": LABELLED_NUMBER_LABEL, "title": LABELLED_NUMBER_LABEL},
    ]
