# pylint: disable=C0116
"""The ``basic`` resource builder, ``a_basic_resource()`` (gain#1410).

A ``type: basic`` resource is the catch-all: no schema, no type-specific
file list, and the type every repository-layout test reaches for when it
needs *a* resource and does not care which.  Before the builder, that
resource was a hand-written YAML literal.  These tests fence the two
things the literal's replacement has to keep: the bytes of the bare
config, which repository tests pin by md5, and the ``meta:`` round trip.
"""
from __future__ import annotations

import pathlib
import textwrap

from gain.genomic_resources.repository import GR_CONF_FILE_NAME
from gain.genomic_resources.testing.builders import a_basic_resource, a_grr


def test_bare_builder_is_a_basic_resource_with_one_payload_file(
    tmp_path: pathlib.Path,
) -> None:
    res = a_basic_resource().build_resource(tmp_path)

    assert res.get_type() == "basic"
    assert res.get_manifest().names() == {GR_CONF_FILE_NAME, "data.txt"}
    assert res.get_file_content("data.txt")


def test_bare_config_is_byte_identical_to_the_hand_written_literal(
    tmp_path: pathlib.Path,
) -> None:
    """Exactly ``type: basic\\n`` -- 12 bytes, no ``meta:``, no blank line.

    Repository-layout tests hand-write that literal and pin its size and
    md5 in forged manifests; the builder has to render the same bytes for
    those fixtures to be migratable onto it.
    """
    res = a_basic_resource().build_resource(tmp_path)

    assert res.get_file_content(GR_CONF_FILE_NAME) == "type: basic\n"


A_MARKDOWN_DESCRIPTION = textwrap.dedent("""\
    ### Score definitions

    | category | meaning |
    |---|---|
    | 1 | high confidence |
    """)


def test_meta_reads_back_as_authored(tmp_path: pathlib.Path) -> None:
    """A summary and a Markdown description survive the round trip.

    The description is the shape a GRR author writes -- a heading, a
    blank line, a table -- and it comes back verbatim, blank lines and
    all, as a YAML ``|`` block would have stored it.
    """
    res = (
        a_basic_resource()
        .with_meta(
            summary="scores for genes",
            description=A_MARKDOWN_DESCRIPTION)
        .build_resource(tmp_path)
    )

    assert res.get_type() == "basic"
    assert res.get_summary() == "scores for genes"
    assert res.get_description() == A_MARKDOWN_DESCRIPTION


def test_the_in_memory_exit_needs_no_tmp_path() -> None:
    """The same resource, realized in memory.

    A fixture with no ``tmp_path`` in its signature -- the template
    tests' shape -- gets the resource straight from the builder, and it
    is the one the filesystem exit would have written: same type, same
    files, same meta.
    """
    res = (
        a_basic_resource()
        .with_meta(
            summary="scores for genes",
            description=A_MARKDOWN_DESCRIPTION)
        .build_inmemory()
    )

    assert res.get_type() == "basic"
    assert res.get_manifest().names() == {GR_CONF_FILE_NAME, "data.txt"}
    assert res.get_summary() == "scores for genes"
    assert res.get_description() == A_MARKDOWN_DESCRIPTION


def test_with_file_adds_a_payload_beside_the_default(
    tmp_path: pathlib.Path,
) -> None:
    res = (
        a_basic_resource()
        .with_file("README.md", "# about\n")
        .build_resource(tmp_path)
    )

    assert res.get_manifest().names() == {
        GR_CONF_FILE_NAME, "data.txt", "README.md"}
    assert res.get_file_content("README.md") == "# about\n"


def test_with_file_replaces_the_default_payload_by_name(
    tmp_path: pathlib.Path,
) -> None:
    res = (
        a_basic_resource()
        .with_file("data.txt", "authored")
        .build_resource(tmp_path)
    )

    assert res.get_manifest().names() == {GR_CONF_FILE_NAME, "data.txt"}
    assert res.get_file_content("data.txt") == "authored"


def test_a_scalar_meta_is_carried_as_is(tmp_path: pathlib.Path) -> None:
    """``basic`` runs no schema, so a ``meta:`` that is prose gets through.

    That is what the meta-shape tests rely on when they hand-write one,
    and the builder inherits the spelling from ``MetaMixin`` without any
    code of its own: the config carries the scalar, and the resource
    reads it back as no summary rather than refusing to load.
    """
    res = (
        a_basic_resource()
        .with_raw_meta("Some prose that is not a mapping.")
        .build_resource(tmp_path)
    )

    assert res.get_config()["meta"] == "Some prose that is not a mapping."
    assert res.get_summary() == ""


def test_composes_into_a_grr_under_its_own_id(
    tmp_path: pathlib.Path,
) -> None:
    repo = (
        a_grr()
        .with_resource("about/one", a_basic_resource())
        .with_resource("about/two", a_basic_resource().with_meta(summary="s"))
        .build_repo(tmp_path)
    )

    assert repo.get_resource("about/one").get_summary() == ""
    assert repo.get_resource("about/two").get_summary() == "s"
