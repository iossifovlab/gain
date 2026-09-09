# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""One refusal for a wrong resource type, whichever annotator was asked.

Every annotator that consumes a typed genomic resource states the types it
accepts once, as a class-level declaration, and resolves its resource
through the one shared helper.  What these tests pin is that the *reader*
sees that: the refusal for pointing an annotator at the wrong kind of
resource names the annotator, the type it found and the types it accepts,
and reads the same whichever of the five was configured (gain#1329).

Before this, the refusal depended on which of five shapes the annotator
used.  Two passed an accepted-types set to the shared helper; the other
three resolved the resource themselves and left the check to whatever
constructor eventually met it, so a gene-score annotator pointed at a
position score was told "invalid resource type <resource id>" -- naming
the resource, but neither the annotator that wanted it nor what it wanted.

These tests deliberately sit at the *builder* seam
(``build_<kind>_annotator``) rather than at a whole pipeline: it is the
annotator layer that this consistency belongs to, and a pipeline would
drag in each annotator's other required parameters -- an upstream
``input_gene_list`` provider for two of the five -- which have nothing to
do with the resource type.  ``test_gene_score_annotator`` already tests
the missing-``input_gene_list`` refusal at this same seam.
"""
import textwrap
from collections.abc import Callable

import pytest
from gain.annotation.allele_score_annotator import (
    build_allele_score_annotator,
)
from gain.annotation.annotation_config import AnnotatorInfo
from gain.annotation.annotation_pipeline import AnnotationPipeline, Annotator
from gain.annotation.annotator_base import AnnotatorBase
from gain.annotation.fragment_score_annotator import (
    build_fragment_score_annotator,
)
from gain.annotation.gene_score_annotator import build_gene_score_annotator
from gain.annotation.gene_set_annotator import build_gene_set_annotator
from gain.annotation.position_score_annotator import (
    build_position_score_annotator,
)
from gain.genomic_resources.repository import (
    GR_CONF_FILE_NAME,
    GenomicResourceRepo,
)
from gain.genomic_resources.testing import build_inmemory_test_repository

#: The opening of the wrong-type refusal, for the two tests that assert a
#: DIFFERENT fault does not produce it.  The wrong-type test itself
#: compares the whole rendered sentence instead, so it is not written in
#: terms of this.
_SHARED_REFUSAL = "requires 'resource_id' to point to a resource of type"

AnnotatorBuilder = Callable[[AnnotationPipeline, AnnotatorInfo], Annotator]

#: One case per annotator that declares accepted resource types: the
#: builder, the name it is configured as, a resource of a type it does
#: NOT accept, and the accepted types as the refusal should spell them.
#:
#: Two probes rather than one, because no single resource is the wrong
#: type for all five: the position-score annotator is pointed at the
#: allele score and everyone else at the position score.
_WRONG_TYPE_CASES = [
    pytest.param(
        build_position_score_annotator, "position_score_annotator",
        "an_allele_score", "allele_score", "'position_score'",
        id="position_score"),
    pytest.param(
        build_allele_score_annotator, "allele_score_annotator",
        "a_position_score", "position_score", "'allele_score'",
        id="allele_score"),
    pytest.param(
        build_fragment_score_annotator, "fragment_score_annotator",
        "a_position_score", "position_score",
        "'fragment_score', 'cnv_collection'",
        id="fragment_score"),
    pytest.param(
        build_gene_score_annotator, "gene_score_annotator",
        "a_position_score", "position_score", "'gene_score'",
        id="gene_score"),
    pytest.param(
        build_gene_set_annotator, "gene_set_annotator",
        "a_position_score", "position_score",
        "'gene_set_collection', 'gene_set'",
        id="gene_set"),
]

#: The same annotators, for the tests that do not need a probe resource.
#: Derived rather than re-typed: a sixth annotator added to one table and
#: not the other would silently produce a differently-named passing test.
_ALL_ANNOTATORS = [
    pytest.param(*case.values[:2], id=case.id) for case in _WRONG_TYPE_CASES
]


@pytest.fixture
def typed_repo() -> GenomicResourceRepo:
    """Two resources, each the wrong type for some annotator below.

    Configs are minimal on purpose: a wrong-type resource is refused
    before anything opens it, so nothing here has to be loadable.
    """
    return build_inmemory_test_repository({
        "a_position_score": {
            GR_CONF_FILE_NAME: textwrap.dedent("""
                type: position_score
                table:
                    filename: data.mem
                scores:
                    - id: s1
                      name: s1
                      type: float
                      desc: ""
            """),
            "data.mem": "chrom  pos_begin  s1\n1      10         0.1\n",
        },
        "an_allele_score": {
            GR_CONF_FILE_NAME: textwrap.dedent("""
                type: allele_score
                table:
                    filename: data.mem
                    reference:
                      name: reference
                    alternative:
                      name: alternative
                scores:
                    - id: s1
                      name: s1
                      type: float
                      desc: ""
            """),
            "data.mem":
                "chrom  pos_begin reference alternative s1\n"
                "1      10        A         G           0.1\n",
        },
    })


@pytest.mark.parametrize(
    ("builder", "annotator_name", "probe_id", "probe_type", "accepted_text"),
    _WRONG_TYPE_CASES)
def test_a_wrong_resource_type_is_refused_the_same_way_by_every_annotator(
    builder: AnnotatorBuilder,
    annotator_name: str,
    probe_id: str,
    probe_type: str,
    accepted_text: str,
    typed_repo: GenomicResourceRepo,
) -> None:
    """The consistency gain#1329 is about, asserted on all five.

    The whole rendered sentence is compared, not its parts: the accepted
    and found types are pinned as the QUOTED text the helper renders, and
    pinned as CONTIGUOUS with the phrase around them.  Loose ``in``
    checks are worthless here -- an annotator's name contains its
    resource type's spelling ("gene_score_annotator" contains
    "gene_score"), and the ``AnnotatorInfo`` repr this message embeds
    contains the probe's resource id, so ``"position_score" in message``
    passes without the refusal naming a type at all.

    That this refusal comes from the annotator layer and not from the
    score constructor behind it is pinned by the same comparison: only
    the annotator layer produces this sentence.  The constructors keep
    their own checks, which guard every other route to the score (ADR
    0011).
    """
    pipeline = AnnotationPipeline(typed_repo)
    info = AnnotatorInfo(annotator_name, [], {"resource_id": probe_id})

    with pytest.raises(ValueError) as excinfo:
        builder(pipeline, info)

    message = str(excinfo.value)
    assert annotator_name in message
    assert (
        f"requires 'resource_id' to point to a resource of type "
        f"{accepted_text}; resource of type <{probe_type}> found."
    ) in message


@pytest.mark.parametrize(("builder", "annotator_name"), _ALL_ANNOTATORS)
def test_an_empty_resource_id_is_a_configuration_error_not_a_missing_file(
    builder: AnnotatorBuilder,
    annotator_name: str,
    typed_repo: GenomicResourceRepo,
) -> None:
    """``resource_id:`` written but left blank is the config's fault.

    The gene-score and gene-set builders used to test the value's truth
    rather than the key's presence, so an empty one was refused as a
    missing parameter.  Resolving it instead reaches the repository and
    raises ``FileNotFoundError`` for a resource named "" -- a different
    exception CLASS, which anything catching ``ValueError`` around a
    pipeline build stops seeing, and a message that sends the reader
    looking for a resource they never named.
    """
    pipeline = AnnotationPipeline(typed_repo)
    info = AnnotatorInfo(annotator_name, [], {"resource_id": ""})

    with pytest.raises(ValueError) as excinfo:
        builder(pipeline, info)

    # The whole clause, not "resource_id" alone: the AnnotatorInfo repr
    # embedded in the message spells the parameter name too, so a bare
    # substring check passes whatever the refusal actually says.
    message = str(excinfo.value)
    assert (
        "needs a 'resource_id' parameter naming the resource the "
        "annotator reads."
    ) in message
    assert _SHARED_REFUSAL not in message


def test_an_annotator_that_declares_nothing_is_a_programming_error(
    typed_repo: GenomicResourceRepo,
) -> None:
    """The hazard the empty default creates, closed where it is created.

    ``ACCEPTED_RESOURCE_TYPES`` defaults to empty so the annotators with
    no typed resource -- most of them -- need not say so.  An annotator
    that calls the helper WITHOUT declaring would then match nothing, and
    the plain membership test would refuse a perfectly good resource with
    "requires 'resource_id' to point to a resource of type ;" -- an empty
    list of accepted types, blaming the config for a mistake in the
    annotator.  Refused as what it is instead.
    """
    pipeline = AnnotationPipeline(typed_repo)
    info = AnnotatorInfo(
        "undeclared_annotator", [], {"resource_id": "a_position_score"})

    with pytest.raises(ValueError) as excinfo:
        AnnotatorBase.resolve_resource(pipeline, info)

    message = str(excinfo.value)
    assert "ACCEPTED_RESOURCE_TYPES" in message
    assert "AnnotatorBase" in message
    # Not the config's fault, so not the config's message.
    assert _SHARED_REFUSAL not in message
