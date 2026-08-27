# pylint: disable=W0621,C0116
"""What the editor tells someone who names an untemplated annotator.

GAIn registers more annotator types than the editor carries configuration
templates for: ``chrom_mapping`` and ``debug_annotator`` have no place in
the pipeline editor's UI, and several accepted spellings resolve to a
template reached under a different name.  Such a name is neither a typo
nor retired -- it is usable in a pipeline this endpoint simply cannot
draw a form for.

Before iossifovlab/gain#959 the endpoint answered it with an uncaught
``KeyError`` and a 500, which reads as a server fault for well-formed
input.  It now refuses in the same register as its sibling endpoints, and
says the true thing rather than claiming the type is unknown.

Exercised through the HTTP endpoint: the template chain is a private
helper, and what matters is what a client is told.
"""
import pytest
from django.test import Client
from gain.annotation.annotation_factory import get_available_annotator_types

from web_annotation.editor.views import _unavailable_annotator_message

ANNOTATOR_CONFIG_URL = "/api/editor/annotator_config"
ANNOTATOR_TYPES_URL = "/api/editor/annotator_types"


@pytest.fixture
def client() -> Client:
    # A regression here is an UNCAUGHT exception, which this client reports
    # as the 500 a browser would see rather than re-raising into the test.
    # Without it a reintroduced fault reads as an error, not a failed
    # status assertion, and the sweep below cannot record it at all.
    return Client(raise_request_exception=False)


@pytest.mark.django_db
def test_registered_but_untemplated_name_is_refused_not_crashed(
    client: Client,
) -> None:
    response = client.post(
        ANNOTATOR_CONFIG_URL,
        data={"annotator_type": "chrom_mapping"},
        content_type="application/json",
    )

    assert response.status_code == 400, response.content
    error = response.json()["error"]
    assert "chrom_mapping" in error
    # Compared against what production would say for a name GAIn never
    # had, rather than against a copy of that text: the point is that the
    # two refusals stay different, and a literal here would go on passing
    # if the unknown-name wording were reworded out from under it.
    assert error != _unavailable_annotator_message("chrom_mapping")


@pytest.mark.django_db
def test_the_endpoint_never_faults_for_a_registered_type(
    client: Client,
) -> None:
    """Asked about anything GAIn can build, the endpoint must not 500.

    Enumerated from the registry rather than listed by hand: the template
    chain is a fixed set of names, so registering a new annotator -- here
    or in a plugin -- silently adds a type this endpoint has never been
    asked about.  A hand-written list would keep passing while the new
    name faults.

    Reading the registry is also what could make this test vacuous, so
    the sweep is bracketed: an empty or unrecognisable registry, and an
    endpoint that refused every single type, both fail here rather than
    passing with an empty loop.
    """
    registered = get_available_annotator_types()
    assert "chrom_mapping" in registered
    assert "position_score_annotator" in registered

    statuses = {}
    for annotator_type in registered:
        response = client.post(
            ANNOTATOR_CONFIG_URL,
            data={"annotator_type": annotator_type},
            content_type="application/json",
        )
        statuses[annotator_type] = response.status_code

    assert not {
        name: code for name, code in statuses.items() if code >= 500
    }
    assert 200 in statuses.values()


@pytest.mark.django_db
def test_every_annotator_the_editor_offers_has_a_template(
    client: Client,
) -> None:
    """The dropdown must not offer a type the config endpoint refuses.

    The advertised list and the template chain are maintained separately,
    so nothing but this stops them drifting.  It is also what keeps the
    untemplated branch in ``ResourceAnnotators`` dormant -- that endpoint
    walks the same advertised list, and skips anything untemplated, so a
    drift would quietly shorten a resource's annotator list instead of
    failing.  Here it fails.
    """
    offered = client.get(ANNOTATOR_TYPES_URL)
    assert offered.status_code == 200, offered.content
    annotator_types = offered.json()
    assert annotator_types

    refused = {}
    for annotator_type in annotator_types:
        response = client.post(
            ANNOTATOR_CONFIG_URL,
            data={"annotator_type": annotator_type},
            content_type="application/json",
        )
        if response.status_code != 200:
            refused[annotator_type] = response.status_code

    assert not refused
