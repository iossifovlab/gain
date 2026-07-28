# pylint: disable=W0621,C0114,C0116,W0212,W0613
import copy
import json
import os
import pathlib
import subprocess
import sys
from typing import cast

import pytest
import yaml
from gain.genomic_resources.cached_repository import GenomicResourceCachedRepo
from gain.genomic_resources.fsspec_protocol import (
    FsspecReadOnlyProtocol,
    FsspecReadWriteProtocol,
    FsspecRepositoryProtocol,
)
from gain.genomic_resources.genomic_scores import (
    PositionScore,
)
from gain.genomic_resources.group_repository import GenomicResourceGroupRepo
from gain.genomic_resources.repository import (
    GR_CONF_FILE_NAME,
    GenomicResourceProtocolRepo,
)
from gain.genomic_resources.repository_factory import (
    build_genomic_resource_repository,
)
from gain.genomic_resources.testing import (
    build_filesystem_test_repository,
    convert_to_tab_separated,
    setup_directories,
)


def test_build_an_empty_repository() -> None:
    empty_repository = build_genomic_resource_repository(
        {"id": "empty", "type": "embedded", "content": {}})
    assert len(list(empty_repository.get_all_resources())) == 0


def test_build_a_repository_with_one_resource() -> None:
    one_resrouce_repo = build_genomic_resource_repository(
        {"id": "oneResrouce",
         "type": "embedded",
         "content": {
                 "one": {"genomic_resource.yaml": ""},
         },
         })
    assert len(list(one_resrouce_repo.get_all_resources())) == 1


def test_build_a_group_repository() -> None:
    repo = build_genomic_resource_repository(
        {"type": "group", "children": [
            {"id": "a", "type": "embedded", "content": {}},
            {"id": "b", "type": "embedded", "content": {}},
        ]})
    assert isinstance(repo, GenomicResourceGroupRepo)
    assert len(repo.children) == 2


def test_build_a_complex_but_realistic_scenario(
    tmp_path: pathlib.Path,
) -> None:
    repo = build_genomic_resource_repository(
        {"type": "group", "children": [
            {
                "type": "group",
                "cache_dir": tmp_path / "ttt/remotes12Cache", "children": [
                    {"id": "r1", "type": "http", "url": "http://r1.org/repo"},
                    {"id": "r2", "type": "http", "url": "http://r2.org/repo"},
                ]},
            {"id": "r3", "type": "http", "url": "http://r3.org/repo",
             "cache_dir": tmp_path / "ttt/remote3Cache"},
            {"id": "my", "type": "directory",
             "directory": tmp_path / "data/my/grRepo"},
            {"id": "mm", "type": "embedded", "content": {}},
        ]})
    # The asserts implicitly test the types of the repositories too:
    #   * only group     repository has a 'children'              attribute;
    #   * only chached   repository has a 'child' and 'cache_dir' attributes;
    #   * only url       repository has a 'url'                   attribute;
    #   * only directory repository has a 'directory'             attribute;
    #   * only embedded   repository has a 'content'               attribute.

    assert isinstance(repo, GenomicResourceGroupRepo)

    assert isinstance(repo.children[0], GenomicResourceCachedRepo)
    assert str(repo.children[0].cache_url) == \
        f"file://{tmp_path / 'ttt/remotes12Cache'!s}"

    assert isinstance(repo.children[0].child, GenomicResourceGroupRepo)
    assert isinstance(
        repo.children[0].child.children[0],
        GenomicResourceProtocolRepo)
    assert repo.children[0].child.children[0].proto.url == \
        "http://r1.org/repo"

    assert isinstance(
        repo.children[0].child.children[1],
        GenomicResourceProtocolRepo)
    assert repo.children[0].child.children[1].proto.url == \
        "http://r2.org/repo"

    assert isinstance(repo.children[1], GenomicResourceCachedRepo)
    assert str(repo.children[1].cache_url) == \
        f"file://{tmp_path / 'ttt/remote3Cache'!s}"

    assert isinstance(
        repo.children[1].child,
        GenomicResourceProtocolRepo)
    assert repo.children[1].child.proto.url == "http://r3.org/repo"

    assert isinstance(
        repo.children[2],
        GenomicResourceProtocolRepo)
    assert repo.children[2].proto.url == \
        f"file://{tmp_path / 'data/my/grRepo'!s}"

    assert isinstance(
        repo.children[3],
        GenomicResourceProtocolRepo)
    assert isinstance(
        repo.children[3].proto,
        FsspecRepositoryProtocol)
    assert repo.children[3].proto.scheme == "memory"


def test_build_a_complex_but_realistic_scenario_yaml(
    tmp_path: pathlib.Path,
) -> None:
    definition = yaml.safe_load(f"""
        type: group
        children:
        - type: group
          cache_dir: "{tmp_path!s}/ttt/remotes12Cache(1.3)"
          children:
          - id: r1
            type: http
            url: http://r1.org/repo
          - id: r2
            type: http
            url: http://r2.org/repo
        - id: r3
          type: http
          url: http://r3.org/repo
          cache_dir: {tmp_path!s}/ttt/remote3Cache
        - id: my
          type: directory
          directory: {tmp_path!s}/data/my/grRepo
        - id: mm
          type: embedded
          content: {{}}

    """)
    repo = build_genomic_resource_repository(definition)

    # The asserts implicitly test the types of the repositories too:
    #   * only group     repository has a 'children'              attribute;
    #   * only chached   repository has a 'child' and 'cache_dir' attributes;
    #   * only url       repository has a 'url'                   attribute;
    #   * only directory repository has a 'directory'             attribute;
    #   * only embedded   repository has a 'content'               attribute.
    assert isinstance(repo, GenomicResourceGroupRepo)

    assert isinstance(repo.children[0], GenomicResourceCachedRepo)
    assert str(repo.children[0].cache_url) == \
        f"file://{tmp_path}/ttt/remotes12Cache(1.3)"

    assert isinstance(repo.children[0].child, GenomicResourceGroupRepo)
    assert isinstance(
        repo.children[0].child.children[0],
        GenomicResourceProtocolRepo)
    assert repo.children[0].child.children[0].proto.url == \
        "http://r1.org/repo"

    assert isinstance(
        repo.children[0].child.children[1],
        GenomicResourceProtocolRepo)
    assert repo.children[0].child.children[1].proto.url == \
        "http://r2.org/repo"

    assert isinstance(repo.children[1], GenomicResourceCachedRepo)
    assert str(repo.children[1].cache_url) == \
        f"file://{tmp_path}/ttt/remote3Cache"

    assert isinstance(
        repo.children[1].child,
        GenomicResourceProtocolRepo)
    assert repo.children[1].child.proto.url == "http://r3.org/repo"

    assert isinstance(
        repo.children[2],
        GenomicResourceProtocolRepo)
    assert repo.children[2].proto.url == f"file://{tmp_path}/data/my/grRepo"

    assert isinstance(
        repo.children[3],
        GenomicResourceProtocolRepo)
    assert isinstance(
        repo.children[3].proto,
        FsspecRepositoryProtocol)
    assert repo.children[3].proto.scheme == "memory"


def test_build_a_configuration_with_embedded() -> None:
    definition = yaml.safe_load("""
        id: mm
        type: embedded
        content:
            one:
                genomic_resource.yaml: |
                    type: position_score
                    table:
                        filename: data.mem
                    scores:
                    - id: score
                      type: float
                      desc: >
                            The phastCons computed over the tree of 100
                            verterbarte species
                      name: s1
                data.mem: |
                    chrom  pos_begin  s1
                    chr1   23         0.01
                    chr1   24         0.2""")
    repo = build_genomic_resource_repository(definition)
    assert repo is not None
    res = repo.get_resource("one")
    assert res is not None

    score = PositionScore(res)
    score.open()
    assert score.fetch_scores("chr1", 23) == [0.01]


# ---------------------------------------------------------------------------
# Child repository ids within a group must be unique (#445)
# ---------------------------------------------------------------------------


def test_group_with_duplicate_explicit_child_ids_is_rejected() -> None:
    with pytest.raises(ValueError, match="dup"):
        build_genomic_resource_repository(
            {"type": "group", "children": [
                {"id": "dup", "type": "embedded", "content": {}},
                {"id": "dup", "type": "embedded", "content": {}},
            ]})


def test_group_with_id_less_children_gets_distinct_child_ids() -> None:
    repo = build_genomic_resource_repository(
        {"type": "group", "children": [
            {"type": "http", "url": "https://one.example.com"},
            {"type": "http", "url": "https://two.example.com"},
        ]})
    assert isinstance(repo, GenomicResourceGroupRepo)

    child_ids = [child.repo_id for child in repo.children]
    assert all(child_ids), child_ids
    assert len(set(child_ids)) == 2, child_ids


# The definition used by both determinism tests below. Kept module-level so
# the in-process test and the subprocess test cannot drift apart.
_DETERMINISM_DEFINITION = {
    "type": "group", "children": [
        {"type": "http", "url": "https://one.example.com"},
        {"type": "embedded", "content": {}},
    ]}

# Pinned literals, not a recomputation of the implementation's own formula:
# asserting the exact strings is what makes this test able to notice a change
# of scheme (a different digest, a different slug rule, or a switch away from
# SHA-256). ``<slug>_<sha256(identity)[:8]>`` for a child with a url, and
# ``<type>_<path>`` for one with no identity of its own.
_DETERMINISM_EXPECTED_IDS = [
    "https_one_example_com_933c1326",
    "embedded_1",
]


def test_synthesised_child_ids_are_deterministic() -> None:
    repo = build_genomic_resource_repository(
        copy.deepcopy(_DETERMINISM_DEFINITION))
    assert isinstance(repo, GenomicResourceGroupRepo)

    assert [
        child.repo_id for child in repo.children
    ] == _DETERMINISM_EXPECTED_IDS


def test_synthesised_child_ids_are_stable_across_processes() -> None:
    """Ids must not depend on Python's per-process hash randomisation.

    The in-process test above cannot catch a switch from ``hashlib.sha256``
    to the builtin salted ``hash()``: ``core/pytest.ini`` pins
    ``PYTHONHASHSEED=0``, so even the pinned literals would keep matching
    under a salted scheme. Only building the same definition in two
    interpreters started with *different* seeds proves the ids are stable
    for a cache directory that outlives the process that created it.
    """
    script = (
        "import json;"
        "from gain.genomic_resources.repository_factory import "
        "build_genomic_resource_repository as b;"
        f"r=b({_DETERMINISM_DEFINITION!r});"
        "print(json.dumps([c.repo_id for c in r.children]))"
    )

    def child_ids_under_seed(seed: str) -> list[str]:
        env = {**os.environ, "PYTHONHASHSEED": seed}
        completed = subprocess.run(
            [sys.executable, "-c", script],
            check=True, capture_output=True, text=True, env=env)
        return cast(list[str], json.loads(completed.stdout))

    assert child_ids_under_seed("0") == _DETERMINISM_EXPECTED_IDS
    assert child_ids_under_seed("1") == _DETERMINISM_EXPECTED_IDS


def test_group_with_the_same_directory_listed_twice_is_rejected(
    tmp_path: pathlib.Path,
) -> None:
    with pytest.raises(ValueError, match=str(tmp_path.name)):
        build_genomic_resource_repository(
            {"type": "group", "children": [
                {"type": "directory", "directory": str(tmp_path / "grr")},
                {"type": "directory", "directory": str(tmp_path / "grr")},
            ]})


def test_duplicate_child_ids_in_a_nested_group_are_rejected() -> None:
    with pytest.raises(ValueError, match="inner_dup"):
        build_genomic_resource_repository(
            {"type": "group", "children": [
                {"id": "outer", "type": "embedded", "content": {}},
                {"id": "nested", "type": "group", "children": [
                    {"id": "inner_dup", "type": "embedded", "content": {}},
                    {"id": "inner_dup", "type": "embedded", "content": {}},
                ]},
            ]})


def test_the_same_directory_at_two_nesting_levels_is_rejected(
    tmp_path: pathlib.Path,
) -> None:
    """A collision across nesting levels is still a collision.

    The synthesised id is derived from the child's ``directory``, which does
    not depend on where in the tree the child sits -- so listing one
    directory at two different levels resolves to one id twice.
    """
    with pytest.raises(ValueError, match=str(tmp_path.name)):
        build_genomic_resource_repository(
            {"id": "top", "type": "group", "children": [
                {"type": "directory", "directory": str(tmp_path / "grr")},
                {"id": "nested", "type": "group", "children": [
                    {"type": "directory", "directory": str(tmp_path / "grr")},
                ]},
            ]})


def test_id_less_children_of_nested_groups_are_addressable() -> None:
    """Positional ids must be unique across the whole definition tree.

    A fallback id built from the child's index among its *siblings* gives the
    first child of the top group and the first child of a nested group the
    same id -- reintroducing, one level down, the exact ambiguity #445 is
    about.
    """
    repo = build_genomic_resource_repository(
        {"id": "top", "type": "group", "children": [
            {"type": "embedded", "content": {
                "one": {GR_CONF_FILE_NAME: "type: position_score",
                        "data.txt": "AAAA-from-a"}}},
            {"id": "nested", "type": "group", "children": [
                {"type": "embedded", "content": {
                    "one": {GR_CONF_FILE_NAME: "type: position_score",
                            "data.txt": "BBBB-from-b"}}},
            ]},
        ]})
    assert isinstance(repo, GenomicResourceGroupRepo)

    outer = repo.children[0]
    nested = repo.children[1]
    assert isinstance(nested, GenomicResourceGroupRepo)
    inner = nested.children[0]
    assert outer.repo_id != inner.repo_id

    assert repo.get_resource(
        "one", repository_id=outer.repo_id,
    ).get_file_content("data.txt") == "AAAA-from-a"
    assert repo.get_resource(
        "one", repository_id=inner.repo_id,
    ).get_file_content("data.txt") == "BBBB-from-b"


def test_cached_group_with_id_less_children_of_nested_groups(
    tmp_path: pathlib.Path,
) -> None:
    """The cached-repo collision guard must not be reachable from a definition.

    With sibling-scoped positional ids this raised ``repository id
    <embedded_0> is used by more than one repository`` on first enumeration
    -- a crash the operator cannot act on, naming a group they never wrote.
    """
    repo = build_genomic_resource_repository(
        {"id": "top", "type": "group", "cache_dir": str(tmp_path / "cache"),
         "children": [
             {"type": "embedded", "content": {
                 "one": {GR_CONF_FILE_NAME: "type: position_score"}}},
             {"id": "nested", "type": "group", "children": [
                 {"type": "embedded", "content": {
                     "two": {GR_CONF_FILE_NAME: "type: position_score"}}},
             ]},
         ]})

    assert {
        res.get_full_id() for res in repo.get_all_resources()
    } == {"one", "two"}


def test_duplicate_child_id_error_does_not_echo_a_password() -> None:
    secret = "s3cr3t-445"  # noqa: S105
    with pytest.raises(ValueError, match="dup") as exc_info:
        build_genomic_resource_repository(
            {"type": "group", "children": [
                {"id": "dup", "type": "http", "url": "https://a.example.com",
                 "user": "alice", "password": secret},
                {"id": "dup", "type": "http", "url": "https://b.example.com"},
            ]})
    assert secret not in str(exc_info.value)


def test_synthesised_child_id_does_not_echo_a_url_password() -> None:
    secret = "s3cr3t-url-445"  # noqa: S105
    repo = build_genomic_resource_repository(
        {"type": "group", "children": [
            {"type": "http", "url": f"https://alice:{secret}@a.example.com"},
        ]})
    assert isinstance(repo, GenomicResourceGroupRepo)
    assert secret not in repo.children[0].repo_id


def test_id_less_group_children_are_individually_addressable() -> None:
    repo = build_genomic_resource_repository(
        {"type": "group", "children": [
            {"type": "embedded", "content": {
                "one": {GR_CONF_FILE_NAME: "type: position_score",
                        "data.txt": "AAAA-from-a"}}},
            {"type": "embedded", "content": {
                "one": {GR_CONF_FILE_NAME: "type: position_score",
                        "data.txt": "BBBB-from-b"}}},
        ]})
    assert isinstance(repo, GenomicResourceGroupRepo)
    first_id, second_id = (child.repo_id for child in repo.children)

    first = repo.get_resource("one", repository_id=first_id)
    second = repo.get_resource("one", repository_id=second_id)

    assert first.get_file_content("data.txt") == "AAAA-from-a"
    assert second.get_file_content("data.txt") == "BBBB-from-b"


def test_read_only_filesystem_repo(
    tmp_path: pathlib.Path,
) -> None:
    setup_directories(
        tmp_path / "grr", {
            "allele_score": {
                GR_CONF_FILE_NAME: """
                    type: allele_score
                    table:
                        filename: data.txt
                        reference:
                          name: reference
                        alternative:
                          name: alternative
                    scores:
                        - id: freq
                          type: float
                          desc: ""
                          name: freq
                    default_annotation:
                    - source: freq
                      name: allele_freq

                """,
                "data.txt": convert_to_tab_separated("""
                    chrom  pos_begin  reference  alternative  freq
                    1      10         A          G            0.02
                """),
            },
        })
    repo = build_filesystem_test_repository(tmp_path / "grr")
    assert repo is not None
    assert isinstance(repo, GenomicResourceProtocolRepo)
    assert isinstance(repo.proto, FsspecRepositoryProtocol)
    assert isinstance(repo.proto, FsspecReadWriteProtocol)

    repo_ro = build_genomic_resource_repository({
        "id": "allele_score_local",
        "type": "directory",
        "directory": str(tmp_path / "grr"),
        "read_only": True,
    })
    assert repo_ro is not None
    assert isinstance(repo_ro, GenomicResourceProtocolRepo)
    assert isinstance(repo_ro.proto, FsspecRepositoryProtocol)
    assert isinstance(repo_ro.proto, FsspecReadOnlyProtocol)
