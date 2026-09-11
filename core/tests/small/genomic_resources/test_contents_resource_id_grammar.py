# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""One grammar for a resource id, on both enumeration paths (gain#1352).

Scanning a repository parses every candidate path with
``parse_gr_id_version_token``, so an id outside ``[a-zA-Z0-9/._-]`` fails
the scan and the repository cannot be enumerated at all.  Enumerating from
a remote ``.CONTENTS`` used to check the id only for *containment*, which
accepts a space, a ``%``, a ``#``, a ``?`` and a non-ASCII letter -- so the
two paths disagreed about what an id is.

The disagreement is not cosmetic.  A wider id served from ``.CONTENTS`` is
cached to local disk under that name, and every later scan of that cache
dies on it -- costing the cache its healthy resources too.  These tests pin
that such an id is refused where it enters, with the healthy resources of
the same ``.CONTENTS`` still served.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import logging
import pathlib
from typing import Any

import pytest
from gain.genomic_resources.cached_repository import (
    GenomicResourceCachedRepo,
    cache_resources,
)
from gain.genomic_resources.repository import (
    _RESOURCE_ID_WITH_VERSION_PATH_RE,
    GR_CONF_FILE_NAME,
    GR_CONTENTS_FILE_NAME,
    GenomicResourceProtocolRepo,
    malformed_resource_id_reason,
)
from gain.genomic_resources.testing import build_filesystem_test_protocol

from tests.small.genomic_resources.conftest import captured_warnings

#: The resource every entry below describes.
_CONFIG_TEXT = "type: basic\n"
_CONFIG_MD5 = hashlib.md5(  # ruff: ignore[hashlib-insecure-hash-function]
    _CONFIG_TEXT.encode()).hexdigest()

#: Where the drop is reported from.
_PROTOCOL_LOGGER = "gain.genomic_resources.fsspec_protocol"


def _contents_entry(resource_id: str) -> dict[str, Any]:
    return {
        "id": resource_id,
        "version": "0",
        "config": {"type": "basic"},
        "manifest": [{
            "name": GR_CONF_FILE_NAME,
            "size": len(_CONFIG_TEXT),
            "md5": _CONFIG_MD5,
        }],
    }


def _write_contents(
    remote_root: pathlib.Path, resource_ids: list[str],
) -> None:
    with gzip.open(remote_root / GR_CONTENTS_FILE_NAME, "wt") as outfile:
        json.dump([_contents_entry(rid) for rid in resource_ids], outfile)


def _remote_with_contents_ids(
    tmp_path: pathlib.Path, resource_ids: list[str],
) -> pathlib.Path:
    """A remote GRR whose ``.CONTENTS`` is written by hand, not by a scan.

    Written directly on purpose: ``.CONTENTS`` is the only enumeration a
    protocol that cannot scan has, and a scan is exactly what cannot
    produce these ids.  The resources themselves are not laid down --
    enumeration never opens them.
    """
    remote_root = tmp_path / "remote"
    remote_root.mkdir(parents=True)
    _write_contents(remote_root, resource_ids)
    return remote_root


def _served_ids(remote_root: pathlib.Path) -> list[str]:
    proto = build_filesystem_test_protocol(
        remote_root, repair=False, read_only=True)
    return sorted(res.resource_id for res in proto.get_all_resources())


@pytest.mark.parametrize("resource_id", [
    "has space/x",
    "hg38/100%/x",
    "hg38/a#b/x",
    "hg38/a?b/x",
    "hg38/naïve/x",
])
def test_contents_id_outside_the_scan_grammar_is_not_served(
    tmp_path: pathlib.Path, resource_id: str,
) -> None:
    """Ids the scan refuses and containment used to allow.

    End to end, through a protocol, rather than against the predicate:
    the wiring is the half a predicate test cannot see.
    """
    remote_root = _remote_with_contents_ids(tmp_path, [resource_id, "good_one"])

    assert _served_ids(remote_root) == ["good_one"]


def test_contents_id_with_an_empty_segment_is_not_served(
    tmp_path: pathlib.Path,
) -> None:
    """The one refusal the scan grammar does *not* already make.

    ``[a-zA-Z0-9/._-]`` has ``/`` inside it, so a doubled separator
    matches the grammar and passes the scan -- a filesystem simply cannot
    produce it, having no empty directory name.  A hand-written
    ``.CONTENTS`` can, so this rule has to be added rather than inherited.
    """
    remote_root = _remote_with_contents_ids(
        tmp_path, ["hg38//scores/x", "good_one"])

    assert _served_ids(remote_root) == ["good_one"]


def test_a_refused_id_is_reported_with_the_character_it_carries(
    tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """Dropped silently, the resource just goes missing with no lead."""
    remote_root = _remote_with_contents_ids(tmp_path, ["a#b", "good_one"])

    with caplog.at_level(logging.WARNING, logger=_PROTOCOL_LOGGER):
        assert _served_ids(remote_root) == ["good_one"]

    dropped = [
        message for message in captured_warnings(caplog)
        if "dropping resource" in message
    ]
    assert len(dropped) == 1
    assert "a#b" in dropped[0]
    assert "carries <#>" in dropped[0]
    assert GR_CONTENTS_FILE_NAME in dropped[0]


@pytest.mark.parametrize("resource_id", [
    "hg38/scores/phastcons",
    "one",
    "with.dots-and_dashes/x",
    ".",
    "",
])
def test_a_well_formed_contents_id_is_still_served(
    tmp_path: pathlib.Path, resource_id: str,
) -> None:
    """The rule has to leave every ordinary id alone.

    ``""`` is the case with teeth, and the reason the root exemption is
    not decoration: a GRR whose root directory carries the config *is* a
    resource, and it is published into ``.CONTENTS`` under the empty id.
    Splitting that on ``/`` yields one empty segment, so without the
    exemption the empty-segment rule would drop the root resource of
    every such repository.  ``.`` is the other spelling of the root and
    passes both sub-rules on its own, which is exactly why it cannot
    stand in for this case.
    """
    remote_root = _remote_with_contents_ids(tmp_path, [resource_id])

    assert _served_ids(remote_root) == [resource_id]


@pytest.mark.parametrize("character", [
    *(chr(code) for code in range(0x20, 0x7F)), "ä", "é", "ß", "ñ",
])
def test_the_two_id_grammars_accept_the_same_characters(
    character: str,
) -> None:
    """The agreement the whole change exists to create, character by character.

    Both patterns are composed from ``RESOURCE_ID_CHARACTER_CLASS``, so
    they cannot drift *textually*.  What this pins is that they still
    agree in *meaning* -- one accepts the class, the other complements
    it, and a future edit that gives either its own spelling again, or
    that adds a rule to one side only, comes back here.

    Asked about a character sitting *between* two ordinary ones, so that
    neither rule is answering a question about a degenerate id: ``/``
    alone is a pair of empty segments, and ``(`` alone cannot open the
    version suffix it delimits.
    """
    resource_id = f"a{character}b"

    accepted_by_scan = _RESOURCE_ID_WITH_VERSION_PATH_RE.fullmatch(
        resource_id) is not None

    assert (malformed_resource_id_reason(resource_id) is None) \
        == accepted_by_scan, resource_id


def _remote_with_real_resources(
    tmp_path: pathlib.Path, resource_ids: list[str],
) -> pathlib.Path:
    """Like the above, but the resources are laid down on disk as well.

    Caching copies the files and verifies them against the manifest, so
    a fixture that gets cached needs the resources to be there.
    """
    remote_root = tmp_path / "remote"
    for resource_id in resource_ids:
        resource_dir = remote_root / resource_id
        resource_dir.mkdir(parents=True)
        (resource_dir / GR_CONF_FILE_NAME).write_text(_CONFIG_TEXT)
    _write_contents(remote_root, resource_ids)
    return remote_root


def test_a_wider_contents_id_no_longer_poisons_a_local_cache(
    tmp_path: pathlib.Path,
) -> None:
    """The damage the disagreement actually did, and the reason for the rule.

    Caching the whole repository copied the wider id to local disk under
    that name -- and a cache directory is an ordinary GRR, so the next
    scan of it raised ``unexpected value for resource ID and version``
    and the cache lost every healthy resource in it, not just the one.
    """
    remote_root = _remote_with_real_resources(
        tmp_path, ["has space/x", "good_one"])
    remote_repo = GenomicResourceProtocolRepo(
        build_filesystem_test_protocol(
            remote_root, repair=False, read_only=True))
    cache_dir = tmp_path / "cache"

    cache_resources(
        GenomicResourceCachedRepo(remote_repo, str(cache_dir)), None)

    cached_root, = [path for path in cache_dir.iterdir() if path.is_dir()]
    rescanned = build_filesystem_test_protocol(cached_root, repair=False)
    assert sorted(
        res.resource_id for res in rescanned.get_all_resources()
    ) == ["good_one"]
