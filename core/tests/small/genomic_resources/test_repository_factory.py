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
from gain.genomic_resources.testing.builders import (
    a_grr,
    a_position_score,
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
    assert score.fetch_position_scores("chr1", 23) == [0.01]


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
    to the builtin salted ``hash()``: CI runs the suite under
    ``PYTHONHASHSEED=0`` (an ``ENV`` in ``core/Dockerfile`` -- not
    ``core/pytest.ini``, which sets only ``addopts`` and ``markers``), so
    there the pinned literals would keep matching under a salted scheme.
    Only building the same definition in two interpreters started with
    *different* seeds proves the ids are stable for a cache directory that
    outlives the process that created it.
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


def test_a_path_valued_directory_resolves_to_the_same_id_both_ways(
    tmp_path: pathlib.Path,
) -> None:
    """The validator and the builder must agree on a child's id.

    ``_resolve_repo_id`` is one function, but the uniqueness check
    feeds it attributes off the validated pydantic model while the builder
    feeds it values popped from the raw definition dict. They agree only
    while the field type does not coerce -- ``_PathOrStr`` is a smart union
    today, so a ``pathlib.Path`` survives as itself on both sides. Were that
    to change, validation would reason about one id while the repository
    (and its cache directory) got another. Pin it with a definition whose
    ``directory`` is a real ``Path`` rather than a ``str``.
    """
    directory = tmp_path / "grr"
    with pytest.raises(ValueError, match="grr"):
        build_genomic_resource_repository(
            {"type": "group", "children": [
                {"type": "directory", "directory": directory},
                {"type": "directory", "directory": str(directory)},
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


def test_a_root_id_shared_with_a_child_is_rejected() -> None:
    """The root is in the same id namespace as its descendants (#447).

    Naming a repository by its own id now selects it, so a root sharing an
    id with a descendant is no longer benign: the root matches first and
    the descendant becomes unreachable by ``repository_id`` -- the very
    outcome the child-uniqueness check exists to prevent. Refused when the
    definition is loaded, before any repository is built.
    """
    with pytest.raises(ValueError, match="top"):
        build_genomic_resource_repository(
            {"id": "top", "type": "group", "children": [
                {"id": "top", "type": "embedded", "content": {}},
            ]})


def test_a_synthesised_root_id_shared_with_a_descendant_is_rejected(
) -> None:
    """The root's id collides whether it was spelled or synthesised.

    An id-less top-level group is named ``group_repo``; a descendant
    spelling that id explicitly shadows it exactly as a spelled root id
    would. The collision is refused across nesting levels, like every other
    duplicate id in the tree.
    """
    with pytest.raises(ValueError, match="group_repo"):
        build_genomic_resource_repository(
            {"type": "group", "children": [
                {"id": "nested", "type": "group", "children": [
                    {"id": "group_repo", "type": "embedded", "content": {}},
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
    secret = "s3cr3t-445"  # ruff: ignore[hardcoded-password-string]
    with pytest.raises(ValueError, match="dup") as exc_info:
        build_genomic_resource_repository(
            {"type": "group", "children": [
                {"id": "dup", "type": "http", "url": "https://a.example.com",
                 "user": "alice", "password": secret},
                {"id": "dup", "type": "http", "url": "https://b.example.com"},
            ]})
    assert secret not in str(exc_info.value)


def test_synthesised_child_id_does_not_echo_a_url_password() -> None:
    """A credential in a child's url must not survive into its id.

    The secret is deliberately *slug-stable* -- it is all alphanumerics, so
    ``_NON_ID_CHARS_RE`` rewrites none of it and it would appear in the id
    verbatim if the userinfo were not redacted before synthesis. A secret
    spelled with ``-`` or ``.`` would be rewritten to ``_`` on the way into
    the slug, and this assertion would hold whether or not redaction
    happened.
    """
    secret = "s3cr3tURL445"  # ruff: ignore[hardcoded-password-string]
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


# ---------------------------------------------------------------------------
# Top-level repository id synthesis (#461)
# ---------------------------------------------------------------------------


def _realize_source_grr(source_dir: pathlib.Path) -> str:
    """Realize a one-resource filesystem GRR and return its directory."""
    (
        a_grr()
        .with_resource(
            "one",
            a_position_score()
            .with_score("score", "float")
            .with_data("""
                chrom  pos_begin  pos_end  score
                chr1   10         12       0.1
            """),
        )
        .build_repo(source_dir)
    )
    return str(source_dir)


def test_id_less_leaf_repository_with_cache_dir_enumerates_resources(
    tmp_path: pathlib.Path,
) -> None:
    """A definition without a top-level ``id`` must still be usable.

    An id-less ``~/.grr_definition.yaml`` is a supported shape, and the
    factory used to pass the missing id straight through to the protocol --
    where the cache url join (``os.path.join(cache_url, proto_id)``) blew up
    with a ``TypeError`` on the first access.
    """
    repo = build_genomic_resource_repository({
        "type": "dir",
        "directory": _realize_source_grr(tmp_path / "grr"),
        "cache_dir": str(tmp_path / "cache"),
    })

    assert [res.get_full_id() for res in repo.get_all_resources()] == ["one"]


def test_id_less_leaf_repository_with_cache_dir_gets_a_usable_repo_id(
    tmp_path: pathlib.Path,
) -> None:
    """The synthesised root id must be a real name, not ``None`` or ``""``.

    The id names the repository in log messages, in a ``repository_id``
    filter and -- for a cached repository -- on disk, so it has to be
    non-empty in every ``.``-separated segment.
    """
    repo = build_genomic_resource_repository({
        "type": "dir",
        "directory": _realize_source_grr(tmp_path / "grr"),
        "cache_dir": str(tmp_path / "cache"),
    })

    assert repo.repo_id
    assert "None" not in repo.repo_id
    assert all(segment for segment in repo.repo_id.split(".")), repo.repo_id


def test_id_less_leaf_repository_caches_into_a_per_repository_subdirectory(
    tmp_path: pathlib.Path,
) -> None:
    """Cached files land under ``<cache_dir>/<repo id>/``, never in its root.

    ``os.path.join(cache_url, proto_id)`` collapses to the cache root when
    the id is empty, so an id-less repository would spill its resources
    directly into a cache directory that may be shared with other
    repositories.
    """
    cache_dir = tmp_path / "cache"
    repo = build_genomic_resource_repository({
        "type": "dir",
        "directory": _realize_source_grr(tmp_path / "grr"),
        "cache_dir": str(cache_dir),
    })

    assert repo.get_resource("one").get_file_content("data.txt")

    cached_roots = sorted(path.name for path in cache_dir.iterdir())
    assert len(cached_roots) == 1, cached_roots
    assert (cache_dir / cached_roots[0] / "one" / "data.txt").is_file()
    assert not (cache_dir / "one").exists()


@pytest.mark.parametrize("repo_type", ["file", "directory"])
def test_id_less_leaf_root_of_every_filesystem_type_is_usable(
    repo_type: str,
    tmp_path: pathlib.Path,
) -> None:
    """``file`` and ``directory`` are aliases of ``dir`` -- same AC.

    The id resolution happens before the type is dispatched on, so an alias
    must not be a hole in the fix: each one has to enumerate its resources
    and cache them under a per-repository directory just like ``dir`` does.

    ``s3`` is the one leaf type this section cannot cover: its branch builds
    a read-write fsspec protocol, which calls ``create_bucket`` at
    construction time, so it needs the MinIO fixture behind
    ``--enable-s3-testing`` (``docker compose up -d``) rather than anything a
    small test has. ``url`` covers the remote-identity shape instead -- see
    ``test_id_less_url_root_gets_the_same_id_as_the_same_url_as_http``.
    """
    cache_dir = tmp_path / "cache"
    repo = build_genomic_resource_repository({
        "type": repo_type,
        "directory": _realize_source_grr(tmp_path / "grr"),
        "cache_dir": str(cache_dir),
    })

    assert repo.repo_id
    assert "None" not in repo.repo_id
    assert [res.get_full_id() for res in repo.get_all_resources()] == ["one"]
    assert repo.get_resource("one").get_file_content("data.txt")

    cached_roots = sorted(path.name for path in cache_dir.iterdir())
    assert len(cached_roots) == 1, cached_roots
    assert (cache_dir / cached_roots[0] / "one" / "data.txt").is_file()
    assert not (cache_dir / "one").exists()


def test_empty_top_level_id_behaves_like_an_absent_one(
    tmp_path: pathlib.Path,
) -> None:
    """``id: ""`` is as good as no ``id`` -- both get the synthesised one.

    An empty id is falsy everywhere it is used (the ``repository_id`` filter
    ignores it, the cache path join collapses on it), so it must not be a
    second, subtly different way of spelling "no id".
    """
    source_dir = _realize_source_grr(tmp_path / "grr")
    absent_cache = tmp_path / "cache_absent"
    empty_cache = tmp_path / "cache_empty"

    absent = build_genomic_resource_repository({
        "type": "dir", "directory": source_dir,
        "cache_dir": str(absent_cache)})
    empty = build_genomic_resource_repository({
        "id": "", "type": "dir", "directory": source_dir,
        "cache_dir": str(empty_cache)})

    assert empty.repo_id == absent.repo_id

    for repo in (absent, empty):
        assert repo.get_resource("one").get_file_content("data.txt")

    cached_roots = sorted(path.name for path in empty_cache.iterdir())
    assert cached_roots == sorted(path.name for path in absent_cache.iterdir())
    assert len(cached_roots) == 1, cached_roots
    assert (empty_cache / cached_roots[0] / "one" / "data.txt").is_file()


def test_id_less_leaf_repository_without_cache_dir_is_addressable_by_its_id(
    tmp_path: pathlib.Path,
) -> None:
    """A ``repository_id`` filter must be able to name an id-less repository.

    Without a synthesised id there is nothing to pass as ``repository_id``:
    the repository has ``None`` for an id, and ``None`` is the "no filter"
    value rather than a name.
    """
    repo = build_genomic_resource_repository({
        "type": "dir",
        "directory": _realize_source_grr(tmp_path / "grr"),
    })

    repo_id = repo.repo_id
    assert repo_id

    assert repo.find_resource("one", repository_id=repo_id) is not None
    assert repo.get_resource(
        "one", repository_id=repo_id).get_full_id() == "one"
    assert repo.find_resource("one", repository_id="some_other_repo") is None


# The definition the root-determinism test builds, kept module-level so the
# subprocess script and the pinned expectation cannot drift apart.
_ROOT_DETERMINISM_DEFINITION = {
    "type": "http", "url": "https://one.example.com"}

# A pinned literal, not a recomputation of the implementation's own formula:
# asserting the exact string is what makes this test able to notice a change
# of scheme. The root is synthesised from the same identity a child with this
# url would be, hence the same ``<slug>_<sha256(identity)[:8]>``.
_ROOT_DETERMINISM_EXPECTED_ID = "https_one_example_com_933c1326"


def test_synthesised_root_id_is_stable_across_processes() -> None:
    """The root id must not depend on Python's per-process hash randomisation.

    A cache directory named after the synthesised id outlives the process
    that created it, so a salted ``hash()`` would silently move the whole
    cache on every run. CI runs the suite under ``PYTHONHASHSEED=0`` (an
    ``ENV`` in ``core/Dockerfile`` -- not ``core/pytest.ini``, which sets
    only ``addopts`` and ``markers``), so only building the same definition
    in two interpreters started with *different* seeds can prove this.
    """
    script = (
        "import json;"
        "from gain.genomic_resources.repository_factory import "
        "build_genomic_resource_repository as b;"
        f"r=b({_ROOT_DETERMINISM_DEFINITION!r});"
        "print(json.dumps(r.repo_id))"
    )

    def root_id_under_seed(seed: str) -> str:
        env = {**os.environ, "PYTHONHASHSEED": seed}
        completed = subprocess.run(
            [sys.executable, "-c", script],
            check=True, capture_output=True, text=True, env=env)
        return cast(str, json.loads(completed.stdout))

    assert root_id_under_seed("0") == _ROOT_DETERMINISM_EXPECTED_ID
    assert root_id_under_seed("1") == _ROOT_DETERMINISM_EXPECTED_ID


def test_id_less_url_root_gets_the_same_id_as_the_same_url_as_http() -> None:
    """The ``url`` type resolves its root id like every other leaf type.

    ``url`` and ``http`` are separate branches of the builder but one
    identity: the id is synthesised from the ``url`` alone, so declaring the
    same repository either way must name it the same -- and, with a
    ``cache_dir``, point it at the same cache directory. Only the fallback
    for a repository with *no* identity of its own looks at the type.
    """
    as_url = build_genomic_resource_repository(
        {"type": "url", "url": "https://one.example.com"})
    as_http = build_genomic_resource_repository(
        {"type": "http", "url": "https://one.example.com"})

    assert as_url.repo_id == _ROOT_DETERMINISM_EXPECTED_ID
    assert as_url.repo_id == as_http.repo_id


def test_synthesised_root_id_does_not_echo_a_url_password() -> None:
    """The root id is derived from its url, which may carry a credential.

    The id is logged and, for a cached repository, becomes a directory name
    on disk -- neither is a place for a password.

    The secret is deliberately *slug-stable* -- it is all alphanumerics, so
    ``_NON_ID_CHARS_RE`` rewrites none of it and it would appear in the id
    verbatim if the userinfo were not redacted before synthesis. A secret
    spelled with ``-`` or ``.`` would be rewritten to ``_`` on the way into
    the slug, and this assertion would hold whether or not redaction
    happened.
    """
    secret = "s3cr3tURL461"  # ruff: ignore[hardcoded-password-string]
    repo = build_genomic_resource_repository(
        {"type": "http", "url": f"https://alice:{secret}@a.example.com"})

    assert repo.repo_id
    assert secret not in repo.repo_id


def test_id_less_top_level_embedded_repository_gets_a_clean_id() -> None:
    """A repository with no identity of its own is named after its type.

    An ``embedded`` root has neither a url nor a directory to synthesise
    from, and -- being the root -- no path through ``children`` either, so
    the fallback is the type alone. It must not trail the separator of an
    empty path (``embedded_``).
    """
    repo = build_genomic_resource_repository(
        {"type": "embedded", "content": {}})

    assert repo.repo_id == "embedded_repo"


def test_id_less_top_level_group_keeps_the_group_repo_id(
    tmp_path: pathlib.Path,
) -> None:
    """Resolving the root's id must not rename an id-less top-level group.

    ``GenomicResourceGroupRepo`` supplied this name itself, from its own
    ``None`` fallback; the factory now resolves it before the repository is
    built. The observable id -- and the cache directory named after it, which
    an operator's populated cache lives in -- must be byte-identical.

    The expected id changed deliberately in #447: a ``cache_dir`` used to
    make the built repository report ``group_repo.caching_repo``, because
    the caching wrapper renamed what it wrapped. It no longer does -- a
    cache decides how resources are served, not what the repository is
    called -- so the root reports the same id with and without a cache.
    """
    repo = build_genomic_resource_repository(
        {"type": "group", "cache_dir": str(tmp_path / "cache"),
         "children": [
             {"id": "a", "type": "embedded", "content": {
                 "one": {GR_CONF_FILE_NAME: "type: position_score",
                         "data.txt": "AAAA-from-a"}}},
         ]})

    assert repo.repo_id == "group_repo"
    assert repo.get_resource("one").get_file_content("data.txt") == \
        "AAAA-from-a"


def test_synthesised_group_root_id_is_selectable_as_a_repository_id(
) -> None:
    """A group root answers to its own id, like every other repository.

    The semantics changed deliberately in #447: this test used to assert
    that naming the root selected *nothing*, because
    ``GenomicResourceGroupRepo.find_resource`` compared ``repository_id``
    against its children's ids only and never against its own. Now every
    repository answers to its own id, so naming the repository the caller
    is holding resolves the resource with no further filtering below that
    point -- and a synthesised root id is as selectable as an explicit one.

    Selecting a *child* by its id, and the unfiltered lookup, are unchanged.
    """
    repo = build_genomic_resource_repository(
        {"type": "group", "children": [
            {"id": "a", "type": "embedded", "content": {
                "one": {GR_CONF_FILE_NAME: "type: position_score",
                        "data.txt": "AAAA-from-a"}}},
        ]})

    assert repo.repo_id == "group_repo"

    assert repo.find_resource("one", repository_id=repo.repo_id) is not None
    assert repo.find_resource("one", repository_id="a") is not None
    assert repo.find_resource("one") is not None


def test_explicit_top_level_id_is_used_verbatim(
    tmp_path: pathlib.Path,
) -> None:
    """An explicit ``id`` is never synthesised over -- nor is its cache path.

    A repository that already names itself keeps that name and keeps caching
    into ``<cache_dir>/<id>/``, so an existing populated cache is not
    orphaned by the id-synthesis this section is about.

    The expected id changed deliberately in #447: the caching wrapper used
    to report ``mine.caching_repo``, so the ``id`` an operator spelled was
    verbatim only until a ``cache_dir`` was added next to it. Wrapping a
    repository in a cache no longer renames it. The cache directory is
    derived from the *protocol* id and was never affected either way.
    """
    repo = build_genomic_resource_repository({
        "id": "mine",
        "type": "dir",
        "directory": _realize_source_grr(tmp_path / "grr"),
        "cache_dir": str(tmp_path / "cache"),
    })

    assert repo.repo_id == "mine"
    assert repo.get_resource("one").get_file_content("data.txt")
    assert (tmp_path / "cache" / "mine" / "one" / "data.txt").is_file()


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
