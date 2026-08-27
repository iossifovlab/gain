# pylint: disable=W0621
"""What the editor tells someone who types a retired annotator name.

``np_score`` and ``np_score_annotator`` were retired in GAIn 2026.8.5
(gain#919).  The core registry answers them with a message naming the
replacement, but every editor endpoint below checks
``get_available_annotator_types()`` *before* it reaches that registry --
so without the same treatment here, the one surface a migrating user is
most likely to be looking at would still answer ``Unknown
annotator_type``, which names nothing to migrate to.

The editor never offered these names in its picker, so nobody arrives
here by clicking.  They arrive by opening a saved pipeline, or by typing
what their old YAML said, which is exactly the reader the message is for.
"""
from typing import Any

import pytest
from django.test import Client
from gain.genomic_resources.resource_types import retired_annotator_message

#: The bare refusal each endpoint gives before this is fixed.
_UNHELPFUL_REFUSAL = "Unknown annotator_type"

#: Every editor endpoint that gates on ``get_available_annotator_types``,
#: with the smallest payload that reaches its gate.  ``annotator_config``
#: takes no pipeline; the other three resolve one first.
_ENDPOINTS: dict[str, dict[str, Any]] = {
    "/api/editor/annotator_config": {},
    "/api/editor/annotator_attributes": {
        "resource_id": "scores/pos1",
        "pipeline_id": "pipeline/test_pipeline",
    },
    "/api/editor/annotator_yaml": {
        "resource_id": "scores/pos1",
        "pipeline_id": "pipeline/test_pipeline",
        "attributes": [],
    },
    "/api/editor/annotator_aggregators": {
        "resource_id": "scores/pos1",
        "pipeline_id": "pipeline/test_pipeline",
        "attribute_sources": [],
    },
}


@pytest.mark.parametrize("endpoint", list(_ENDPOINTS))
@pytest.mark.parametrize(
    "retired,replacement", [
        ("np_score", "allele_score"),
        ("np_score_annotator", "allele_score_annotator"),
    ])
def test_retired_annotator_name_is_refused_with_its_replacement(
    user_client: Client,
    endpoint: str,
    retired: str,
    replacement: str,
) -> None:
    """Every gate answers a retired name with the registry's own message.

    Each endpoint checks the available types itself, so each is its own
    opportunity to answer ``Unknown annotator_type`` instead.
    """
    response = user_client.post(
        endpoint,
        data={"annotator_type": retired, **_ENDPOINTS[endpoint]},
        content_type="application/json",
    )

    assert response.status_code == 400, response.content
    error = response.json()["error"]
    # Whole, so the editor cannot drift into paraphrasing the registry;
    # plus the replacement literally, which `retired_annotator_message`
    # cannot pin against itself.
    assert error == retired_annotator_message(retired)
    assert f"'{replacement}' instead" in error


@pytest.mark.parametrize(
    "retired,replacement", [
        ("np_score", "allele_score"),
        ("np_score_annotator", "allele_score_annotator"),
    ])
def test_the_replacement_the_refusal_names_is_one_this_endpoint_accepts(
    user_client: Client,
    retired: str,
    replacement: str,
) -> None:
    """Following the migration advice has to actually work.

    ``annotator_config`` serves templates by name, and served only the
    suffixed spelling -- so it answered ``np_score`` with "write
    'allele_score' instead" and then answered ``allele_score`` with a 500.
    A refusal that names a replacement the same endpoint rejects is worse
    than the bare one it replaced, so the loop is closed here rather than
    assumed: refuse, then take the endpoint at its word.
    """
    refusal = user_client.post(
        "/api/editor/annotator_config",
        data={"annotator_type": retired},
        content_type="application/json",
    )
    assert refusal.status_code == 400
    assert f"'{replacement}' instead" in refusal.json()["error"]

    accepted = user_client.post(
        "/api/editor/annotator_config",
        data={"annotator_type": replacement},
        content_type="application/json",
    )
    assert accepted.status_code == 200, accepted.content


@pytest.mark.parametrize("endpoint", list(_ENDPOINTS))
def test_a_name_gain_never_had_still_gets_the_generic_refusal(
    user_client: Client,
    endpoint: str,
) -> None:
    """An invented name has no replacement to name, so it keeps the old text.

    Without this, the retired-name branch could widen into a catch-all
    that tells every typo to write ``allele_score``.
    """
    response = user_client.post(
        endpoint,
        data={"annotator_type": "no_such", **_ENDPOINTS[endpoint]},
        content_type="application/json",
    )

    assert response.status_code == 400, response.content
    error = response.json()["error"]
    assert _UNHELPFUL_REFUSAL in error
    assert "allele_score" not in error
