# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""The retired ``np_score`` annotator names, and what a config author is told.

Counterpart to ``test_np_score_removed.py``, which covers the resource
``type:``.  This file covers the other half: the *annotator* names a
pipeline YAML may use.  Both were retired in GAIn 2026.8.5 (gain#919 and
gain#920, announced in gain#781/#918).

What these tests pin is not that the names stopped working -- deleting the
entry points does that on its own -- but that a pipeline still naming one
gets told *what to write instead*.  The bare failure the removal produces
for free is ``unsupported annotator type: np_score``, which names no
replacement and reads, to a config author, as "wrong GAIn" rather than
"edit your YAML".

The resources these pipelines point at are deliberately valid and
``allele_score``-typed, so the annotator name is the only thing wrong.  A
test that pointed at a missing or retired resource could pass for the
wrong reason.
"""
import pytest
from gain.annotation.annotation_config import (
    AnnotationConfigurationError,
)
from gain.annotation.annotation_factory import (
    get_annotator_factory,
    get_available_annotator_types,
    load_pipeline_from_yaml,
    retired_annotator_message,
)
from gain.genomic_resources.repository import GR_CONF_FILE_NAME
from gain.genomic_resources.testing import (
    build_inmemory_test_repository,
)

#: What the removal produces on its own, once the entry points are gone.
#:
#: Named here rather than described, because it is the defect these tests
#: guard against.
_UNHELPFUL_REFUSAL = "unsupported annotator type"

_ALLELE_SCORE_RESOURCE = {
    GR_CONF_FILE_NAME: """
        type: allele_score
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


@pytest.fixture
def grr():
    return build_inmemory_test_repository({
        "a_score": _ALLELE_SCORE_RESOURCE,
    })


def test_np_score_annotator_name_is_refused_naming_its_replacement(
    grr,
) -> None:
    with pytest.raises(AnnotationConfigurationError) as excinfo:
        load_pipeline_from_yaml("""
            - np_score:
                resource_id: a_score
        """, grr)

    message = str(excinfo.value)
    assert retired_annotator_message("np_score") in message
    assert _UNHELPFUL_REFUSAL not in message


def test_np_score_annotator_suffix_is_refused_with_the_suffixed_name(
    grr,
) -> None:
    """The suffixed spelling is sent to the suffixed replacement.

    Both retired names would satisfy a "mentions allele_score" assertion,
    since one replacement is a prefix of the other.  This pins the mapping
    that makes each migration a one-word edit: the ``_annotator`` spelling
    must not be answered with the bare one.
    """
    with pytest.raises(AnnotationConfigurationError) as excinfo:
        load_pipeline_from_yaml("""
            - np_score_annotator:
                resource_id: a_score
        """, grr)

    assert retired_annotator_message("np_score_annotator") in str(
        excinfo.value)


def test_the_refusal_names_the_replacement_and_the_release() -> None:
    """The exact text, pinned once and whole.

    The tests around this one assert only that the message is *delivered*
    at a seam, through the same function that builds it -- so none of them
    would notice half the sentence going missing. This owns the content.

    The release is spelled out rather than read from
    ``RETIRED_VOCABULARY_REMOVAL_RELEASE``: it is the removal window
    announced in gain#918, so a change to it should fail here and be
    argued about, not follow the constant silently. Same for the
    replacements -- taking those from ``RETIRED_ANNOTATOR_NAMES`` would
    assert the map against itself.
    """
    assert retired_annotator_message("np_score") == (
        "annotator 'np_score' was removed in GAIn 2026.8.5; "
        "write 'allele_score' instead")
    assert retired_annotator_message("np_score_annotator") == (
        "annotator 'np_score_annotator' was removed in GAIn 2026.8.5; "
        "write 'allele_score_annotator' instead")


def test_a_name_gain_never_had_still_gets_the_generic_refusal() -> None:
    """A retired name and an invented one are different failures.

    ``np_score`` earns a migration because GAIn used to accept it.  A name
    it never had earns the generic message -- there is nothing to migrate
    to, and inventing a replacement would be a guess.  Without this, the
    retired branch could widen into a catch-all that answers every typo
    with "write 'allele_score' instead".
    """
    with pytest.raises(ValueError, match=_UNHELPFUL_REFUSAL) as excinfo:
        get_annotator_factory("no_such_annotator")

    assert "allele_score" not in str(excinfo.value)


def test_retired_names_are_not_offered_as_available_annotator_types() -> None:
    """Nothing may offer a name that cannot be used.

    ``get_available_annotator_types`` drives annotator pickers and the web
    API's validity checks, so a retired name left in it would be advertised
    and then refused.  What keeps it out is the entry-point registration
    being gone, not the refusal above -- the two are independent, and this
    is the one that pins the registration.

    The surviving spelling is asserted alongside as a control: without it
    an empty or broken registry would satisfy the absence on its own.
    """
    available = get_available_annotator_types()

    assert "allele_score" in available
    assert "allele_score_annotator" in available
    assert "np_score" not in available
    assert "np_score_annotator" not in available
