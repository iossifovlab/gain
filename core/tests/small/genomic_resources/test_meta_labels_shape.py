# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""What a reader gets when ``meta.labels`` is not a mapping (gain#654).

``meta.labels`` is free-form YAML, so a resource may declare it as a
scalar, a list or an int.  ``GenomicResource.get_labels`` narrows what it
finds instead of trusting it: a non-mapping reads as no labels, with a
warning naming the resource and the type it actually carries.  Reads never
validate (ADR 0008), and one malformed resource must not take down a walk
over the whole repository (gain#464, gain#503, ADR 0010).
"""
import logging
import pathlib

import pytest
from gain.genomic_resources.cli import _create_contents_db
from gain.genomic_resources.testing import build_filesystem_test_protocol
from gain.genomic_resources.testing.builders import a_grr, a_position_score

from .conftest import a_resource_whose_meta_is, captured_warnings


@pytest.mark.parametrize(("labels", "type_name"), [
    ("some text", "str"),
    (["domain", "gene"], "list"),
    (2019, "int"),
    (1.5, "float"),
    (True, "bool"),
    # Falsy non-mappings are the same curator mistake as their truthy
    # spellings, and must be reported rather than read silently as the
    # `{}` they happen to coincide with.
    ([], "list"),
    (0, "int"),
    ("", "str"),
    (False, "bool"),
])
def test_labels_declared_as_a_non_mapping_read_as_no_labels(
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
    labels: object,
    type_name: str,
) -> None:
    # Built through a GRR rather than as a lone resource: `build_resource`
    # gives the resource an EMPTY id, against which `id in message` holds
    # for every message ever written and pins nothing.
    resource = (
        a_grr()
        .with_resource(
            "scores/broken", a_position_score().with_raw_labels(labels))
        .build_repo(tmp_path)
        .get_resource("scores/broken")
    )

    with caplog.at_level(logging.WARNING):
        assert resource.get_labels() == {}

    warnings = captured_warnings(caplog)
    assert len(warnings) == 1
    assert "scores/broken" in warnings[0]
    assert type_name in warnings[0]


@pytest.mark.parametrize(("meta", "type_name"), [
    ("some text", "str"),
    (["labels"], "list"),
    (2019, "int"),
])
def test_a_meta_that_is_not_a_mapping_reads_as_no_labels(
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
    meta: object,
    type_name: str,
) -> None:
    # `meta:` is as free-form as `meta.labels:`, and a non-mapping there
    # reached this read the same way -- and crashed it with the same bare
    # AttributeError the issue is about, one level up.
    resource = a_resource_whose_meta_is(tmp_path, meta)

    with caplog.at_level(logging.WARNING):
        assert resource.get_labels() == {}

    warnings = captured_warnings(caplog)
    assert len(warnings) == 1
    assert "scores/broken" in warnings[0]
    assert type_name in warnings[0]


def test_a_well_formed_labels_mapping_is_read_unchanged(
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Free-form values -- a bool and an int alongside the strings -- are
    # the container's business, not the values' (gain#654 is only about
    # the container's own shape).
    resource = (
        a_position_score()
        .with_labels(domain="gene", perturbed=False, year=2019)
        .build_resource(tmp_path)
    )

    with caplog.at_level(logging.WARNING):
        assert resource.get_labels() == {
            "domain": "gene", "perturbed": False, "year": 2019}

    assert captured_warnings(caplog) == []


def test_an_explicit_yaml_null_labels_reads_as_no_labels_silently(
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # `labels:` with nothing after it is what ten resources in the
    # published GRRs carry; it already read as no labels and must keep
    # doing so, without a warning -- there is nothing wrong with it.
    resource = (
        a_position_score()
        .with_raw_labels(None)
        .build_resource(tmp_path)
    )

    with caplog.at_level(logging.WARNING):
        assert resource.get_labels() == {}

    assert captured_warnings(caplog) == []


def test_a_resource_with_no_meta_block_at_all_reads_as_no_labels(
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    resource = a_position_score().build_resource(tmp_path)

    with caplog.at_level(logging.WARNING):
        assert resource.get_labels() == {}

    assert captured_warnings(caplog) == []


def test_a_meta_block_without_labels_reads_as_no_labels(
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    resource = (
        a_position_score()
        .with_meta(summary="a summary")
        .build_resource(tmp_path)
    )

    with caplog.at_level(logging.WARNING):
        assert resource.get_labels() == {}

    assert captured_warnings(caplog) == []


def test_the_index_build_still_refuses_a_non_mapping_labels(
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A tolerant read must not make the refusal tolerant too.

    A resource type that runs the base schema -- ``labels`` is declared
    there as a nullable dict -- is refused for a malformed block, and the
    index build reports it against that one resource and indexes the rest
    (gain#464, gain#503). This issue makes the *read* safe; it moves no
    validation and weakens none.
    """
    (
        a_grr()
        .with_resource(
            "scores/aaa", a_position_score().with_labels(domain="alpha"))
        .with_resource(
            "scores/broken", a_position_score().with_raw_labels("some text"))
        .build_repo(tmp_path)
    )
    proto = build_filesystem_test_protocol(tmp_path, repair=False)

    with caplog.at_level(logging.ERROR):
        failed = _create_contents_db(proto)

    assert failed == {"scores/broken"}
    assert any(
        "scores/broken" in record.getMessage()
        for record in caplog.records
        if record.levelno == logging.ERROR
    )


@pytest.mark.parametrize("labels", [
    "some text", ["domain", "gene"], 2019, None, {}, {"domain": "gene"},
    [], 0, "", False,
])
def test_get_labels_never_raises(
    tmp_path: pathlib.Path,
    labels: object,
) -> None:
    resource = (
        a_position_score()
        .with_raw_labels(labels)
        .build_resource(tmp_path)
    )

    assert isinstance(resource.get_labels(), dict)


@pytest.mark.parametrize("meta", [
    "some text", ["labels"], 2019, 1.5, True, {}, {"labels": {"a": "b"}},
])
def test_get_labels_never_raises_on_a_malformed_meta_either(
    tmp_path: pathlib.Path,
    meta: object,
) -> None:
    # The guarantee the name claims has to hold for the level above the
    # one the issue is named for, or it is not the guarantee it says.
    resource = a_resource_whose_meta_is(tmp_path, meta)

    assert isinstance(resource.get_labels(), dict)
