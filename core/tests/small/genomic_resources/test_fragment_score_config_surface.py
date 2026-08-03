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
these remain accepted until ``2027.1.0`` (gain#538 deprecated them, gain#539
removes them), and this module is what stops a "finish the rename" pass from
dropping them early.  Each legacy spelling must also ANNOUNCE itself: every
surface below is pinned twice, once for what it still does and once for the
warning it now emits.
"""
import importlib
import importlib.util
import logging
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
from gain.genomic_resources.repository_factory import (
    build_resource_implementation,
)
from gain.genomic_resources.resource_types import (
    deprecated_spelling_message,
    equivalent_resource_types,
)
from gain.genomic_resources.testing.builders import (
    FragmentScoreBuilder,
    a_fragment_score,
    a_grr,
)
from gain.task_graph.cli_tools import task_graph_run
from gain.task_graph.graph import TaskGraph
from gain.task_graph.sequential_executor import SequentialExecutor

LEGACY_RESOURCE_TYPE = "cnv_collection"
PREFERRED_RESOURCE_TYPE = "fragment_score"

#: The release the legacy spellings stop working in (gain#539).  Every
#: warning must name it -- a deprecation that does not say when it bites is
#: an apology, not a notice.
REMOVAL_RELEASE = "2027.1.0"

CORE_PYPROJECT = pathlib.Path(__file__).parents[3] / "pyproject.toml"


def deprecation_warnings(
    caplog: pytest.LogCaptureFixture, surface: str, legacy_spelling: str,
) -> list[str]:
    """Return the messages warning about one legacy spelling of one surface.

    Filtered by surface as well as spelling: ``cnv_collection`` is both a
    resource type and an annotator name, and a pipeline built on the legacy
    annotator name over a legacy-typed resource announces both offences.
    Each test here is about exactly one of them.
    """
    needle = f"deprecated {surface} '{legacy_spelling}'"
    return [
        message for message in all_deprecation_warnings(caplog)
        if needle in message
    ]


def all_deprecation_warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    """Return every fragment-score vocabulary warning captured.

    Recognised by the removal release rather than by the word "deprecated":
    unrelated warnings elsewhere in the pipeline use that word too, and a
    filter that caught them would make "the preferred spelling is silent"
    fail for reasons that have nothing to do with this vocabulary.
    """
    return [
        record.getMessage()
        for record in caplog.records
        if record.levelno == logging.WARNING
        and REMOVAL_RELEASE in record.getMessage()
    ]


@pytest.fixture
def legacy_grr(tmp_path: pathlib.Path) -> GenomicResourceRepo:
    """A GRR with one fragment score declared under the legacy type."""
    return (
        a_grr()
        .with_resource(
            "fragments",
            a_fragment_score()
            .with_resource_type(LEGACY_RESOURCE_TYPE)
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


@pytest.fixture
def modern_grr(tmp_path: pathlib.Path) -> GenomicResourceRepo:
    """The same GRR, declared under the preferred type.

    The control for every warning assertion below: it isolates the
    annotator-side surfaces from the resource-``type:`` one, and it is what
    "the preferred spelling stays silent" is checked against.
    """
    return (
        a_grr()
        .with_resource(
            "fragments",
            a_fragment_score()
            .with_resource_type(PREFERRED_RESOURCE_TYPE)
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


def test_legacy_resource_type_warns_once_naming_the_resource(
    legacy_grr: GenomicResourceRepo,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING):
        FragmentScore(legacy_grr.get_resource("fragments"))

    (message,) = deprecation_warnings(
        caplog, "resource type", LEGACY_RESOURCE_TYPE)
    assert "fragments" in message
    assert PREFERRED_RESOURCE_TYPE in message
    assert REMOVAL_RELEASE in message


def test_repository_sweep_warns_once_per_legacy_resource(
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Every offender is named exactly once, and only the offenders.

    Shaped like ``grr_manage repo-repair``, which does not stop at
    constructing an implementation: it builds and runs each resource's
    statistics tasks, and every min/max and histogram task calls
    ``build_score_implementation_from_resource`` again -- another
    ``FragmentScore`` over the same resource.  Both ways of getting the
    volume wrong are pinned here, because both make the notice
    unactionable: one warning per run hides every legacy resource after
    the first, and one per task buries all of them under thousands of
    identical lines naming a single offender.
    """
    def a_typed_fragment_score(resource_type: str) -> FragmentScoreBuilder:
        return (
            a_fragment_score()
            .with_resource_type(resource_type)
            .with_score("frequency", "float")
            .with_data("""
                chrom  pos_begin  pos_end  frequency
                1      10         20       0.02
                1      50         100      0.1
            """)
        )

    grr = (
        a_grr()
        .with_resource("old_one", a_typed_fragment_score(
            LEGACY_RESOURCE_TYPE))
        .with_resource("old_two", a_typed_fragment_score(
            LEGACY_RESOURCE_TYPE))
        .with_resource("current", a_typed_fragment_score(
            PREFERRED_RESOURCE_TYPE))
        .build_repo(tmp_path)
    )

    task_count = 0
    with caplog.at_level(logging.WARNING):
        for resource in grr.get_all_resources():
            impl = build_resource_implementation(resource)
            tasks = impl.create_statistics_build_tasks(region_size=25)
            task_count += len(tasks)
            graph = TaskGraph()
            graph.add_tasks(tasks)
            task_graph_run(graph, SequentialExecutor())

    # Each task re-opens its resource, so a per-open warning would show up
    # once per task.  Without several tasks per resource this test could
    # not tell the two volumes apart.
    assert task_count > 3

    # Sorted, not compared in order: `get_all_resources` walks the
    # repository in filesystem order, which is not the order they were
    # declared in.
    messages = deprecation_warnings(
        caplog, "resource type", LEGACY_RESOURCE_TYPE)
    assert sorted(messages) == [
        deprecated_spelling_message(
            "resource type",
            LEGACY_RESOURCE_TYPE, PREFERRED_RESOURCE_TYPE,
            found_in=f"Resource '{resource_id}'")
        for resource_id in ("old_one", "old_two")
    ]


def test_sweep_names_each_version_of_one_legacy_resource(
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Two versions of one id are two offenders, and are named apart.

    A repository legitimately carries several versions of the same
    ``resource_id`` -- that is what ``find_resource``'s "newest matching
    version" resolution exists for -- and each of them is a separate
    directory with its own config to migrate.  Naming them by the
    version-less id would collapse both into one warning under the
    announce-once-per-message rule, so the operator would see one line for
    two offenders and, after migrating one of them, a byte-identical line
    with nothing to distinguish what is left.
    """
    def a_legacy_fragment_score() -> FragmentScoreBuilder:
        return (
            a_fragment_score()
            .with_resource_type(LEGACY_RESOURCE_TYPE)
            .with_score("frequency", "float")
            .with_data("""
                chrom  pos_begin  pos_end  frequency
                1      10         20       0.02
            """)
        )

    grr = (
        a_grr()
        .with_resource("fragments(1.0)", a_legacy_fragment_score())
        .with_resource("fragments(2.0)", a_legacy_fragment_score())
        .build_repo(tmp_path)
    )

    with caplog.at_level(logging.WARNING):
        for resource in grr.get_all_resources():
            FragmentScore(resource)

    messages = deprecation_warnings(
        caplog, "resource type", LEGACY_RESOURCE_TYPE)
    assert sorted(messages) == [
        deprecated_spelling_message(
            "resource type",
            LEGACY_RESOURCE_TYPE, PREFERRED_RESOURCE_TYPE,
            found_in=f"Resource '{full_id}'")
        for full_id in ("fragments(1.0)", "fragments(2.0)")
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


@pytest.mark.parametrize("annotator_name,preferred_name", [
    ("cnv_collection", "fragment_score"),
    ("cnv_collection_annotator", "fragment_score_annotator"),
])
def test_legacy_annotator_name_warns_once_naming_its_replacement(
    annotator_name: str,
    preferred_name: str,
    modern_grr: GenomicResourceRepo,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING):
        load_pipeline_from_yaml(f"- {annotator_name}: fragments", modern_grr)

    (message,) = deprecation_warnings(
        caplog, "annotator name", annotator_name)
    assert "fragments" in message
    assert f"write '{preferred_name}' instead" in message
    assert REMOVAL_RELEASE in message


@pytest.mark.parametrize("legacy_name,preferred_name", [
    ("cnv_collection", "fragment_score"),
    ("cnv_collection_annotator", "fragment_score_annotator"),
])
def test_legacy_annotator_name_annotates_identically_to_its_replacement(
    legacy_name: str,
    preferred_name: str,
    modern_grr: GenomicResourceRepo,
) -> None:
    """The deprecation announces; it does not change an answer."""
    def annotate(annotator_name: str) -> dict:
        pipeline = load_pipeline_from_yaml(
            f"- {annotator_name}: fragments", modern_grr)
        # Bound and returned outside the `with`, which ruff reads as a
        # redundant temporary and mypy requires: the pipeline's `__exit__`
        # is typed as able to suppress, so a `return` inside the body leaves
        # a path that falls off the end of the function.
        with pipeline.open() as open_pipeline:
            annotated = open_pipeline.annotate(Region("1", 15, 60))
        return annotated  # noqa: RET504

    assert annotate(legacy_name) == annotate(preferred_name)


@pytest.mark.parametrize("record_count", [1, 25])
def test_warning_count_does_not_grow_with_annotated_records(
    record_count: int,
    legacy_grr: GenomicResourceRepo,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The seams are pipeline build and resource open, never a record.

    A per-record warning would out-volume the annotation output itself,
    which is the noise argument that kept this vocabulary silent until now.
    """
    with caplog.at_level(logging.WARNING):
        pipeline = load_pipeline_from_yaml(
            "- cnv_collection: fragments", legacy_grr)
        with pipeline.open() as open_pipeline:
            for _ in range(record_count):
                open_pipeline.annotate(Region("1", 15, 60))

    assert len(deprecation_warnings(
        caplog, "annotator name", "cnv_collection")) == 1
    assert len(deprecation_warnings(
        caplog, "resource type", LEGACY_RESOURCE_TYPE)) == 1


def test_preferred_spellings_emit_no_deprecation_warning(
    modern_grr: GenomicResourceRepo,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING):
        pipeline = load_pipeline_from_yaml(
            textwrap.dedent("""
                - fragment_score:
                    resource_id: fragments
                    fragment_filter: collection == "SSC"
            """),
            modern_grr)
        with pipeline.open() as open_pipeline:
            open_pipeline.annotate(Region("1", 15, 60))

    assert all_deprecation_warnings(caplog) == []


def test_querying_resources_by_type_emits_no_warning(
    legacy_grr: GenomicResourceRepo,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A type filter is a query, not an open.

    ``FRAGMENT_SCORE_TYPES`` is a membership test inside the repository
    layer's SQL predicate and inside wildcard resolution; warning from
    there would fire per query over resources the caller never opened --
    including resources the caller never went on to open at all.
    """
    with caplog.at_level(logging.WARNING):
        assert [
            resource.get_id()
            for resource in legacy_grr.get_all_resources()
            if resource.get_type() in equivalent_resource_types(
                PREFERRED_RESOURCE_TYPE)
        ] == ["fragments"]
        assert AnnotationConfigParser.query_resources(
            "fragment_score", "*", legacy_grr) == ["fragments"]

    assert all_deprecation_warnings(caplog) == []


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


def test_legacy_cnv_filter_parameter_warns_once_naming_its_replacement(
    modern_grr: GenomicResourceRepo,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING):
        load_pipeline_from_yaml(
            textwrap.dedent("""
                - fragment_score:
                    resource_id: fragments
                    cnv_filter: collection == "SSC"
            """),
            modern_grr)

    (message,) = deprecation_warnings(caplog, "parameter", "cnv_filter")
    assert "fragments" in message
    assert "write 'fragment_filter' instead" in message
    assert REMOVAL_RELEASE in message


# The retired names are spelled out rather than derived, so that
# re-introducing any one of them -- as an alias, a shim or an accidental
# re-export -- fails here.
#
# What this pins is narrow: these particular PYTHON names no longer exist.
# It is not a claim that the old vocabulary is gone from the sources.  It
# survives deliberately wherever it spells a CONFIGURATION string -- the
# resource type dispatched in ``genomic_scores``, ``with_resource_type``
# in the test builders, the annotator names in ``annotation_config``, the
# ``cnv_filter`` parameter -- because repositories and pipelines we do
# not control type those.  They are config, not leftovers; widening them
# is gain#471.
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
