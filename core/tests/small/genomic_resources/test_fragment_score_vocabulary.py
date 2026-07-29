# pylint: disable=W0621,C0116
"""The ``fragment_score`` configuration vocabulary, accepted beside the old.

gain#470 renamed the Python surface and deliberately left every
configuration string alone.  gain#471 adds the new spellings *beside* the
old ones -- nothing is replaced, and nothing is deprecated.

The legacy half is pinned in ``test_fragment_score_config_surface``.  The
two modules are complementary and both must pass: this one would go green
if the old spellings were dropped, and that one would go green if the new
ones were never added.
"""
import pathlib
import textwrap
import tomllib

import pytest
from gain.annotation.annotatable import Region
from gain.annotation.annotation_config import (
    AnnotationConfigParser,
    AnnotationConfigurationError,
)
from gain.annotation.annotation_factory import load_pipeline_from_yaml
from gain.genomic_resources.genomic_scores import (
    FragmentScore,
    build_score_from_resource,
)
from gain.genomic_resources.implementations.genomic_scores_impl import (
    FragmentScoreImplementation,
    build_score_implementation_from_resource,
)
from gain.genomic_resources.repository import GenomicResourceRepo
from gain.genomic_resources.testing.builders import a_fragment_score, a_grr

FRAGMENT_SCORE_TYPE = "fragment_score"

CORE_PYPROJECT = pathlib.Path(__file__).parents[3] / "pyproject.toml"


@pytest.fixture
def modern_grr(tmp_path: pathlib.Path) -> GenomicResourceRepo:
    """A GRR whose fragment score declares the new resource type."""
    return (
        a_grr()
        .with_resource(
            "fragments",
            a_fragment_score()
            .with_resource_type(FRAGMENT_SCORE_TYPE)
            .with_score("frequency", "float")
            .with_score("collection", "str")
            .with_data("""
                chrom  pos_begin  pos_end  frequency  collection
                1      10         20       0.02       SSC
                1      50         100      0.1        AGRE
            """),
        )
        .build_repo(tmp_path)
    )


def test_new_resource_type_opens_and_reads(
    modern_grr: GenomicResourceRepo,
) -> None:
    resource = modern_grr.get_resource("fragments")
    assert resource.get_type() == FRAGMENT_SCORE_TYPE

    with FragmentScore(resource).open() as score:
        fragments = score.fetch_fragments("1", 5, 60)

    assert [(f.chrom, f.pos_begin, f.pos_end) for f in fragments] == [
        ("1", 10, 20), ("1", 50, 100),
    ]


def test_new_resource_type_dispatches_in_build_score(
    modern_grr: GenomicResourceRepo,
) -> None:
    score = build_score_from_resource(modern_grr.get_resource("fragments"))
    assert isinstance(score, FragmentScore)


def test_new_resource_type_builds_the_fragment_score_implementation(
    modern_grr: GenomicResourceRepo,
) -> None:
    impl = build_score_implementation_from_resource(
        modern_grr.get_resource("fragments"))
    assert isinstance(impl, FragmentScoreImplementation)


@pytest.mark.parametrize("group,key", [
    ("gain.genomic_resources.implementations", "fragment_score"),
    ("gain.annotation.annotators", "fragment_score"),
    ("gain.annotation.annotators", "fragment_score_annotator"),
])
def test_new_entry_point_keys_are_declared_in_pyproject(
    group: str, key: str,
) -> None:
    """The declaration, not the installed dist-info.

    Same reasoning as the legacy counterpart: ``importlib.metadata`` reads
    a build artefact that an editable install can leave stale, so a test
    resolving through it cannot fail when the source declaration is wrong.
    """
    with CORE_PYPROJECT.open("rb") as infile:
        pyproject = tomllib.load(infile)

    assert pyproject["project"]["name"] == "gain-core"
    assert key in pyproject["project"]["entry-points"][group]


@pytest.mark.parametrize("annotator_name", [
    "fragment_score",
    "fragment_score_annotator",
])
def test_new_annotator_names_build_and_annotate(
    annotator_name: str,
    modern_grr: GenomicResourceRepo,
) -> None:
    pipeline = load_pipeline_from_yaml(
        f"- {annotator_name}: fragments", modern_grr)

    with pipeline.open() as open_pipeline:
        attributes = open_pipeline.annotate(Region("1", 15, 60))

    assert attributes["count"] == 2


def test_fragment_filter_parameter_is_honoured(
    modern_grr: GenomicResourceRepo,
) -> None:
    pipeline = load_pipeline_from_yaml(
        textwrap.dedent("""
            - fragment_score:
                resource_id: fragments
                fragment_filter: collection == "SSC"
        """),
        modern_grr)

    with pipeline.open() as open_pipeline:
        attributes = open_pipeline.annotate(Region("1", 15, 60))

    assert attributes["count"] == 1


def test_configuring_both_filter_spellings_is_refused(
    modern_grr: GenomicResourceRepo,
) -> None:
    """Refused, not silently resolved.

    Both spellings mean the same parameter, so honouring one and dropping
    the other would apply a filter the config did not ask for -- and a
    fragment filter decides which fragments are counted, so the wrong
    choice is a wrong annotation rather than an error.  The message names
    both spellings; a user who has only ever seen one of them cannot
    otherwise tell what the conflict is.
    """
    pipeline_config = textwrap.dedent("""
        - fragment_score:
            resource_id: fragments
            fragment_filter: collection == "SSC"
            cnv_filter: collection == "AGRE"
    """)

    with pytest.raises(AnnotationConfigurationError) as excinfo:
        load_pipeline_from_yaml(pipeline_config, modern_grr)

    message = str(excinfo.value)
    assert "fragment_filter" in message
    assert "cnv_filter" in message


@pytest.fixture
def legacy_typed_grr(tmp_path: pathlib.Path) -> GenomicResourceRepo:
    """The same score, declared with the legacy resource type."""
    return (
        a_grr()
        .with_resource(
            "fragments",
            a_fragment_score().with_score("frequency", "float").with_data("""
                chrom  pos_begin  pos_end  frequency
                1      10         20       0.02
            """),
        )
        .build_repo(tmp_path)
    )


@pytest.mark.parametrize("annotator_name", [
    "fragment_score",
    "fragment_score_annotator",
    "cnv_collection",
    "cnv_collection_annotator",
])
@pytest.mark.parametrize("grr_fixture", ["modern_grr", "legacy_typed_grr"])
def test_every_annotator_name_wildcard_matches_either_resource_type(
    annotator_name: str,
    grr_fixture: str,
    request: pytest.FixtureRequest,
) -> None:
    """The two vocabularies are not two parallel worlds.

    A name and a type can be spelled independently -- a pipeline on the new
    name may point at a GRR still on the old type, which is exactly the
    state the deployed GRRs will be in until gain#469.  So all four
    annotator names must resolve against both resource types; keying the
    map on one spelling each would silently return no matches, and a
    wildcard that matches nothing annotates nothing rather than failing.
    """
    grr = request.getfixturevalue(grr_fixture)
    assert AnnotationConfigParser.query_resources(
        annotator_name, "*", grr) == ["fragments"]
