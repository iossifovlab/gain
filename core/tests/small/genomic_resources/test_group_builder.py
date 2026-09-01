# pylint: disable=C0116
"""Composing several GRRs into a group with the test-data builders.

A group of directory repositories, each published on its own host, is the
shape production actually deploys -- and the shape a test needs to prove
that a resource's advertised address comes from the child repository it
was found in rather than from one base url.
"""
import pathlib

import pytest
import yaml
from gain.genomic_resources.repository import GenomicResourceRepo
from gain.genomic_resources.repository_factory import (
    build_genomic_resource_repository,
)
from gain.genomic_resources.testing.builders import (
    ResourceValidationError,
    a_grr,
    a_position_score,
)
from gain.genomic_resources.testing.group_builder import a_grr_group


def test_each_child_of_a_group_advertises_its_own_host(
    tmp_path: pathlib.Path,
) -> None:
    # The same resource id exists in both children, so the address is only
    # right if it comes from the child the resource was found in.
    repo = (
        a_grr_group()
        .with_child(
            "main",
            a_grr()
            .with_resource("scores/pos1", a_position_score())
            .with_public_url("http://grr.example.org"))
        .with_child(
            "encode",
            a_grr()
            .with_resource("scores/pos1", a_position_score())
            .with_public_url("http://grr-encode.example.org/"))
        .build_repo(tmp_path)
    )

    main = repo.get_resource("scores/pos1", repository_id="main")
    encode = repo.get_resource("scores/pos1", repository_id="encode")

    assert main.get_public_url() == "http://grr.example.org/scores/pos1"
    assert encode.get_public_url() == \
        "http://grr-encode.example.org/scores/pos1"


def test_build_definition_writes_a_usable_group_grr_yaml(
    tmp_path: pathlib.Path,
) -> None:
    # A CLI tool is handed a definition *file*, so the whole group -- each
    # child's directory and advertised url -- has to survive the round
    # trip through the written yaml.
    definition = (
        a_grr_group()
        .with_child(
            "main",
            a_grr()
            .with_resource("scores/pos1", a_position_score())
            .with_public_url("http://grr.example.org"))
        .with_child(
            "encode",
            a_grr()
            .with_resource("scores/pos1", a_position_score())
            .with_public_url("http://grr-encode.example.org"))
        .build_definition(tmp_path, grr_id="my_group")
    )

    parsed = yaml.safe_load(definition.read_text())
    repo = build_genomic_resource_repository(file_name=str(definition))

    assert parsed["id"] == "my_group"
    assert parsed["type"] == "group"
    assert [child["id"] for child in parsed["children"]] == ["main", "encode"]
    assert repo.get_resource(
        "scores/pos1", repository_id="encode").get_public_url() == \
        "http://grr-encode.example.org/scores/pos1"
    # As for a single GRR, the definition must live OUTSIDE the directory
    # holding the children, or it would be walked as though it were one.
    assert not (tmp_path / "grr" / "grr.yaml").exists()


def test_each_child_realizes_into_its_own_directory(
    tmp_path: pathlib.Path,
) -> None:
    # Two children carrying the SAME resource id is the whole point of the
    # fixture, so they must not realize over each other.
    (
        a_grr_group()
        .with_child(
            "main", a_grr().with_resource("scores/pos1", a_position_score()))
        .with_child(
            "encode", a_grr().with_resource("scores/pos1", a_position_score()))
        .build_repo(tmp_path)
    )

    for child_id in ("main", "encode"):
        assert (tmp_path / child_id / "scores" / "pos1"
                / "genomic_resource.yaml").is_file()


def test_a_child_is_realized_exactly_as_a_standalone_grr_is(
    tmp_path: pathlib.Path,
) -> None:
    # A fixture must not change shape by being composed into a group: a
    # child is built through its own builder, so it is repaired like any
    # other GRR. An unrepaired one has no manifests and no content file,
    # which anything wrapping it in a cache or serving it would notice.
    grr = a_grr().with_resource("scores/pos1", a_position_score())

    grr.build_repo(tmp_path / "alone")
    a_grr_group().with_child("main", grr).build_repo(tmp_path / "grouped")

    def realized(root: pathlib.Path) -> set[str]:
        return {
            str(path.relative_to(root))
            for path in root.rglob("*") if path.is_file()
        }

    assert realized(tmp_path / "grouped" / "main") == \
        realized(tmp_path / "alone")
    assert ".CONTENTS.json.gz" in realized(tmp_path / "grouped" / "main")


def test_two_groups_advertising_different_hosts_do_not_collide(
    tmp_path: pathlib.Path,
) -> None:
    # Comparing two spellings of an advertised address is the comparison
    # this form exists to make, so it must not be the one shape it cannot
    # express. A child id is also its protocol id and callers look
    # resources up by it, so the url has to make the child's DIRECTORY
    # distinct instead -- the other half of the memo key.
    def a_group_advertising(public_url: str) -> GenomicResourceRepo:
        return (
            a_grr_group()
            .with_child(
                "main",
                a_grr()
                .with_resource("scores/pos1", a_position_score())
                .with_public_url(public_url))
            .build_repo(tmp_path)
        )

    first = a_group_advertising("http://one.example.org")
    second = a_group_advertising("http://two.example.org")

    assert first.get_resource(
        "scores/pos1", repository_id="main").get_public_url() == \
        "http://one.example.org/scores/pos1"
    assert second.get_resource(
        "scores/pos1", repository_id="main").get_public_url() == \
        "http://two.example.org/scores/pos1"


def test_a_duplicate_child_id_is_refused(tmp_path: pathlib.Path) -> None:
    # A child id is also a cache directory name, and the group repository
    # refuses duplicates -- catching it at the call site names the builder
    # rather than surfacing later from inside the factory.
    group = a_grr_group().with_child(
        "main", a_grr().with_resource("scores/pos1", a_position_score()))

    with pytest.raises(ResourceValidationError, match="duplicate child"):
        group.with_child("main", a_grr())

    # The refusal is a rejection of the SECOND declaration, not damage to
    # the builder it was refused on -- which is still usable.
    repo = group.build_repo(tmp_path)
    assert repo.get_resource("scores/pos1", repository_id="main") is not None


def test_the_group_builder_is_immutable() -> None:
    base = a_grr_group()
    extended = base.with_child("main", a_grr())

    assert len(base.children) == 0
    assert len(extended.children) == 1
    assert base is not extended
