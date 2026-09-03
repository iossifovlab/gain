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
from gain.annotation.annotation_factory import load_pipeline_from_yaml
from gain.genomic_resources.repository import GenomicResourceRepo
from rest_framework.test import APIClient

from web_annotation.editor.views import EditorMixin

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


#: The types the editor offers, read from the helper the endpoint serves
#: so that a tenth type is a case here the day it is added -- and, having
#: no pipeline or allowlist below, a failing one rather than a silent gap.
ANNOTATOR_TYPES = EditorMixin()._get_annotator_types()

#: Keys the framework writes into every annotator's parameters and marks
#: as read.  Not a parameter a user writes, so neither offered nor
#: allowlisted: discounted once, before the comparison.
INJECTED_PARAMETERS = frozenset({"work_dir"})

#: Per type, the parameters the annotator reads that the editor
#: deliberately does NOT offer, each for a stated reason.  Anything else
#: an annotator learns to read has to be added to its template, or added
#: here on purpose.  This is the inventory of what is unreachable from
#: the UI, which is why an entry without a reason is not acceptable.
DELIBERATELY_NOT_OFFERED: dict[str, frozenset[str]] = {
    "position_score_annotator": frozenset(),
    "allele_score_annotator": frozenset(),
    "gene_score_annotator": frozenset(),
    "gene_set_annotator": frozenset({
        # Read by the framework's input-annotatable decorator, which probes
        # every annotator for the key.  A gene-list annotator consumes no
        # annotatable, so there is nothing for the form to offer.
        "input_annotatable",
    }),
    "fragment_score_annotator": frozenset({
        # The deprecated spelling of `fragment_filter`.  Still accepted,
        # but the template emits the vocabulary worth writing today
        # (gain#471), so offering it would hand out the spelling being
        # retired.
        "cnv_filter",
    }),
    "effect_annotator": frozenset({
        # Not documented in the annotation infrastructure docs; offering
        # an undocumented knob in the form is premature.  Document, then
        # offer.
        "promoter_len",
    }),
    "simple_effect_annotator": frozenset(),
    "liftover_annotator": frozenset(),
    "normalize_allele_annotator": frozenset(),
}

_GENE_LIST_UPSTREAM = """
- effect_annotator:
    gene_models: t4c8/t4c8_genes
    genome: t4c8/t4c8_genome
    attributes:
    - name: gene_list
      internal: true
"""

#: Per type, the smallest pipeline that builds against the test GRR,
#: spelled the way the template EMITS it (what a pipeline saved from the
#: editor contains).  The last annotator is the one under test.
MINIMAL_PIPELINES: dict[str, str] = {
    "position_score_annotator": """
- position_score_annotator:
    resource_id: scores/pos1
""",
    "allele_score_annotator": """
- allele_score:
    resource_id: scores/allele1
""",
    "gene_score_annotator": _GENE_LIST_UPSTREAM + """
- gene_score_annotator:
    resource_id: t4c8/gene_scores/t4c8_score
    input_gene_list: gene_list
""",
    "gene_set_annotator": _GENE_LIST_UPSTREAM + """
- gene_set_annotator:
    resource_id: t4c8/gene_sets/main
    input_gene_list: gene_list
""",
    "fragment_score_annotator": """
- fragment_score:
    resource_id: cnv_collections/test_collection
""",
    "effect_annotator": """
- effect_annotator:
    gene_models: t4c8/t4c8_genes
    genome: t4c8/t4c8_genome
""",
    "simple_effect_annotator": """
- simple_effect_annotator:
    gene_models: t4c8/t4c8_genes
""",
    "liftover_annotator": """
- liftover_annotator:
    chain: liftover/mock
    source_genome: t4c8/t4c8_genome
    target_genome: t4c8/t4c8_genome
""",
    "normalize_allele_annotator": """
- normalize_allele_annotator:
    genome: t4c8/t4c8_genome
""",
}


@pytest.mark.django_db
@pytest.mark.parametrize("annotator_type", ANNOTATOR_TYPES)
def test_the_template_offers_every_parameter_the_annotator_reads(
    client: APIClient, test_grr: GenomicResourceRepo, annotator_type: str,
) -> None:
    """The two declarations of an annotator's vocabulary agree.

    The other tests in this module assert that SPECIFIC keys are present,
    and the round-trip test compares a template against ITSELF, so a key
    missing from both the template and every assertion leaves them all
    green.  This compares the two SETS instead, so a parameter added to an
    annotator and forgotten in its template is caught without anyone
    having to remember to assert it.

    What it does NOT catch: `get_used_keys` records the keys actually
    read, and this builds a MINIMAL pipeline, so a parameter read only
    when some sibling key is present would be invisible to it.  Every read
    in every annotator here is unconditional today -- deliberately, since
    `info.parameters` refuses a key nobody read -- which is what makes the
    comparison total.  A future conditional read would quietly narrow it.
    """
    assert annotator_type in MINIMAL_PIPELINES, (
        f"{annotator_type} has no minimal pipeline here, so its template "
        f"is compared against nothing -- add one")
    assert annotator_type in DELIBERATELY_NOT_OFFERED, (
        f"{annotator_type} has no allowlist entry here -- add one, even "
        f"if empty, so its gaps are a decision and not an omission")

    template = _config_template(client, annotator_type)
    offered = set(template) - {"annotator_type", "documentation_url"}

    pipeline = load_pipeline_from_yaml(
        MINIMAL_PIPELINES[annotator_type], test_grr)
    accepted = pipeline.annotators[-1].get_info().parameters.get_used_keys()
    accepted = accepted - INJECTED_PARAMETERS

    assert offered <= accepted, (
        f"the editor offers {sorted(offered - accepted)}, which the "
        f"annotator does not read -- the form would post a key the "
        f"pipeline then refuses as an unused parameter")
    assert accepted - offered == DELIBERATELY_NOT_OFFERED[annotator_type], (
        f"the annotator reads {sorted(accepted - offered)} but the editor "
        f"template does not offer it, so it cannot be configured from the "
        f"UI. Add it to the template, or to DELIBERATELY_NOT_OFFERED with "
        f"a reason")


#: The simple effect template declares itself `effect_annotator`, so a
#: pipeline saved from its form reopens as the full effect annotator
#: (iossifovlab/gain#1169).  Strict, so fixing it there fails here and
#: this pin is removed with it rather than outliving the defect.
ROUND_TRIP_CASES = [
    pytest.param(
        annotator_type,
        marks=pytest.mark.xfail(
            strict=True,
            reason="iossifovlab/gain#1169: emits effect_annotator"),
    ) if annotator_type == "simple_effect_annotator" else annotator_type
    for annotator_type in ANNOTATOR_TYPES
]


@pytest.mark.django_db
@pytest.mark.parametrize("annotator_type", ROUND_TRIP_CASES)
def test_the_spelling_a_template_emits_resolves_to_the_same_template(
    client: APIClient, annotator_type: str,
) -> None:
    """What comes out of the endpoint has to be allowed back into it.

    A template answers to the editor's name for its type but declares
    itself under the spelling a saved pipeline will carry, so the two
    have to resolve to the same template -- or a pipeline saved from the
    editor reopens as something else, or not at all (iossifovlab/gain#959
    for the fragment score, #919 for the allele score).
    """
    emitted = _config_template(client, annotator_type)

    assert _config_template(client, emitted["annotator_type"]) == emitted
