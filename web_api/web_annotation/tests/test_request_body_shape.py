# pylint: disable=W0621,C0114,C0116
"""A request body of the wrong shape is a 400, not an AssertionError.

These views used to open with ``assert isinstance(request.data, ...)``, so a
syntactically valid body that parsed to the wrong type -- a JSON array or
scalar on a JSON-object endpoint -- raised ``AssertionError``: an unhandled
500 on an anonymous endpoint (iossifovlab/gain#1650).
"""
import pytest
from django.test import Client

#: (url, error key the view's module uses for its 400s)
JSON_OBJECT_ENDPOINTS = [
    ("/api/editor/annotator_config", "error"),
    ("/api/editor/annotator_attributes", "error"),
    ("/api/editor/annotator_yaml", "error"),
    ("/api/editor/annotator_aggregators", "error"),
    ("/api/pipelines/load", "reason"),
    ("/api/single_allele/annotate", "reason"),
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
