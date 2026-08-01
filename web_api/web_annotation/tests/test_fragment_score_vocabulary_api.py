# pylint: disable=W0621,C0116
"""The annotation editor speaks the ``fragment_score`` vocabulary.

gain#471 widens the accepted configuration vocabulary; the editor is where
a user meets it, so what this API offers is what most people will ever
write.  It offers the new spelling and keeps accepting the old.

Exercised through the HTTP endpoints rather than the view classes: the
enumerations are private helpers, and what matters is what a browser is
told.
"""
from typing import Any

import pytest
from rest_framework.test import APIClient

ANNOTATOR_TYPES_URL = "/api/editor/annotator_types"
ANNOTATOR_CONFIG_URL = "/api/editor/annotator_config"


@pytest.fixture
def client() -> APIClient:
    return APIClient()


def _annotator_types(client: APIClient) -> list[str]:
    response = client.get(ANNOTATOR_TYPES_URL)
    assert response.status_code == 200, response.content
    data = response.json()
    types = data["annotator_types"] if isinstance(data, dict) else data
    assert isinstance(types, list)
    return types


@pytest.mark.django_db
def test_editor_offers_the_fragment_score_annotator(
    client: APIClient,
) -> None:
    assert "fragment_score_annotator" in _annotator_types(client)


@pytest.mark.django_db
def test_editor_still_offers_nothing_removed(client: APIClient) -> None:
    """Widening the vocabulary must not drop an annotator from the menu.

    The list is what the editor renders; a name silently disappearing from
    it is not a test failure anywhere else.
    """
    offered = set(_annotator_types(client))
    assert {
        "position_score_annotator",
        "allele_score_annotator",
        "gene_score_annotator",
        "gene_set_annotator",
        "effect_annotator",
        "simple_effect_annotator",
        "liftover_annotator",
        "normalize_allele_annotator",
    } <= offered


def _config_template(client: APIClient, annotator_type: str) -> Any:
    response = client.post(
        ANNOTATOR_CONFIG_URL,
        {"annotator_type": annotator_type},
        format="json")
    assert response.status_code == 200, response.content
    return response.json()


@pytest.mark.django_db
def test_fragment_score_annotator_template_uses_the_new_vocabulary(
    client: APIClient,
) -> None:
    template = _config_template(client, "fragment_score_annotator")

    assert template["annotator_type"] == "fragment_score"
    assert template["fragment_filter"]["field_type"] == "string"
    assert template["resource_id"]["resource_type"] == "fragment_score"


@pytest.mark.django_db
def test_the_editor_documentation_link_targets_a_real_anchor(
    client: APIClient,
) -> None:
    """The Help link must point at the heading the docs actually carry.

    Renaming the documentation heading changes its anchor, so the two move
    together or the link 404s -- and a dead Help link is invisible to
    every other test.
    """
    template = _config_template(client, "fragment_score_annotator")
    url = template["documentation_url"]

    # `endswith` alone is not enough, and the first version of this test
    # proved it: the base URL used to carry its own trailing '#', so every
    # link was `...html##fragment-score-annotator`.  That ends with the
    # right text and resolves to nothing -- a browser reads the fragment as
    # `#fragment-score-annotator` only if there is exactly one '#'.
    assert url.count("#") == 1, url

    # The anchor docutils generates for a heading lowercases it and turns
    # every run of non-alphanumerics into a single '-', so the RST heading
    # `fragment_score_annotator` is reachable as `#fragment-score-annotator`.
    # Spelled out rather than computed with `docutils.nodes.make_id`: that
    # would import a docs-only dependency into the web_api test suite, which
    # does not otherwise have it.
    assert url.endswith("#fragment-score-annotator"), url


RESOURCES_URL = "/api/resources"


@pytest.mark.django_db
def test_picker_filtered_by_the_new_type_finds_legacy_typed_resources(
    client: APIClient,
) -> None:
    """The editor's resource picker must not go blank.

    The annotator template asks the picker for ``resource_type:
    fragment_score``, while a GRR may declare ``cnv_collection``. An
    exact-equality filter would therefore offer the user an empty list
    -- a silent dead end, since an empty result is indistinguishable from
    "this GRR has none".
    """
    legacy = "cnv_collections/test_collection"

    # Sanity: the fixture GRR really does hold a legacy-typed resource, so
    # an empty result below means the filter dropped it rather than that
    # there was nothing to find.
    by_legacy_name = client.get(RESOURCES_URL, {"type": "cnv_collection"})
    assert legacy in by_legacy_name.json()

    response = client.get(RESOURCES_URL, {"type": "fragment_score"})
    assert response.status_code == 200, response.content

    assert legacy in response.json()


RESOURCE_ANNOTATORS_URL = "/api/editor/resource_annotators"


@pytest.mark.django_db
def test_resource_first_flow_works_for_a_legacy_typed_resource(
    client: APIClient,
) -> None:
    """Picking a legacy-typed fragment score must offer its annotator.

    The editor's resource-first flow asks "which annotators accept this
    resource?" by matching the annotator template's ``resource_type``
    against the resource's own. The template says ``fragment_score`` while
    this fixture says ``cnv_collection``, so an equality match returns an
    empty ``configs`` -- while ``default`` still names an
    annotator.  The UI then looks the default up in the empty list, so this
    is a crash in the wizard rather than a shorter menu.
    """
    response = client.get(
        RESOURCE_ANNOTATORS_URL,
        {"resource_id": "cnv_collections/test_collection"})
    assert response.status_code == 200, response.content

    data = response.json()
    assert data["default"] == "fragment_score_annotator"
    # The invariant the UI relies on: whatever `default` names must be a
    # key of `configs`.
    assert data["default"] in data["configs"], data
    assert data["configs"]["fragment_score_annotator"]["resource_id"] == \
        "cnv_collections/test_collection"


@pytest.mark.django_db
def test_resource_search_by_the_new_type_finds_legacy_typed_resources(
    client: APIClient,
) -> None:
    """The picker's *search* path needs the same expansion as its list path.

    Separate endpoint, separate code path -- and this one pushes the type
    predicate down into the FTS index, where no Python-side filtering can
    recover a row the query never returned.
    """
    legacy = "cnv_collections/test_collection"

    by_legacy = client.get(
        f"{RESOURCES_URL}/search", {"type": "cnv_collection"})
    assert legacy in str(by_legacy.json()), by_legacy.json()

    response = client.get(
        f"{RESOURCES_URL}/search", {"type": "fragment_score"})
    assert response.status_code == 200, response.content
    assert legacy in str(response.json()), response.json()
