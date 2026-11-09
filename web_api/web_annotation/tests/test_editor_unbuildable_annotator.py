# pylint: disable=W0621,C0116
"""What the editor tells someone whose annotator configuration cannot build.

The annotator factory refuses a configuration that names a missing
resource, or a resource of the wrong type, with a ``ValueError`` or a
``FileNotFoundError``.  Before iossifovlab/gain#2199 the attribute and
aggregator endpoints let those reach Django, which answered 500.  They
now refuse with a 400 that names the cause, as ``annotator_yaml`` does.

The editor posts partial configurations while the user types, so a
configuration that builds still gets a 200, even with an unknown
parameter in it.
"""
from typing import Any

import pytest
from django.test import Client

ANNOTATOR_ATTRIBUTES_URL = "/api/editor/annotator_attributes"
ANNOTATOR_AGGREGATORS_URL = "/api/editor/annotator_aggregators"
EDITOR_URLS = [ANNOTATOR_ATTRIBUTES_URL, ANNOTATOR_AGGREGATORS_URL]
PIPELINE_ID = "pipeline/test_pipeline"


@pytest.fixture
def client() -> Client:
    # On the base the factory exception is UNCAUGHT.  This client reports
    # it as the 500 a browser sees, instead of re-raising it into the test.
    return Client(raise_request_exception=False)


def _post(client: Client, url: str, config: dict[str, Any]) -> Any:
    return client.post(
        url,
        data={"pipeline_id": PIPELINE_ID, **config},
        content_type="application/json",
    )


@pytest.mark.django_db
@pytest.mark.parametrize("url", EDITOR_URLS)
@pytest.mark.parametrize("config,expected_in_error", [
    (
        {
            "annotator_type": "effect_annotator",
            "genome": "no/such_genome",
            "gene_models": "t4c8/t4c8_genes",
        },
        "no/such_genome",
    ),
    (
        {
            "annotator_type": "effect_annotator",
            "genome": "t4c8/t4c8_genome",
            "gene_models": "t4c8/t4c8_genome",
        },
        "wrong resource type",
    ),
    (
        {
            "annotator_type": "position_score_annotator",
            "resource_id": "t4c8/t4c8_genome",
        },
        "requires 'resource_id' to point to",
    ),
], ids=["missing-genome", "wrong-gene-models-type", "wrong-score-type"])
def test_unbuildable_configuration_is_refused_with_its_cause(
    client: Client,
    url: str,
    config: dict[str, Any],
    expected_in_error: str,
) -> None:
    response = _post(client, url, config)

    assert response.status_code == 400, response.content
    error = response.json()["error"]
    assert error.startswith("Invalid annotator configuration: ")
    assert expected_in_error in error


@pytest.mark.django_db
def test_unbuildable_configuration_wins_over_a_bad_search_term(
    client: Client,
) -> None:
    response = _post(client, ANNOTATOR_ATTRIBUTES_URL, {
        "annotator_type": "effect_annotator",
        "genome": "no/such_genome",
        "gene_models": "t4c8/t4c8_genes",
        "search": 7,
    })

    assert response.status_code == 400, response.content
    assert "no/such_genome" in response.json()["error"]


@pytest.mark.django_db
@pytest.mark.parametrize("url", EDITOR_URLS)
def test_unknown_parameter_in_a_buildable_configuration_is_tolerated(
    client: Client,
    url: str,
) -> None:
    response = _post(client, url, {
        "annotator_type": "effect_annotator",
        "genome": "t4c8/t4c8_genome",
        "gene_models": "t4c8/t4c8_genes",
        "bogus": 1,
    })

    assert response.status_code == 200, response.content
