# pylint: disable=W0621,C0116
"""The annotation editor speaks the ``fragment_score`` vocabulary.

gain#471 widens the accepted configuration vocabulary; the editor is where
a user meets it, so what this API offers is what most people will ever
write.  It offers the new spelling and keeps accepting the old.

Exercised through the HTTP endpoints rather than the view classes: the
enumerations are private helpers, and what matters is what a browser is
told.
"""
import textwrap
from typing import Any

import pytest
from gain.annotation.annotation_factory import load_pipeline_from_yaml
from gain.genomic_resources.testing import (
    build_inmemory_test_repository,
    convert_to_tab_separated,
)
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
def test_the_spelling_the_template_emits_is_a_spelling_it_accepts(
    client: APIClient,
) -> None:
    """What comes out of this endpoint has to be allowed back into it.

    The template answers to ``fragment_score_annotator`` but declares
    itself ``fragment_score``, so a pipeline saved from the editor names
    the spelling the endpoint used to refuse with a 500 -- the endpoint
    rejecting its own output (iossifovlab/gain#959).  The allele score
    template had the identical gap, closed by iossifovlab/gain#919.
    """
    emitted = _config_template(client, "fragment_score_annotator")

    assert _config_template(client, emitted["annotator_type"]) == emitted


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
    fragment_score``, but an unmigrated GRR declares ``cnv_collection``.
    An exact-equality filter would therefore offer the user an empty list
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
    against the resource's own.  The template now says ``fragment_score``
    while a legacy-typed resource says ``cnv_collection``, so an equality
    match returns an empty ``configs`` -- while ``default`` still names an
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


@pytest.mark.django_db
def test_the_template_offers_both_overlap_fractions(
    client: APIClient,
) -> None:
    """The editor can express the two thresholds (gain#1125).

    The annotator accepting a parameter is only half of it: this template
    is what the editor renders as the configuration form, so a key missing
    here is a capability that works in a hand-written pipeline and over
    the API but cannot be reached from the UI.

    Typed as ``string`` rather than as a number of its own: the UI's
    ``fieldType`` union is ``resource | string | bool | attribute`` and the
    form template branches on exactly those, so a ``float`` field_type
    would match no branch and render as NOTHING -- the same invisibility
    as omitting the key, reached the long way round.  The annotator reads
    a numeric string as the number it spells.
    """
    template = _config_template(client, "fragment_score_annotator")

    assert template["min_region_overlap_fraction"]["field_type"] == "string"
    assert template["min_region_overlap_fraction"]["optional"] is True
    assert template["min_fragment_overlap_fraction"]["field_type"] == "string"
    assert template["min_fragment_overlap_fraction"]["optional"] is True


#: Parameters the annotator reads that the editor deliberately does NOT
#: offer, each for a stated reason.  Anything else the annotator learns to
#: read has to be added to the template, or added here on purpose.
DELIBERATELY_NOT_OFFERED = {
    # The deprecated spelling of `fragment_filter`.  Still accepted, but
    # the template emits the vocabulary worth writing today (gain#471), so
    # offering it in the form would hand out the spelling being retired.
    "cnv_filter",
    # Injected by the framework, not written by a user.
    "work_dir",
}

FRAGMENT_GRR_CONTENT = {
    "fragments": {
        "genomic_resource.yaml": textwrap.dedent("""
            type: fragment_score
            table:
              filename: data.mem
            scores:
            - id: frequency
              name: frequency
              type: float
              desc: a population frequency
        """),
        "data.mem": convert_to_tab_separated("""
           chrom  pos_begin  pos_end  frequency
           1      10         20       0.02
        """),
    },
}

MINIMAL_FRAGMENT_PIPELINE = textwrap.dedent("""
    - fragment_score:
        resource_id: fragments
""")


@pytest.mark.django_db
def test_the_template_offers_every_parameter_the_annotator_reads(
    client: APIClient,
) -> None:
    """The two declarations of this annotator's vocabulary agree.

    A gain annotator parameter is declared TWICE: once by the constructor
    reading it -- `info.parameters` refuses a key nobody read, so the read
    IS the declaration -- and once in the hand-maintained template this
    module tests, which is what the editor renders as the configuration
    form.  Skip the second and the parameter works in a hand-written
    pipeline and over the API but is invisible in the UI.

    Nothing else here catches that.  The other tests assert that SPECIFIC
    keys are present, and the round-trip one compares the template against
    ITSELF, so a key missing from both the template and every assertion
    leaves this file green.  This compares the two SETS instead, so a
    parameter added to the annotator and forgotten here is caught without
    anyone having to remember to assert it.

    What it does NOT catch: `get_used_keys` records the keys actually
    read, and this builds a MINIMAL pipeline, so a parameter read only
    when some sibling key is present would be invisible to it.  Every read
    in this annotator is unconditional today -- deliberately, since
    `info.parameters` refuses a key nobody read -- which is what makes the
    comparison total.  A future conditional read would quietly narrow it.
    """
    template = _config_template(client, "fragment_score_annotator")
    offered = set(template) - {"annotator_type", "documentation_url"}

    grr = build_inmemory_test_repository(FRAGMENT_GRR_CONTENT)
    pipeline = load_pipeline_from_yaml(MINIMAL_FRAGMENT_PIPELINE, grr)
    accepted = pipeline.annotators[0].get_info().parameters.get_used_keys()

    assert offered <= accepted, (
        f"the editor offers {sorted(offered - accepted)}, which the "
        f"annotator does not read -- the form would post a key the "
        f"pipeline then refuses as an unused parameter")
    assert accepted - offered == DELIBERATELY_NOT_OFFERED, (
        f"the annotator reads {sorted(accepted - offered)} but the editor "
        f"template does not offer it, so it cannot be configured from the "
        f"UI. Add it to the template, or to DELIBERATELY_NOT_OFFERED with "
        f"a reason")
