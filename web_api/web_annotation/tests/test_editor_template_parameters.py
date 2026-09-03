# pylint: disable=W0621,C0116
"""The editor's config templates agree with the annotators they describe.

A gain annotator parameter is declared TWICE: once by the constructor
reading it -- ``info.parameters`` refuses a key nobody read, so the read
IS the declaration -- and once in the hand-maintained template the editor
renders as the configuration form.  Skip the second and the parameter
works in a hand-written pipeline and over the API but is invisible in the
UI.  This module compares the two declarations, for every annotator type
the editor offers (gain#1165).

Exercised through the HTTP endpoints rather than the view classes: the
templates are private helpers, and what matters is what a browser is told.
"""
from typing import Any

import pytest
from rest_framework.test import APIClient

ANNOTATOR_TYPES_URL = "/api/editor/annotator_types"
ANNOTATOR_CONFIG_URL = "/api/editor/annotator_config"


@pytest.fixture
def client() -> APIClient:
    return APIClient()


def _config_template(client: APIClient, annotator_type: str) -> Any:
    response = client.post(
        ANNOTATOR_CONFIG_URL,
        {"annotator_type": annotator_type},
        format="json")
    assert response.status_code == 200, response.content
    return response.json()


@pytest.mark.django_db
def test_the_allele_score_template_offers_the_filter_and_the_mode(
    client: APIClient,
) -> None:
    """``allele_filter`` and ``mode`` are reachable from the form.

    Both are documented allele score parameters, and ``allele_filter`` is
    the direct analogue of the fragment score's ``fragment_filter``, which
    the editor already offers.  ``string`` for the reason recorded on the
    fragment score template: it is the only field type the form renders
    free text as.
    """
    template = _config_template(client, "allele_score_annotator")

    assert template["allele_filter"] == {
        "field_type": "string", "optional": True}
    assert template["mode"] == {"field_type": "string", "optional": True}


@pytest.mark.django_db
@pytest.mark.parametrize("annotator_type", [
    "position_score_annotator",
    "allele_score_annotator",
    "effect_annotator",
])
def test_the_template_offers_the_region_length_cutoff(
    client: APIClient, annotator_type: str,
) -> None:
    """Every annotator with a region length cutoff lets the form set it.

    The documentation tells a user annotating large CNVs to *raise
    ``region_length_cutoff`` on the annotator*; a form that cannot express
    it sends that user to a hand-written pipeline.
    """
    template = _config_template(client, annotator_type)

    assert template["region_length_cutoff"] == {
        "field_type": "string", "optional": True}
