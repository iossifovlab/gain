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

from web_annotation.messages import (
    DAE_INDEL_NOT_SUPPORTED,
    INVALID_ANNOTATABLE,
)
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
    {"location": "chr1:100", "variant": "garbage"},
    {"chrom": "chr1", "pos": 1, "variant": "garbage"},
    {"location": "chr1:100", "variant": "del(x)"},
    {"chrom": "chr1", "pos": 1, "variant": "ins(ac)"},
    # An ins/del variant does not excuse a malformed location or position.
    {"variant": "del(1)"},
    {"chrom": "chr1", "variant": "ins(A)"},
    {"location": 3, "variant": "del(1)"},
    {"location": "chr1", "variant": "del(1)"},
    {"chrom": "chr1", "pos": "abc", "variant": "del(1)"},
    {"chrom": "chr1", "pos": None, "variant": "ins(A)"},
    {"chrom": "chr1", "pos_beg": 1, "pos_end": 2, "cnv_type": 3},
]


#: Well-formed DAE-style indels: converting one needs a reference genome,
#: which a single-allele request does not name (iossifovlab/gain#1680).
DAE_INDELS = [
    {"location": "chr1:100", "variant": "del(1)"},
    {"chrom": "chr1", "pos": 1, "variant": "ins(AC)"},
]


def _post_annotatable(
    client: Client, annotatable: object,
    pipeline_id: str = "pipeline/test_pipeline",
) -> Any:
    return client.post(
        SINGLE_ALLELE_URL,
        {"pipeline_id": pipeline_id, "annotatable": annotatable},
        content_type="application/json",
    )


@pytest.mark.django_db
@pytest.mark.parametrize("annotatable", INVALID_ANNOTATABLES)
def test_invalid_annotatable_is_a_400(
    anonymous_client: Client, annotatable: object,
) -> None:
    response = _post_annotatable(anonymous_client, annotatable)

    assert response.status_code == 400
    assert response.json() == {"reason": INVALID_ANNOTATABLE}


def test_dae_indel_reason_is_not_the_generic_one() -> None:
    assert DAE_INDEL_NOT_SUPPORTED != INVALID_ANNOTATABLE


@pytest.mark.django_db
@pytest.mark.parametrize("annotatable", DAE_INDELS)
def test_dae_indel_is_refused_as_unsupported(
    anonymous_client: Client, annotatable: object,
) -> None:
    response = _post_annotatable(anonymous_client, annotatable)

    assert response.status_code == 400
    assert response.json() == {"reason": DAE_INDEL_NOT_SUPPORTED}


@pytest.mark.django_db
@pytest.mark.parametrize("annotatable", [3, {}, *DAE_INDELS])
def test_refused_annotatable_records_no_usage(
    user_client: Client, annotatable: object,
) -> None:
    user = User.objects.get(email="user@example.com")
    quota_before = model_to_dict(UserQuota.get_or_create_for(user=user))

    # No such pipeline: refusing after the pipeline lookup would be a 404.
    response = _post_annotatable(
        user_client, annotatable, pipeline_id="no/such_pipeline",
    )

    assert response.status_code == 400
    assert not AlleleQuery.objects.filter(owner=user).exists()
    assert model_to_dict(UserQuota.get_or_create_for(user=user)) == \
        quota_before
