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
import collections
import importlib
import importlib.util
import logging
import pathlib
import pkgutil
import textwrap
import tomllib
from types import ModuleType

import pytest
import pytest_mock
from gain.annotation.annotatable import Region
from gain.annotation.annotation_config import AnnotationConfigParser
from gain.annotation.annotation_factory import load_pipeline_from_yaml
from gain.genomic_resources import (
    get_resource_implementation_builder,
    resource_types,
)
from gain.genomic_resources.cli import _create_contents_db, cli_manage
from gain.genomic_resources.genomic_scores import (
    FragmentScore,
    build_score_from_resource,
    fragment,
)
from gain.genomic_resources.implementations.genomic_scores_impl import (
    FragmentScoreImplementation,
)
from gain.genomic_resources.repository import (
    GenomicResourceProtocolRepo,
    GenomicResourceRepo,
)
from gain.genomic_resources.repository_factory import (
    build_resource_implementation,
)
from gain.genomic_resources.resource_types import (
    deprecated_spelling_message,
    equivalent_resource_types,
    reset_deprecation_notices,
)
from gain.genomic_resources.testing import (
    build_filesystem_test_protocol,
    convert_to_tab_separated,
    setup_directories,
)
from gain.genomic_resources.testing.builders import (
    FragmentScoreBuilder,
    a_fragment_score,
    a_grr,
)
from gain.task_graph.cli_tools import task_graph_run
from gain.task_graph.graph import TaskGraph
from gain.task_graph.sequential_executor import SequentialExecutor

#: This module IS the legacy half, so its tests are expected to provoke the
#: deprecation notice; ``deprecation_notices_are_owned`` in
#: ``core/tests/conftest.py`` fails any unmarked test that emits one, which
#: is what keeps the rest of the suite from drifting back onto the old
#: spellings.  The few tests below that pin the preferred half stay silent
#: on their own -- the marker permits a notice, it does not require one.
pytestmark = pytest.mark.legacy_vocabulary

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
        fragments = list(score.fetch_fragment_scores("1", 5, 60))

    assert fragments == [
        (10, 20, (0.02, "SSC")),
        (50, 100, (0.1, "AGRE")),
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
    mocker: pytest_mock.MockerFixture,
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

    # Counts how often the SEAM fires, not how many tasks ran.  The
    # property this test rests on is that a legacy resource is recognised
    # many times per sweep and announced once; a task count is only a proxy
    # for that, and a proxy that goes quiet the moment score construction
    # is memoised -- at which point the memo below could be deleted
    # entirely and every assertion here would still pass.
    recognitions: collections.Counter[str] = collections.Counter()
    # Patched on the submodule that CALLS it, not on the package facade:
    # since gain#902 the facade only re-exports names, so rebinding an
    # attribute there would leave `fragment`'s own global untouched and this
    # counter silently at zero.
    announce = fragment.warn_deprecated_spelling

    def counting_announce(*args: object, **kwargs: object) -> None:
        recognitions[str(kwargs["found_in"])] += 1
        announce(*args, **kwargs)  # type: ignore[arg-type]

    mocker.patch.object(
        fragment, "warn_deprecated_spelling", counting_announce)

    with caplog.at_level(logging.WARNING):
        for resource in grr.get_all_resources():
            impl = build_resource_implementation(resource)
            tasks = impl.create_statistics_build_tasks(region_size=25)
            graph = TaskGraph()
            graph.add_tasks(tasks)
            task_graph_run(graph, SequentialExecutor())

    # Each legacy resource is recognised repeatedly -- once per statistics
    # task that rebuilds its score -- so the single line each gets below is
    # the memo doing its job, not an accident of the sweep being short.
    assert all(
        count > 1
        for count in (
            recognitions[f"Resource '{resource_id}'"]
            for resource_id in ("old_one", "old_two")
        )
    ), dict(recognitions)

    # ... and the preferred-typed resource is never recognised at all.
    assert recognitions["Resource 'current'"] == 0

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

    # Compared whole rather than by `in` checks: the resource id alone
    # satisfies every substring assertion this could make, so dropping
    # `annotator_id` from the message -- the one thing that says WHICH
    # stanza to edit -- would not fail a test that only looked for
    # "fragments".
    assert deprecation_warnings(caplog, "annotator name", annotator_name) == [
        deprecated_spelling_message(
            "annotator name", annotator_name, preferred_name,
            found_in="Annotator A0 on resource 'fragments'"),
    ]


def test_each_annotator_on_one_resource_is_named_apart(
    modern_grr: GenomicResourceRepo,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Two legacy stanzas over one resource are two offenders.

    The resource id cannot discriminate them -- it is the same resource --
    so a message built without the annotator id would collapse both into
    one line under the announce-once rule, and the reader of a 30-annotator
    pipeline would be told to edit "fragments" with no way to find which
    stanza still says it.
    """
    with caplog.at_level(logging.WARNING):
        load_pipeline_from_yaml(
            textwrap.dedent("""
                - cnv_collection:
                    resource_id: fragments
                    attributes:
                    - name: count_one
                      source: count
                - cnv_collection:
                    resource_id: fragments
                    attributes:
                    - name: count_two
                      source: count
            """),
            modern_grr)

    assert deprecation_warnings(
        caplog, "annotator name", "cnv_collection") == [
        deprecated_spelling_message(
            "annotator name", "cnv_collection", "fragment_score",
            found_in=f"Annotator {annotator_id} on resource 'fragments'")
        for annotator_id in ("A0", "A1")
    ]


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
        return annotated  # ruff: ignore[unnecessary-assign]

    assert annotate(legacy_name) == annotate(preferred_name)


def test_building_and_opening_a_legacy_pipeline_warns_once_each(
    legacy_grr: GenomicResourceRepo,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Both offences in one pipeline are announced, once apiece."""
    with caplog.at_level(logging.WARNING):
        pipeline = load_pipeline_from_yaml(
            "- cnv_collection: fragments", legacy_grr)
        with pipeline.open():
            pass

    assert len(deprecation_warnings(
        caplog, "annotator name", "cnv_collection")) == 1
    assert len(deprecation_warnings(
        caplog, "resource type", LEGACY_RESOURCE_TYPE)) == 1


@pytest.mark.parametrize("record_count", [1, 25])
def test_annotating_a_record_never_announces(
    record_count: int,
    legacy_grr: GenomicResourceRepo,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The seams are pipeline build and resource open, never a record.

    A per-record warning would out-volume the annotation output itself,
    which is the noise argument that kept this vocabulary silent until now.

    Asserting SILENCE across the record loop rather than a stable total
    across two record counts, because the announce-once memo would flatten
    a per-record warning to one line either way: every record renders a
    byte-identical message, so a total of 1 is what a seam in
    ``_do_annotate`` produces too, and a test reading only the total passes
    whether the recognition sits in the constructor or in the record path.
    Forgetting what build and open announced makes the record loop the only
    thing that can put a message in ``caplog`` -- so a seam that moved into
    the loop shows up here as the first record announcing, no matter how
    the memo would have collapsed the rest.
    """
    pipeline = load_pipeline_from_yaml(
        "- cnv_collection: fragments", legacy_grr)

    with pipeline.open() as open_pipeline:
        # Both halves of "what came before does not count": `caplog`
        # collects for the whole test regardless of where `at_level` is
        # entered, and the memo would swallow a repeat of anything build
        # and open already said.
        caplog.clear()
        reset_deprecation_notices()
        with caplog.at_level(logging.WARNING):
            for _ in range(record_count):
                open_pipeline.annotate(Region("1", 15, 60))

    assert all_deprecation_warnings(caplog) == []


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


@pytest.fixture
def indexed_legacy_grr(
    tmp_path: pathlib.Path,
) -> GenomicResourceProtocolRepo:
    """A searchable GRR holding one LEGACY-typed fragment score.

    Indexed, because ``search_resources`` answers from the FTS index rather
    than by scanning -- and the index is where the type predicate is
    applied, in SQL, which no Python-level alias expansion can reach.
    Building the index opens the resource, so the setup announces the
    deprecation; that is why this lives here and not beside its
    preferred-typed mirror in ``test_fragment_score_vocabulary``.
    """
    setup_directories(
        tmp_path,
        {
            "fragments/legacy": {
                "genomic_resource.yaml": textwrap.dedent("""
                    type: cnv_collection
                    table:
                        filename: data.txt
                    scores:
                        - id: frequency
                          type: float
                          name: frequency
                """),
                "data.txt": convert_to_tab_separated("""
                    chrom  pos_begin  pos_end  frequency
                    1      10         20       0.02
                """),
            },
        },
    )
    cli_manage(["repo-manifest", "-R", str(tmp_path)])
    proto = build_filesystem_test_protocol(tmp_path, repair=False)
    _create_contents_db(proto)
    return GenomicResourceProtocolRepo(proto)


@pytest.mark.parametrize(
    "requested_type", [PREFERRED_RESOURCE_TYPE, LEGACY_RESOURCE_TYPE])
def test_searching_finds_a_legacy_typed_resource_under_either_spelling(
    indexed_legacy_grr: GenomicResourceProtocolRepo,
    requested_type: str,
) -> None:
    """The direction that was actually broken: stored old, asked new.

    A repository that has not migrated stores ``cnv_collection``; a user on
    a current GAIn asks for ``fragment_score``.  An exact ``type = ?``
    answers "none" rather than failing, which is a wrong answer rather than
    an error -- and stays wrong until the last legacy resource is gone.
    """
    resources = list(
        indexed_legacy_grr.search_resources(resource_type=requested_type))

    assert [r.get_id() for r in resources] == ["fragments/legacy"]


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


def _module_and_submodules(module_name: str) -> list[ModuleType]:
    """The named module, plus every module inside it if it is a package.

    A package's ``__init__`` holds only what it re-exports, so asking it
    alone stops guarding anything the moment the module becomes a package:
    the gain#902 split turned ``genomic_scores`` into one, and a ``CNV``
    re-added in ``fragment`` -- where it would be re-added -- is not visible
    from the facade.  Walked rather than listed so a module added later is
    covered without anyone remembering to come here.
    """
    module = importlib.import_module(module_name)
    return [module, *(
        importlib.import_module(f"{module_name}.{found.name}")
        for found in pkgutil.iter_modules(getattr(module, "__path__", []))
    )]


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
    offenders = [
        module.__name__
        for module in _module_and_submodules(module_name)
        if hasattr(module, symbol)
    ]
    assert offenders == [], (
        f"the retired name {symbol!r} is back in: {offenders}"
    )


def test_old_annotator_module_is_gone() -> None:
    assert importlib.util.find_spec(
        "gain.annotation.cnv_collection_annotator") is None


def test_the_announcement_memory_is_bounded(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """What has been announced is remembered, but not without limit.

    ``found_in`` is caller-supplied -- a resource id out of a repository,
    or an annotator id derived from a posted pipeline -- and the memo is a
    module global in a process that may serve requests for weeks.  Bounding
    it costs a duplicate line only past the cap, which no real repository
    reaches; leaving it unbounded would let whatever the process was asked
    to parse accumulate in it forever.
    """
    overflow = 16

    with caplog.at_level(logging.WARNING):
        for offender in range(resource_types._ANNOUNCEMENT_MEMORY + overflow):
            resource_types.warn_deprecated_spelling(
                logging.getLogger(__name__),
                "resource type", LEGACY_RESOURCE_TYPE,
                PREFERRED_RESOURCE_TYPE,
                found_in=f"Resource 'fragments{offender}'")

    assert len(all_deprecation_warnings(caplog)) == (
        resource_types._ANNOUNCEMENT_MEMORY + overflow)
    assert (
        len(resource_types._ANNOUNCED_DEPRECATIONS)
        == resource_types._ANNOUNCEMENT_MEMORY)


def test_an_evicted_offender_is_announced_again(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Eviction is what bounds the memo -- not a wholesale clear.

    Pins the direction of the eviction too: the FIRST offender announced is
    the first forgotten, so the memo keeps what it most recently said.
    """
    def announce(offender: str) -> None:
        resource_types.warn_deprecated_spelling(
            logging.getLogger(__name__),
            "resource type", LEGACY_RESOURCE_TYPE, PREFERRED_RESOURCE_TYPE,
            found_in=f"Resource '{offender}'")

    announce("first")
    for offender in range(resource_types._ANNOUNCEMENT_MEMORY):
        announce(f"filler{offender}")

    # "first" has been pushed out by now; the filler that arrived straight
    # after it has not.  Probed in that order on purpose -- announcing
    # "first" is itself an insertion, which evicts whatever is then oldest,
    # so asking about the survivor afterwards would evict it first and
    # report every entry as forgotten.
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        announce("filler0")
        announce("first")

    assert [
        message.split(" uses ")[0] for message in
        all_deprecation_warnings(caplog)
    ] == ["Resource 'first'"]
