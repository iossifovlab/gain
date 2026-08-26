# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""The retired ``np_score`` resource type, and what a holder is told.

``np_score`` was deprecated for 21 months and removed in GAIn 2026.8.5
(gain#920, announced in gain#781/#918).  What these tests pin is not that
it stopped working -- deleting the entry point does that on its own -- but
that a repository still declaring it gets told *what to write instead*.

The bare failure the removal produces for free is
``unsupported resource implementation type <np_score>``, which names no
replacement.  That is the failure mode these tests exist to prevent, at
each of the seams a legacy resource is reached through -- the score
factory, the implementation builder, an annotation pipeline, and
``AlleleScore`` itself.  Four, because there is no single seam they all
pass through; ADR 0011 records the same for the fragment-score warning.

The migration is not a rename.  ``np_score`` defaulted to substitutions
mode and ``allele_score`` defaults to alleles mode, so a holder who only
swaps the type string silently changes how the score reads.  Every message
therefore has to carry ``allele_score_mode: substitutions`` as well as the
replacement type, and each test below asserts both halves rather than a
loose "mentions allele_score".

That the guidance is *true* -- that an ``allele_score`` reads in alleles
mode by default and in substitutions mode when the key says so -- is pinned
by ``test_allele_score_mode_defaults_to_alleles`` and
``test_allele_score_mode_substitutions_config`` in ``test_allele_score.py``.
They are the reason those two behaviours are not re-asserted here: if
either changed, the refusal below would be handing out a migration that
does not work, and those tests fail first.
"""
import pytest
from gain.annotation.annotation_config import (
    AnnotationConfigurationError,
)
from gain.annotation.annotation_factory import load_pipeline_from_yaml
from gain.genomic_resources.genomic_scores import (
    AlleleScore,
    build_score_from_resource,
)
from gain.genomic_resources.repository_factory import (
    build_resource_implementation,
)
from gain.genomic_resources.testing import (
    build_inmemory_test_repository,
    build_inmemory_test_resource,
)

#: What the removal produces on its own, once the entry point is gone.
#:
#: Named here rather than described, because it is the defect these tests
#: guard against: a refusal that says GAIn does not recognise the type
#: reads, to a holder, as "wrong GAIn" rather than "edit your YAML".
_UNHELPFUL_REFUSAL = "unsupported resource implementation type"

_RETIRED_NP_SCORE_RESOURCE = {
    "genomic_resource.yaml": """
        type: np_score
        table:
            filename: data.mem
            reference:
              name: reference
            alternative:
              name: alternative
        scores:
            - id: cadd_raw
              name: s1
              type: float
              desc: ""
    """,
    "data.mem": """
        chrom  pos_begin reference  alternative  s1
        1      10        A          G            0.02
        1      10        A          C            0.03
    """,
}


def test_building_a_score_from_a_np_score_resource_names_the_replacement(
) -> None:
    resource = build_inmemory_test_resource(_RETIRED_NP_SCORE_RESOURCE)

    with pytest.raises(ValueError) as excinfo:
        build_score_from_resource(resource)

    message = str(excinfo.value)
    assert "np_score" in message
    assert "write 'allele_score' instead" in message
    # The mode guidance is the half a plain rename loses, so it is asserted
    # separately: a message naming only the replacement type would satisfy
    # the line above while still leading the reader into a silent change.
    assert "allele_score_mode: substitutions" in message


def test_building_an_implementation_from_a_np_score_resource_guides_too(
) -> None:
    """``grr_manage`` reaches a resource here, not through the score factory.

    A repository-wide sweep builds an implementation per resource, so this
    is the seam a holder actually meets first when they run ``grr_manage``
    over an unmigrated repository.  ADR 0011 records that there is no outer
    seam covering both routes, which is why the rule is asserted at each.
    """
    resource = build_inmemory_test_resource(_RETIRED_NP_SCORE_RESOURCE)

    with pytest.raises(ValueError) as excinfo:
        build_resource_implementation(resource)

    message = str(excinfo.value)
    assert _UNHELPFUL_REFUSAL not in message
    assert "write 'allele_score' instead" in message
    assert "allele_score_mode: substitutions" in message


def test_an_annotation_pipeline_naming_a_np_score_resource_is_guided_too(
) -> None:
    """The seam a user is most likely to meet, and the easiest to regress.

    An annotator resolves its resource through ``get_genomic_resource``,
    which checks the declared type against the set the annotator accepts
    before anything builds a score.  That check raises an error of its
    own, so narrowing its set without guarding it first would swap this
    migration message for a bare "requires 'resource_id' to point to a
    resource of type {'allele_score'}" -- true, and useless to someone
    holding a resource that used to work.
    """
    repo = build_inmemory_test_repository(
        {"retired": _RETIRED_NP_SCORE_RESOURCE})

    with pytest.raises(AnnotationConfigurationError) as excinfo:
        load_pipeline_from_yaml(
            "- allele_score:\n    resource_id: retired\n", repo)

    message = str(excinfo.value)
    assert "write 'allele_score' instead" in message
    assert "allele_score_mode: substitutions" in message


def test_constructing_an_allele_score_from_a_np_score_resource_is_refused(
) -> None:
    """The innermost seam, reached without either factory above.

    ``AlleleScore(resource)`` is public and is what the two factories end
    up calling, so a guard only on the factories would leave the class
    itself accepting a resource it can no longer read correctly -- it used
    to answer such a resource in substitutions mode, and after the removal
    it would answer in alleles mode instead.
    """
    resource = build_inmemory_test_resource(_RETIRED_NP_SCORE_RESOURCE)

    with pytest.raises(ValueError) as excinfo:
        AlleleScore(resource)

    message = str(excinfo.value)
    assert "write 'allele_score' instead" in message
    assert "allele_score_mode: substitutions" in message
