# pylint: disable=W0621,C0114,C0116
"""A request body of the wrong shape is a 400, not an AssertionError.

These views used to open with ``assert isinstance(request.data, ...)``, so a
syntactically valid body that parsed to the wrong type -- a JSON array or
scalar on a JSON-object endpoint -- raised ``AssertionError``: an unhandled
500 on an anonymous endpoint (iossifovlab/gain#1650).
"""
from typing import Any

import pytest
from django.forms.models import model_to_dict
from django.test import Client

from web_annotation.models import AlleleQuery, User, UserQuota

SINGLE_ALLELE_URL = "/api/single_allele/annotate"

#: (url, error key the view's module uses for its 400s)
JSON_OBJECT_ENDPOINTS = [
    ("/api/editor/annotator_config", "error"),
    ("/api/editor/annotator_attributes", "error"),
    ("/api/editor/annotator_yaml", "error"),
    ("/api/editor/annotator_aggregators", "error"),
    ("/api/pipelines/load", "reason"),
    (SINGLE_ALLELE_URL, "reason"),
    ("/api/login", "error"),
    ("/api/register", "error"),
]

NON_OBJECT_BODIES = ["[]", "3", '"x"', "null"]


@pytest.mark.django_db
@pytest.mark.parametrize("body", NON_OBJECT_BODIES)
@pytest.mark.parametrize(("url", "key"), JSON_OBJECT_ENDPOINTS)
def test_non_object_json_body_is_a_400(
    anonymous_client: Client, url: str, key: str, body: str,
) -> None:
    response = anonymous_client.post(
        url, body, content_type="application/json",
    )

    assert response.status_code == 400
    assert response.json() == {key: "Request body must be a JSON object"}


#: Views that read ``request.data`` as multipart form data; a JSON body
#: parses to a ``dict``, not a ``QueryDict``.
MULTIPART_ENDPOINTS = [
    "/api/jobs/preview",
    "/api/pipelines/user",
]


@pytest.mark.django_db
@pytest.mark.parametrize("url", MULTIPART_ENDPOINTS)
def test_json_body_on_a_multipart_endpoint_is_a_400(
    anonymous_client: Client, url: str,
) -> None:
    response = anonymous_client.post(
        url, '{"a": 1}', content_type="application/json",
    )

    assert response.status_code == 400
    assert response.json() == {"reason": "Invalid content type!"}


#: ``annotatable`` values the single-allele view cannot annotate, each of
#: which used to be a 500 (iossifovlab/gain#1660). A non-object tripped an
#: ``assert``; an object went to ``build_annotatable_from_dict``, which picks
#: its converter by the object's keys -- so every shape it recognises is
#: reachable here, and each fails differently on a wrongly-typed value.
INVALID_ANNOTATABLES = [
    3,
    [1],
    "chr1:1 C>A",
    None,
    {},
    {"chrom": "chr1", "pos": "x", "ref": "C", "alt": "A"},
    {"chrom": "chr1", "pos": None, "ref": "C", "alt": "A"},
    {"chrom": "chr1", "pos": 1, "ref": 3, "alt": "A"},
    {"chrom": "chr1", "pos": 1, "ref": "C", "alt": None},
    {"vcf_like": 3},
    {"location": 3, "variant": "sub(A->C)"},
    {"location": "chr1:100", "variant": 3},
    {"chrom": "chr1", "pos_beg": 1, "pos_end": 2, "cnv_type": 3},
]


def _post_annotatable(client: Client, annotatable: object) -> Any:
    return client.post(
        SINGLE_ALLELE_URL,
        {"pipeline_id": "pipeline/test_pipeline", "annotatable": annotatable},
        content_type="application/json",
    )


@pytest.mark.django_db
@pytest.mark.parametrize("annotatable", INVALID_ANNOTATABLES)
def test_invalid_annotatable_is_a_400(
    anonymous_client: Client, annotatable: object,
) -> None:
    response = _post_annotatable(anonymous_client, annotatable)

    assert response.status_code == 400
    assert response.json() == {"reason": "Invalid annotatable provided!"}


@pytest.mark.django_db
@pytest.mark.parametrize("annotatable", [3, {}])
def test_refused_annotatable_records_no_usage(
    user_client: Client, annotatable: object,
) -> None:
    user = User.objects.get(email="user@example.com")
    quota_before = model_to_dict(UserQuota.get_or_create_for(user=user))

    response = _post_annotatable(user_client, annotatable)

    assert response.status_code == 400
    assert not AlleleQuery.objects.filter(owner=user).exists()
    assert model_to_dict(UserQuota.get_or_create_for(user=user)) == \
        quota_before
