# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""What a reader gets when ``meta`` itself is not a mapping (gain#1004).

``meta:`` is free-form YAML, so a resource may declare the whole block as
a scalar -- ``meta: |`` followed by prose is the curator slip this is
named for.  gain#654 narrowed the one reader that looks *inside* it,
``GenomicResource.get_labels``; the readers of ``meta.description`` and
``meta.summary`` kept reaching into the raw config behind a truthiness
check that a non-empty string satisfies, and raised a bare
``AttributeError`` on it.

Reads never validate (ADR 0008), and one malformed resource must cost a
repository-wide walk only itself (gain#464, gain#503) -- so every ``meta``
read degrades to absent metadata, and the resource types that run the base
schema go on refusing a scalar ``meta`` at validation.  Validation
refuses; reading degrades.
"""
import logging
import pathlib
import textwrap

import pytest
from gain.genomic_resources.cli import _create_contents_db, cli_manage
from gain.genomic_resources.genomic_scores import build_score_from_resource
from gain.genomic_resources.repository import (
    GR_CONF_FILE_NAME,
    GenomicResource,
)
from gain.genomic_resources.repository_factory import (
    build_resource_implementation,
)
from gain.genomic_resources.testing import (
    build_filesystem_test_protocol,
    setup_directories,
)
from gain.genomic_resources.testing.builders import a_position_score

from .conftest import a_resource_whose_meta_is, captured_warnings


def _index_row(resource: GenomicResource) -> dict[str, str]:
    """The resource's FTS index row, keyed by the column it lands in."""
    header, row = build_resource_implementation(resource).collect_index_info()
    return dict(zip(header, row, strict=True))


def _assert_reported_once(
    caplog: pytest.LogCaptureFixture, type_name: str,
) -> None:
    """One report, naming the resource and the type it actually carries."""
    warnings = captured_warnings(caplog)
    assert len(warnings) == 1
    assert "scores/broken" in warnings[0]
    assert type_name in warnings[0]


# `basic` is the type that makes the degrade observable end to end: it
# runs no schema, so nothing refuses it earlier and the walk has to carry
# it all the way to an index row and a rendered page.
A_SCALAR_META = textwrap.dedent("""
    type: basic
    meta: |
        Some prose that is not a mapping.
    """)

A_MAPPING_META = textwrap.dedent("""
    type: basic
    meta:
        description: a well-formed description
        summary: a well-formed summary
    """)


def _a_repo_with_a_scalar_meta(root: pathlib.Path) -> pathlib.Path:
    """A GRR holding one sound resource and one whose ``meta`` is prose."""
    setup_directories(root, {
        "sound": {
            GR_CONF_FILE_NAME: A_MAPPING_META,
            "data.txt": "a\n",
        },
        "broken": {
            GR_CONF_FILE_NAME: A_SCALAR_META,
            "data.txt": "a\n",
        },
    })
    build_filesystem_test_protocol(root)
    return root


def test_repo_info_completes_over_a_resource_whose_meta_is_a_scalar(
    tmp_path: pathlib.Path,
) -> None:
    """The whole command must finish, not just the per-resource step.

    The repository index-info build reads every resource's summary from
    *outside* the per-resource error handling, so a single scalar ``meta``
    aborted ``grr_manage repo-info`` outright -- bare traceback, non-zero
    exit, no index written and no resource named.  That is the headline
    defect, and it is only observable at the command.
    """
    root = _a_repo_with_a_scalar_meta(tmp_path)

    cli_manage(["repo-info", "-R", str(root), "-j", "1"])

    assert (root / "index.html").exists()
    assert (root / "sound" / "index.html").exists()
    assert (root / "broken" / "index.html").exists()


def test_a_scalar_meta_resource_is_indexed_with_empty_metadata(
    tmp_path: pathlib.Path,
) -> None:
    """Degraded means indexed-as-absent, not skipped.

    A `basic` resource runs no schema, so nothing refuses it earlier and
    the index has to take it: the row is present, and the two columns the
    unreadable block would have filled are empty.
    """
    root = _a_repo_with_a_scalar_meta(tmp_path)
    proto = build_filesystem_test_protocol(root, repair=False)

    # The whole set, not `"broken" not in failed`: a guard that only ever
    # checks one member goes blind if the walk stops reaching the others.
    assert _create_contents_db(proto) == set()

    row = _index_row(proto.get_resource("broken"))
    assert row["description"] == ""
    assert row["summary"] == ""
    assert row["id"] == "broken"


def test_a_scalar_meta_costs_the_walk_only_itself(
    tmp_path: pathlib.Path,
) -> None:
    """The sound resource beside it keeps its own metadata intact."""
    root = _a_repo_with_a_scalar_meta(tmp_path)
    proto = build_filesystem_test_protocol(root, repair=False)

    row = _index_row(proto.get_resource("sound"))

    assert row["description"] == "a well-formed description"
    assert row["summary"] == "a well-formed summary"


# The truthy spellings are the ones that used to *raise*: the falsy ones
# (`""`, `[]`, `0`, `False`) were already read as absent unfixed, because
# the truthiness check and the `or {}` that used to guard these readers
# both happen to catch them.  So a value-only test over the falsy half
# would pass against the defect and pin nothing.
#
# They are parametrized here all the same, because the *report* is not
# vacuous for them: reading a malformed block as absent silently is what
# leaves the curator with nothing to act on, and `meta: []` is the same
# mistake as `meta: |` prose.  Nothing else pins that -- relaxing
# `get_meta`'s `is None` to a falsiness check keeps every other test in
# this file and in the gain#654 file green.
A_NON_MAPPING_META = [
    ("some prose", "str"),
    (["description"], "list"),
    (2019, "int"),
    (1.5, "float"),
    (True, "bool"),
    ("", "str"),
    ([], "list"),
    (0, "int"),
    (False, "bool"),
]


@pytest.mark.parametrize(("meta", "type_name"), A_NON_MAPPING_META)
def test_a_scalar_meta_reads_as_an_empty_description(
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
    meta: object,
    type_name: str,
) -> None:
    resource = a_resource_whose_meta_is(tmp_path, meta)

    with caplog.at_level(logging.WARNING):
        assert resource.get_description() == ""

    _assert_reported_once(caplog, type_name)


@pytest.mark.parametrize(("meta", "type_name"), A_NON_MAPPING_META)
def test_a_scalar_meta_reads_as_an_empty_summary(
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
    meta: object,
    type_name: str,
) -> None:
    resource = a_resource_whose_meta_is(tmp_path, meta)

    with caplog.at_level(logging.WARNING):
        assert resource.get_summary() == ""

    _assert_reported_once(caplog, type_name)


def test_get_summary_reports_a_malformed_block_once(
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Log volume is behaviour on a repository-wide walk.

    ``get_summary`` falls back to the description off the *same* narrowed
    block rather than re-entering ``get_description``.  Re-entering would
    report the same resource twice for one call, and this call happens
    once per resource in the index build -- so the noise is proportional
    to the repository, not to the mistake.

    Scoped to this one accessor on purpose: a caller reading two fields
    reports twice, once per accessor, and ``collect_index_info`` is such
    a caller (gain#1013).
    """
    resource = a_resource_whose_meta_is(tmp_path, "some prose")

    with caplog.at_level(logging.WARNING):
        resource.get_summary()

    assert len(captured_warnings(caplog)) == 1


@pytest.mark.parametrize("meta", [
    "some prose", ["description"], 2019, 1.5, True,
    [], 0, "", False,
    None, {}, {"description": "d", "summary": "s"},
])
def test_no_meta_read_ever_raises(
    tmp_path: pathlib.Path,
    meta: object,
) -> None:
    """The contract the docstring claims, over every shape YAML allows."""
    resource = a_resource_whose_meta_is(tmp_path, meta)

    assert isinstance(resource.get_meta(), dict)
    assert isinstance(resource.get_description(), str)
    assert isinstance(resource.get_summary(), str)
    assert isinstance(resource.get_labels(), dict)


def test_a_well_formed_meta_is_read_unchanged(
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The narrowing must be invisible to every sound resource."""
    resource = (
        a_position_score()
        .with_meta(summary="a summary", description="a description")
        .with_labels(domain="gene")
        .build_resource(tmp_path)
    )

    with caplog.at_level(logging.WARNING):
        assert resource.get_summary() == "a summary"
        assert resource.get_description() == "a description"
        assert resource.get_labels() == {"domain": "gene"}
        assert resource.get_meta() == {
            "summary": "a summary",
            "description": "a description",
            "labels": {"domain": "gene"},
        }

    assert captured_warnings(caplog) == []


def test_a_summary_absent_from_a_well_formed_meta_falls_back(
    tmp_path: pathlib.Path,
) -> None:
    """The pre-existing fall-back is behaviour, not an accident."""
    resource = (
        a_position_score()
        .with_meta(description="a description")
        .build_resource(tmp_path)
    )

    assert resource.get_summary() == "a description"


def test_validation_still_refuses_a_scalar_meta(
    tmp_path: pathlib.Path,
) -> None:
    """A tolerant read must not make the refusal tolerant too.

    The base schema declares ``meta`` a dict, so a resource type that runs
    it is refused for a scalar one.  This issue makes the *read* safe; it
    moves no validation and weakens none -- validation refuses, reading
    degrades.
    """
    resource = a_resource_whose_meta_is(tmp_path, "some prose")

    with pytest.raises(ValueError, match="Invalid configuration"):
        build_score_from_resource(resource)
