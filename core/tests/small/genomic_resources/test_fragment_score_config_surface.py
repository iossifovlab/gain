# pylint: disable=W0621,C0116
"""Pin the configuration surface of the fragment score across its rename.

The Python names moved from ``CnvCollection`` to ``FragmentScore``
(gain#470); every string a user or a deployed GRR can type stayed exactly
where it was.  The two halves are pinned here together on purpose -- the
rename is only safe because the config surface did not move with it, and a
later change that "finishes the job" by renaming a ``type:`` value or an
entry-point key would break deployed resources this repository cannot grep.

gain#471 added the ``fragment_score`` spellings BESIDE these; the new half is
pinned in ``test_fragment_score_vocabulary``.  Nothing here was replaced --
these remain accepted permanently, and this module is what stops a later
"finish the rename" pass from dropping them.
"""
import importlib
import importlib.util
import pathlib
import textwrap
import tomllib

import pytest
from gain.annotation.annotatable import Region
from gain.annotation.annotation_config import AnnotationConfigParser
from gain.annotation.annotation_factory import load_pipeline_from_yaml
from gain.genomic_resources import get_resource_implementation_builder
from gain.genomic_resources.genomic_scores import (
    FragmentScore,
    build_score_from_resource,
)
from gain.genomic_resources.implementations.genomic_scores_impl import (
    FragmentScoreImplementation,
)
from gain.genomic_resources.repository import GenomicResourceRepo
from gain.genomic_resources.testing.builders import a_fragment_score, a_grr

LEGACY_RESOURCE_TYPE = "cnv_collection"

CORE_PYPROJECT = pathlib.Path(__file__).parents[3] / "pyproject.toml"


@pytest.fixture
def legacy_grr(tmp_path: pathlib.Path) -> GenomicResourceRepo:
    """A GRR with one fragment score declared the way deployed GRRs do."""
    return (
        a_grr()
        .with_resource(
            "fragments",
            a_fragment_score()
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


def test_legacy_resource_type_still_opens_and_reads(
    legacy_grr: GenomicResourceRepo,
) -> None:
    resource = legacy_grr.get_resource("fragments")
    assert resource.get_type() == LEGACY_RESOURCE_TYPE

    with FragmentScore(resource).open() as score:
        fragments = score.fetch_fragment_scores("1", 5, 60)

    assert fragments == [
        {"frequency": 0.02, "collection": "SSC"},
        {"frequency": 0.1, "collection": "AGRE"},
    ]


def test_legacy_resource_type_still_dispatches_in_build_score(
    legacy_grr: GenomicResourceRepo,
) -> None:
    score = build_score_from_resource(legacy_grr.get_resource("fragments"))
    assert isinstance(score, FragmentScore)


@pytest.mark.parametrize("group,key", [
    ("gain.genomic_resources.implementations", "cnv_collection"),
    ("gain.annotation.annotators", "cnv_collection"),
    ("gain.annotation.annotators", "cnv_collection_annotator"),
])
def test_legacy_entry_point_keys_are_declared_in_pyproject(
    group: str, key: str,
) -> None:
    """The declaration itself is pinned, not just what is installed.

    The two entry-point tests below resolve through
    ``importlib.metadata``, which reads the INSTALLED dist-info -- a build
    artefact that can lag the source by an editable install.  Renaming a
    key in ``core/pyproject.toml`` therefore leaves them green.  This
    reads the file the constraint is actually about.
    """
    with CORE_PYPROJECT.open("rb") as infile:
        pyproject = tomllib.load(infile)

    # Resolved by walking up from __file__, so confirm we landed on core's
    # pyproject and not the workspace root's -- a wrong path that still
    # parsed would make this test pass while pinning nothing.
    assert pyproject["project"]["name"] == "gain-core"

    assert key in pyproject["project"]["entry-points"][group]


def test_legacy_implementation_entry_point_key_still_resolves() -> None:
    builder = get_resource_implementation_builder(LEGACY_RESOURCE_TYPE)
    assert builder is FragmentScoreImplementation


@pytest.mark.parametrize("annotator_name", [
    "cnv_collection",
    "cnv_collection_annotator",
])
def test_legacy_annotator_entry_point_keys_still_build(
    annotator_name: str,
    legacy_grr: GenomicResourceRepo,
) -> None:
    pipeline = load_pipeline_from_yaml(
        f"- {annotator_name}: fragments", legacy_grr)

    with pipeline.open() as open_pipeline:
        attributes = open_pipeline.annotate(Region("1", 15, 60))

    assert attributes["count"] == 2


@pytest.mark.parametrize("annotator_name", [
    "cnv_collection",
    "cnv_collection_annotator",
])
def test_legacy_annotator_names_still_select_resources_by_wildcard(
    annotator_name: str,
    legacy_grr: GenomicResourceRepo,
) -> None:
    """Wildcard resolution maps the annotator name to the resource type.

    ``query_resources`` keys its annotator-name-to-resource-type map on
    names a user types in a pipeline config, so both legacy spellings are
    config surface too.  Nothing else fails if one of them is renamed:
    a wildcard simply stops matching, and every pipeline in the suite
    names its resource outright.
    """
    assert AnnotationConfigParser.query_resources(
        annotator_name, "*", legacy_grr) == ["fragments"]


def test_legacy_cnv_filter_parameter_is_still_honoured(
    legacy_grr: GenomicResourceRepo,
) -> None:
    pipeline = load_pipeline_from_yaml(
        textwrap.dedent("""
            - cnv_collection:
                resource_id: fragments
                cnv_filter: collection == "SSC"
        """),
        legacy_grr)

    with pipeline.open() as open_pipeline:
        attributes = open_pipeline.annotate(Region("1", 15, 60))

    assert attributes["count"] == 1


# The retired names are spelled out rather than derived, so that
# re-introducing any one of them -- as an alias, a shim or an accidental
# re-export -- fails here.
#
# What this pins is narrow: these particular PYTHON names no longer exist.
# It is not a claim that the old vocabulary is gone from the sources.  It
# survives deliberately wherever it spells a CONFIGURATION string -- the
# resource type dispatched in ``genomic_scores``, ``SCORE_TYPE`` in the
# test builders, the annotator names in ``annotation_config``, the
# ``cnv_filter`` parameter -- because deployed GRRs and user pipelines
# type those.  They are config, not leftovers; widening them is gain#471.
@pytest.mark.parametrize("module_name,symbol", [
    ("gain.genomic_resources.genomic_scores", "CNV"),
    ("gain.genomic_resources.genomic_scores", "CnvCollection"),
    ("gain.genomic_resources.genomic_scores",
     "build_cnv_collection_from_resource"),
    ("gain.genomic_resources.genomic_scores",
     "build_cnv_collection_from_resource_id"),
    ("gain.genomic_resources.implementations.genomic_scores_impl",
     "CnvCollectionImplementation"),
    ("gain.genomic_resources.testing.builders", "CnvCollectionBuilder"),
    ("gain.genomic_resources.testing.builders", "a_cnv_collection"),
])
def test_old_python_names_are_gone(module_name: str, symbol: str) -> None:
    """No aliases, shims or re-exports survive the rename (gain#470)."""
    module = importlib.import_module(module_name)
    assert not hasattr(module, symbol)


def test_old_annotator_module_is_gone() -> None:
    assert importlib.util.find_spec(
        "gain.annotation.cnv_collection_annotator") is None
