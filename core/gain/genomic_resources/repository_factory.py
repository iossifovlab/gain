"""Provides a factory for building genomic resources repostiories."""

from __future__ import annotations

import copy
import hashlib
import os
import pathlib
import re
import tempfile
from collections.abc import Iterator
from typing import Annotated, Any, Literal, cast
from urllib.parse import urlparse

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    field_serializer,
    field_validator,
    model_validator,
)

from gain import logging

from .cached_repository import GenomicResourceCachedRepo
from .fsspec_protocol import build_fsspec_protocol, build_inmemory_protocol
from .group_repository import GenomicResourceGroupRepo
from .repository import (
    GenomicResource,
    GenomicResourceProtocolRepo,
    GenomicResourceRepo,
    is_safe_repo_id,
)
from .resource_implementation import GenomicResourceImplementation

logger = logging.getLogger(__name__)


_PathOrStr = str | pathlib.Path

# Hosts for which HTTP basic auth over plain http:// is not flagged: a
# credential never leaves the local machine, so cleartext is harmless (and
# localhost/dev GRRs legitimately use it).
_LOCALHOST_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def _check_cache_dir_is_a_local_path(cache_dir: _PathOrStr) -> None:
    """Reject a ``cache_dir`` that carries a URL scheme.

    A GRR cache is always a local directory: the caching protocol serialises
    concurrent downloads with a lockfile, which only provides mutual
    exclusion on a local filesystem. A ``cache_dir`` used to be interpolated
    into ``file://{cache_dir}`` unchecked, so ``s3://bucket/x`` silently
    produced a local directory literally named ``s3:`` instead of the remote
    cache its author asked for. See #473.

    The scheme is detected with ``urlparse`` -- the way
    ``GenomicResourceCachedRepo.__init__`` detects it, so the two checks
    agree -- and NOT by looking for a literal ``://``: a URL scheme needs
    only a ``:``, so ``s3:/bucket/x`` (a plausible typo for
    ``s3://bucket/x``) and ``s3:bucket/x`` are URLs too, and a ``://``
    check accepted both.

    ANY non-empty scheme is refused, ``file`` included: ``cache_dir`` is
    documented as a local path, not a URL, and accepting ``file://`` is what
    invited the ``file://s3://`` confusion in the first place. Every local
    path parses with an empty scheme -- ``/tmp/c:d/cache``, ``~/grr_cache``,
    ``rel/path`` and ``//srv/share`` included.
    """
    value = str(cache_dir)
    try:
        scheme = urlparse(value).scheme
    except ValueError:
        # A value urlparse refuses outright (an unterminated IPv6 bracket)
        # carries no scheme; leave it for the path layer to fail on.
        return
    if not scheme:
        return
    raise ValueError(
        f"the GRR cache_dir must be a local filesystem path, not a URL: "
        f"<{_redact_url_userinfo(value)}>; a GRR cache on a remote "
        f"filesystem is not supported")


class _RepoDefinitionBase(BaseModel):
    # ``hide_input_in_errors=True`` keeps the raw input dict — which may carry
    # a plaintext ``password`` — out of ``str(ValidationError)`` and the
    # traceback. It is inherited by every definition type in the discriminated
    # union. NOTE: it does NOT scrub ``ValidationError.errors()``/``.json()``;
    # ``build_genomic_resource_repository`` additionally wraps validation in a
    # redacted ``ValueError`` to close those paths (see below).
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    id: str | None = None
    public_url: str | None = None

    @field_validator("id")
    @classmethod
    def check_id_is_a_safe_path_segment(cls, value: str | None) -> str | None:
        """Reject an id that is not a single safe filesystem path segment.

        A repository id names that repository's cache directory (see
        ``GenomicResourceCachedRepo._get_or_create_cache_proto``), so an id
        carrying a path separator, an absolute-path prefix, or ``.``/``..``
        moves cached data out of the configured ``cache_dir`` -- an absolute
        id discards it entirely. A control character does the same thing
        less visibly: the cache path is parsed out of a url, and a url
        parser deletes tab, CR and LF, so ``"..\\n"`` arrives as ``..``.

        The id is rejected here, when the definition is loaded, rather than
        sanitised: rewriting it would make the cache layout unpredictable
        and hide the mistake from the operator, exactly as with a duplicate
        id (#460).

        Lives on the base model so it covers every definition type: any of
        them can be a group child, and a top-level repository is cached by
        its own id too.
        """
        # A falsy id is left alone: an empty ``id`` already means "unnamed"
        # -- ``_resolve_repo_id`` synthesises one for it -- and is not a
        # traversal. Naming an unnamed repository is a separate question.
        if value and not is_safe_repo_id(value):
            # ``!r``, not ``<...>``: an id refused for carrying a control
            # character prints as nothing at all otherwise.
            raise ValueError(
                f"invalid repository id {value!r}: a repository id names a "
                f"cache directory, so it must be a single path segment -- "
                f"no path separator, no absolute path, no control "
                f"character, and not '.' or '..'")
        return value

    @field_validator("cache_dir", check_fields=False)
    @classmethod
    def check_cache_dir_is_a_local_path(
        cls, value: _PathOrStr | None,
    ) -> _PathOrStr | None:
        """Reject a ``cache_dir`` that is a URL rather than a local path.

        Rejected here, when the definition is loaded, for the same reason
        ``id`` is (#460): validating it in the build path instead means the
        source repository has already been constructed by the time the bad
        value is noticed, so a refused definition still leaves a directory
        on disk. See ``_check_cache_dir_is_a_local_path`` and #473.

        ``check_fields=False`` because ``cache_dir`` is declared on each of
        the six concrete definition models rather than on this base; the
        validator applies to every one of them that has the field.
        """
        if value is not None:
            _check_cache_dir_is_a_local_path(value)
        return value


class HttpRepoDefinition(_RepoDefinitionBase):
    """Definition for an HTTP/HTTPS genomic resource repository."""

    type: Literal["http"]
    url: str
    user: str | None = None
    password: str | None = None
    cache_dir: _PathOrStr | None = None

    def __repr_args__(self) -> Any:
        # Mask credentials in repr()/str()/f-string interpolation so a
        # diagnostic dump of a definition can never reveal the secret. The
        # real values still travel with the pickled protocol (see
        # fsspec_protocol.py) so dask workers can authenticate. The ``url``
        # itself can embed ``user:pass@host`` userinfo, so redact that too.
        for key, value in super().__repr_args__():
            if key in _CREDENTIAL_KEYS and value is not None:
                yield key, "***"
            elif key == "url" and isinstance(value, str):
                yield key, _redact_url_userinfo(value)
            else:
                yield key, value

    @field_serializer("user", "password")
    def _mask_credential(self, value: str | None) -> str | None:
        # Defense-in-depth: mask credentials in model_dump()/model_dump_json()
        # too (``__repr_args__`` only covers repr/str). The field stays a plain
        # ``str`` so attribute access (``.password``) and the raw definition
        # dict — which is what the auth build path in fsspec_protocol.py reads
        # to construct ``aiohttp.BasicAuth`` — still see the real value.
        return "***" if value is not None else None

    @field_serializer("url")
    def _mask_url_userinfo(self, value: str) -> str:
        # A ``scheme://user:pass@host`` url embeds the credential in its
        # userinfo. Mask it in model_dump()/model_dump_json() (host/path kept)
        # while attribute access and the raw definition dict — read by the auth
        # build path — still return the real, credential-bearing url.
        return _redact_url_userinfo(value)

    @model_validator(mode="after")
    def check_credentials_together(self) -> HttpRepoDefinition:
        if (self.user is None) != (self.password is None):
            raise ValueError(
                "user and password must be provided together or not at all")
        return self

    @model_validator(mode="after")
    def warn_on_insecure_credentials(self) -> HttpRepoDefinition:
        """Warn when basic-auth credentials ride a cleartext http:// URL.

        Credentials are still accepted (localhost/dev GRRs legitimately use
        plain http), but a non-https URL to a non-local host means the
        base64-encoded credentials travel unencrypted, so emit a loud
        warning. The message never includes the password.
        """
        if self.user is None or self.password is None:
            return self
        try:
            parsed = urlparse(self.url)
            scheme = parsed.scheme
            host = (parsed.hostname or "").lower()
        except ValueError:
            # A malformed URL (e.g. an unterminated IPv6 bracket) makes
            # urlparse/.hostname raise. Don't turn that into a confusing
            # ValidationError here — let the definition parse and fail later
            # with the clearer downstream error it produced before this check
            # existed. The credentials never appear in this warning path.
            return self
        if scheme == "https":
            return self
        if host in _LOCALHOST_HOSTS:
            return self
        logger.warning(
            "HTTP basic-auth credentials for GRR %r are configured on a "
            "non-HTTPS URL (host %r); the credentials will be sent "
            "unencrypted. Use an https:// URL for a remote repository.",
            self.id or host, host)
        return self


class UrlRepoDefinition(_RepoDefinitionBase):
    """Definition for a generic URL (http/https/s3) repository."""

    type: Literal["url"]
    url: str
    cache_dir: _PathOrStr | None = None

    def __repr_args__(self) -> Any:
        # The ``url`` can embed ``user:pass@host`` userinfo; redact it from
        # repr()/str()/f-string interpolation.
        for key, value in super().__repr_args__():
            if key == "url" and isinstance(value, str):
                yield key, _redact_url_userinfo(value)
            else:
                yield key, value

    @field_serializer("url")
    def _mask_url_userinfo(self, value: str) -> str:
        # Mask any userinfo credential in model_dump()/model_dump_json(); the
        # real, credential-bearing url is still returned by attribute access
        # and lives in the raw definition dict read by the build path.
        return _redact_url_userinfo(value)


class FileRepoDefinition(_RepoDefinitionBase):
    """Definition for a local filesystem genomic resource repository."""

    type: Literal["file", "dir", "directory"]
    directory: _PathOrStr
    cache_dir: _PathOrStr | None = None
    read_only: bool | None = None


class S3RepoDefinition(_RepoDefinitionBase):
    """Definition for an S3 genomic resource repository."""

    type: Literal["s3"]
    url: str
    endpoint_url: str | None = None
    cache_dir: _PathOrStr | None = None


class EmbeddedRepoDefinition(_RepoDefinitionBase):
    """Definition for an in-memory genomic resource repository."""

    type: Literal["embedded", "memory"]
    content: dict[str, Any] | None = None
    cache_dir: _PathOrStr | None = None


class GroupRepoDefinition(_RepoDefinitionBase):
    """Definition for a group of genomic resource repositories.

    Child repository ids must be distinct across the whole subtree below a
    group, not merely among siblings: an id selects a repository
    (``find_resource``/``get_resource`` take a ``repository_id``) and, for a
    cached repository, names that repository's cache directory. Two
    repositories sharing an id -- at the same level or at different ones --
    leave the second unreachable. Duplicates are a configuration error,
    rejected here when the definition is validated.

    An id is also a directory name -- a cached repository derives each
    repository's cache directory from it -- so it must be a single path
    segment. One that is not (a separator, an absolute path, ``.`` or
    ``..``) would move cached data out of the configured ``cache_dir``, and
    is rejected by the base definition model rather than rewritten (#460).

    The uniqueness check covers *children*, not the definition root: the
    walk starts at ``self.children``, so the root's own id (explicit or
    synthesised) is not compared against any descendant's and a root may
    share an id with one. Benign today -- a group root's id is never handed
    to a protocol as a ``proto_id``, so it names no cache directory, and a
    ``repository_id`` naming the root selects nothing at all (a group
    matches the filter against its children's ids, never its own).

    Spelling ``id`` on a child is optional. A child that omits it gets a
    deterministic id synthesised from its own identity -- its ``url`` or
    ``directory``, or its *path* from the definition root for an
    ``embedded`` / ``memory`` child or a nested ``group``, which have
    neither. The synthesised id is never empty, and two children that would
    synthesise the same id (the same directory listed twice, say) are
    duplicates like any other.
    """

    type: Literal["group"]
    children: list[RepoDefinition]
    cache_dir: _PathOrStr | None = None

    @model_validator(mode="after")
    def check_child_ids_are_unique(self) -> GroupRepoDefinition:
        """Reject a group whose descendants do not have distinct ids.

        Compares the *resolved* ids -- an explicit ``id`` where the child
        spells one, the synthesised id otherwise -- so a pair that would end
        up sharing an id is refused whether or not the collision was
        spelled out.

        The walk covers the whole subtree, not just the direct children.
        Repository ids share one namespace across nesting levels: a
        ``repository_id`` filter is matched against every repository in the
        tree, and a cached repository derives each child's cache directory
        from its id, so two repositories at *different* levels sharing an id
        are as ambiguous as two siblings. Pydantic validates bottom-up, so a
        nested group has already checked its own subtree by the time this
        runs; repeating the walk from here is what catches the cross-level
        pairs a nested group cannot see.
        """
        seen: dict[str, tuple[int, ...]] = {}
        for path, child_id in _walk_resolved_child_ids(self.children):
            if child_id in seen:
                raise ValueError(
                    f"duplicate child repository id <{child_id}> in a group "
                    f"repository definition (children at positions "
                    f"{_format_definition_path(seen[child_id])} and "
                    f"{_format_definition_path(path)}); every repository in a "
                    f"group must have its own unique 'id'")
            seen[child_id] = path
        return self


RepoDefinition = Annotated[
    HttpRepoDefinition
    | FileRepoDefinition
    | S3RepoDefinition
    | UrlRepoDefinition
    | EmbeddedRepoDefinition
    | GroupRepoDefinition,
    Field(discriminator="type"),
]

GroupRepoDefinition.model_rebuild()

# ``hide_input_in_errors`` must be set on the adapter itself: a member model's
# ``model_config`` is NOT honoured for this flag when validating through a
# discriminated-union TypeAdapter, so without this the raw input dict (with a
# plaintext password) would still be echoed in ``str(ValidationError)`` and the
# traceback.
_REPO_DEFINITION_ADAPTER: TypeAdapter[RepoDefinition] = TypeAdapter(
    RepoDefinition, config=ConfigDict(hide_input_in_errors=True))


DEFAULT_DEFINITION = {
    "id": "main-GRR",
    "type": "http",
    "url": "https://grr.iossifovlab.com",
}

# Keys in a repository definition whose values are secrets and must never be
# written to logs or echoed in diagnostics/exceptions.
_CREDENTIAL_KEYS = frozenset({"user", "password"})


def _redact_url_userinfo(value: str) -> str:
    """Mask credentials embedded in a ``scheme://userinfo@host`` URL.

    Only touches strings that parse as a URL whose ``netloc`` carries userinfo
    (``@``); plain paths and other strings are returned unchanged. Two shapes
    of userinfo carry secrets:

    * ``user:pass@host`` — the password after the colon is the secret, so only
      it is replaced with ``***`` (``user:***@host``); the username is kept for
      diagnostics.
    * ``token@host`` — a bearer token / PAT embedded as the SOLE userinfo
      component (no colon) IS itself the secret, so the whole userinfo is
      masked (``***@host``). We must never fabricate a ``token:***`` here — that
      would leave the token fully visible.
    """
    try:
        parsed = urlparse(value)
    except ValueError:
        return value
    if not parsed.netloc or "@" not in parsed.netloc:
        return value
    userinfo, _, hostinfo = parsed.netloc.rpartition("@")
    if not userinfo:
        return value
    username, sep, _password = userinfo.partition(":")
    redacted_netloc = f"{username}:***@{hostinfo}" if sep else f"***@{hostinfo}"
    return parsed._replace(netloc=redacted_netloc).geturl()


# A synthesised repository id -- of a child or of the definition root -- is
# `<slug>_<digest>`: the slug keeps the
# id readable (it shows up in log messages and, for a cached repository, as a
# cache directory name), the digest keeps it unique -- two identities that
# sanitise to the same slug still differ. Only the tail of the identity is
# slugified: the distinguishing part of a URL or a directory path is at its
# end.
_SYNTHESISED_ID_SLUG_MAX = 40
_SYNTHESISED_ID_DIGEST_LEN = 8
_NON_ID_CHARS_RE = re.compile(r"[^A-Za-z0-9]+")


def _repo_definition_identity(url: Any, directory: Any) -> str | None:
    """Return the string identifying a repository, or None if it has none.

    A repository is identified by its ``url`` or its ``directory``; the
    ``embedded``/``memory`` and ``group`` types have neither. Credentials
    embedded in a URL's userinfo are redacted, so a synthesised id can never
    carry a secret and stays stable when a password is rotated.
    """
    if url is not None:
        return _redact_url_userinfo(str(url))
    if directory is not None:
        return str(directory)
    return None


def _format_definition_path(path: tuple[int, ...]) -> str:
    """Render a child's path through ``children`` lists for an error message."""
    return ".".join(str(index) for index in path)


def _synthesise_repo_id(
        identity: str | None, repo_type: str, path: tuple[int, ...]) -> str:
    """Build a deterministic, non-empty id for a repository that omits ``id``.

    Derived from the repository's own identity (its url or directory) so that
    the id is stable across runs and across processes -- the digest is a
    SHA-256 prefix, not Python's salted ``hash()``. A repository with no
    identity of its own (``embedded``/``memory``, or a ``group``) falls back
    to its *path* from the definition root, not its index among its siblings:
    two children at index 0 of two different groups are different
    repositories and must not resolve to one id. Because the path is the full
    index chain, distinct positions always yield distinct ids.

    The definition *root* has an empty path -- there is no index chain to
    fall back on -- so it is named after its type alone: ``group_repo`` and
    ``embedded_repo`` are the shapes that reach it in practice, but the
    branch is not restricted to them (a degenerate root spelling ``url: ""``
    or ``directory: ""`` has no identity either, so it too is named
    ``<type>_repo`` -- ``http_repo``, ``dir_repo`` -- before failing further
    down the builder). Every child has a non-empty path, so no child id
    changes because of this branch.
    """
    if not identity:
        if not path:
            return f"{repo_type}_repo"
        return f"{repo_type}_{'_'.join(str(index) for index in path)}"
    slug = _NON_ID_CHARS_RE.sub("_", identity)[-_SYNTHESISED_ID_SLUG_MAX:]
    slug = slug.strip("_")
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return f"{slug}_{digest[:_SYNTHESISED_ID_DIGEST_LEN]}"


def _resolve_repo_id(
        repo_id: str | None, repo_type: str,
        url: Any, directory: Any, path: tuple[int, ...]) -> str:
    """Return the id a repository is built with -- never the empty string.

    The single source of truth for repository-id resolution, called from
    ``GroupRepoDefinition``'s uniqueness check, from the group builder and
    from the top-level builder. What that buys is exactly one guarantee: no
    repository, root or child, is ever built with ``None`` or ``""`` for an
    id.

    It does NOT make every built id unique. ``check_child_ids_are_unique``
    walks ``self.children`` only, so the ids the *children* resolve to are
    the ids validation reasons about, and the root's resolved id is never
    compared against them -- a root and a descendant may share an id, and
    the descendant wins a ``repository_id`` lookup.

    That is presently benign, which is why extending the walk is left to a
    follow-up rather than done here: a group root's id never becomes a
    ``proto_id``, so it cannot reach the cache-path join that #461 is about,
    and a leaf root has no children to collide with in the first place.

    ``path`` is the repository's position in the definition tree: the empty
    tuple for the root, the chain of ``children`` indices for a child.
    """
    if repo_id:
        return repo_id
    return _synthesise_repo_id(
        _repo_definition_identity(url, directory), repo_type, path)


def _walk_resolved_child_ids(
    children: list[RepoDefinition],
    prefix: tuple[int, ...] = (),
) -> Iterator[tuple[tuple[int, ...], str]]:
    """Yield ``(path, resolved id)`` for every repository below a group.

    Depth-first over the definition tree. A nested group yields its own
    resolved id before its descendants', because a group repository is
    addressable by ``repository_id`` exactly like a leaf one.
    """
    for index, child in enumerate(children):
        path = (*prefix, index)
        yield path, _resolve_repo_id(
            child.id, child.type,
            getattr(child, "url", None),
            getattr(child, "directory", None),
            path)
        if isinstance(child, GroupRepoDefinition):
            yield from _walk_resolved_child_ids(child.children, path)


def redact_definition(definition: Any) -> Any:
    """Return a deep copy of a GRR definition with credentials masked.

    ``user``/``password`` values are replaced with ``"***"`` recursively
    (including inside a group repository's ``children``) so that a definition
    can be logged or embedded in an error message without leaking secrets.
    Credentials embedded in a URL's userinfo (``scheme://user:pass@host``) are
    also scrubbed.
    """
    if isinstance(definition, dict):
        return {
            key: ("***" if key in _CREDENTIAL_KEYS and value is not None
                  else redact_definition(value))
            for key, value in definition.items()
        }
    if isinstance(definition, (list, tuple)):
        return type(definition)(redact_definition(v) for v in definition)
    if isinstance(definition, str):
        return _redact_url_userinfo(definition)
    return definition


def load_definition_file(filename: str) -> Any:
    """Load GRR definition from a YAML file."""
    with open(filename, "rt", encoding="utf8") as infile:
        return yaml.safe_load(infile)


GRR_DEFINITION_FILE_ENV = "GRR_DEFINITION_FILE"


def get_default_grr_definition_path() -> str | None:
    """Return a path to default genomic resources repository definition."""
    env_repo_definition_path = os.environ.get(GRR_DEFINITION_FILE_ENV)
    if env_repo_definition_path is not None:
        logger.debug(
            "found GRR definition from environment variable %s=%s",
            GRR_DEFINITION_FILE_ENV, env_repo_definition_path)
        return env_repo_definition_path
    default_repo_definition_path = f"{os.environ['HOME']}/.grr_definition.yaml"
    logger.debug(
        "checking default GRR definition at %s",
        default_repo_definition_path)
    if pathlib.Path(default_repo_definition_path).exists():
        logger.debug(
            "found GRR definition at %s", default_repo_definition_path)
        return default_repo_definition_path
    return None


def get_default_grr_definition() -> dict[str, Any]:
    """Return default genomic resources repository definition."""
    logger.info("using default GRR definitions")
    definition_path = get_default_grr_definition_path()
    if definition_path:
        return cast(dict[str, Any], load_definition_file(definition_path))
    return copy.deepcopy(DEFAULT_DEFINITION)


def _build_cached_repository(
    repo: GenomicResourceRepo, cache_dir: _PathOrStr,
) -> GenomicResourceCachedRepo:
    """Wrap ``repo`` in a cached repository rooted at a local ``cache_dir``."""
    _check_cache_dir_is_a_local_path(cache_dir)
    return GenomicResourceCachedRepo(repo, f"file://{cache_dir}")


def _build_real_repository(
        proto_type: str = "",
        repo_id: str = "",
        **kwargs: Any) -> GenomicResourceRepo:
    # pylint: disable=too-many-branches
    # Validate ``cache_dir`` BEFORE building anything: a ``directory``
    # repository creates its root on disk when its protocol is built, so
    # checking the cache only on the way out would let a refused definition
    # still leave a directory behind. A definition loaded through
    # ``build_genomic_resource_repository`` has already been refused by
    # ``_RepoDefinitionBase.check_cache_dir_is_a_local_path``; this covers
    # the callers that build a repository from kwargs directly. See #473.
    if "cache_dir" in kwargs:
        _check_cache_dir_is_a_local_path(kwargs["cache_dir"])

    if proto_type == "group":
        repo = _build_group_repository(
            repo_id=repo_id, **kwargs)

    elif proto_type in {"file", "dir", "directory"}:
        root_url = kwargs.pop("directory")

        if root_url is None:
            raise ValueError("missing root url for a file/dir repository")

        if not os.path.isabs(root_url):
            logger.error(
                "for directory/file resources repository we expects an "
                "absolute directory name: %s",
                _redact_url_userinfo(root_url))
            raise ValueError(
                "not an absolute directory name: "
                f"{_redact_url_userinfo(root_url)}")
        root_url = f"file://{root_url}"
        protocol = build_fsspec_protocol(repo_id, root_url, **kwargs)
        repo = GenomicResourceProtocolRepo(protocol)

    elif proto_type == "url":
        root_url = kwargs.pop("url")
        parsed = urlparse(root_url)
        if parsed.scheme not in {"http", "https", "s3"}:
            raise ValueError(
                "unexpected GRR protocol scheme "
                f"{_redact_url_userinfo(root_url)}")
        protocol = build_fsspec_protocol(repo_id, root_url, **kwargs)
        repo = GenomicResourceProtocolRepo(protocol)

    elif proto_type == "http":
        root_url = kwargs.pop("url")

        if urlparse(root_url).scheme not in {"http", "https"}:
            raise ValueError(
                "not an http(s) root url: "
                f"{_redact_url_userinfo(root_url)}")
        protocol = build_fsspec_protocol(repo_id, root_url, **kwargs)
        repo = GenomicResourceProtocolRepo(protocol)

    elif proto_type == "s3":
        root_url = kwargs.pop("url")

        if urlparse(root_url).scheme != "s3":
            raise ValueError(
                "not an s3 root url: "
                f"{_redact_url_userinfo(root_url)}")
        protocol = build_fsspec_protocol(repo_id, root_url, **kwargs)
        repo = GenomicResourceProtocolRepo(protocol)

    elif proto_type in {"embedded", "memory"}:
        root_url = tempfile.mkdtemp(prefix="memory", suffix=repo_id)
        content = kwargs.get("content", {})
        protocol = build_inmemory_protocol(repo_id, root_url, content)
        repo = GenomicResourceProtocolRepo(protocol)

    else:
        raise ValueError(f"unexpected GRR protocol type {proto_type}")

    if "cache_dir" not in kwargs:
        return repo

    cache_dir = kwargs.pop("cache_dir")
    return _build_cached_repository(repo, cache_dir)


def _build_group_repository(
        repo_id: str,
        children: list[dict],
        path: tuple[int, ...] = (),
        **kwargs: Any) -> GenomicResourceRepo:

    # Before any child repository is constructed -- see the note in
    # ``_build_real_repository`` (#473).
    if "cache_dir" in kwargs:
        _check_cache_dir_is_a_local_path(kwargs["cache_dir"])

    result: list[GenomicResourceRepo] = []
    for index, child in enumerate(children):
        # ``path`` must match the one ``GroupRepoDefinition``'s uniqueness
        # check walked the definition with, or the ids that were validated
        # are not the ids the repositories get built with.
        child_path = (*path, index)
        child_id: str = _resolve_repo_id(
            child.pop("id", None), child["type"],
            child.get("url"), child.get("directory"), child_path)
        proto_type = child.pop("type")
        if proto_type == "group":
            repo: GenomicResourceRepo = \
                _build_group_repository(
                    child_id, child.pop("children"), child_path, **child)
            result.append(repo)
            continue

        repo = _build_real_repository(
            proto_type=proto_type, repo_id=child_id, **child)
        result.append(repo)

    repo = GenomicResourceGroupRepo(result, repo_id)

    if "cache_dir" not in kwargs:
        return repo

    cache_dir = kwargs.pop("cache_dir")
    return _build_cached_repository(repo, cache_dir)


def build_genomic_resource_group_repository(
        repo_id: str,
        children: list[GenomicResourceRepo]) -> GenomicResourceRepo:
    return GenomicResourceGroupRepo(children, repo_id)


def build_genomic_resource_repository(
        definition: dict | None = None,
        file_name: str | None = None) -> GenomicResourceRepo:
    """Build a GRR using a definition dict or yaml file."""
    if not definition:
        if file_name is not None:
            definition = load_definition_file(file_name)
        else:
            definition = get_default_grr_definition()
    else:
        if file_name is not None:
            raise ValueError(
                "only one of the definition and file_name parameters"
                "should be provided")

    if definition is None:
        raise ValueError("can't find GRR definition")

    # ``hide_input_in_errors=True`` already keeps the raw input out of
    # ``str(exc)`` and the traceback, but ``ValidationError.errors()`` and
    # ``.json()`` still echo it verbatim — including a plaintext password.
    # Capture only the secret-free ``str(exc)`` (which retains the useful
    # which-field-is-wrong detail) inside the ``except``, then raise a plain
    # ``ValueError`` (the type this factory already uses for bad definitions)
    # OUTSIDE the ``except`` block. Raising outside leaves the ValidationError
    # entirely off the new exception's chain — ``__context__`` is ``None`` —
    # so no code path (including error-aggregation tooling that walks
    # ``__context__`` regardless of ``__suppress_context__``) can reach its
    # leaky ``.errors()``/``.json()`` input. ``from None`` alone would only
    # clear ``__cause__``, leaving the secret on ``__context__``.
    validation_error_msg: str | None = None
    try:
        _REPO_DEFINITION_ADAPTER.validate_python(definition)
    except ValidationError as exc:
        validation_error_msg = str(exc)
    if validation_error_msg is not None:
        raise ValueError(
            f"invalid GRR definition {redact_definition(definition)}: "
            f"{validation_error_msg}")

    logger.info("GRR definition in use: %s", redact_definition(definition))

    definition_copy = copy.deepcopy(definition)

    repo_type = definition_copy.pop("type")
    # The root of a definition gets its id resolved exactly like a child
    # does: a repository built with ``None`` (or ``""``) for an id names no
    # cache directory of its own -- ``os.path.join(cache_url, proto_id)``
    # raises on ``None`` and silently caches into the cache root on ``""``.
    # See #461.
    repo_id = _resolve_repo_id(
        definition_copy.pop("id", None), repo_type,
        definition_copy.get("url"), definition_copy.get("directory"), ())

    if repo_type == "group":
        # ``validate_python`` above rejects a group whose ``children`` is
        # missing or not a list, so these guards are defensive/unreachable in
        # the normal flow; kept as belt-and-braces for callers that might one
        # day reach this with a pre-validated-but-mutated dict.
        if "children" not in definition_copy:
            raise ValueError(
                f"The definition for group repository "
                f"{redact_definition(definition_copy)} "
                "has no children attiribute.")
        if not isinstance(definition_copy["children"], list) and \
                not isinstance(definition_copy["children"], tuple):
            raise ValueError(
                "The children attribute in the definition of a group "
                "repository must be a list")

        children = cast(list[dict], definition_copy.pop("children"))
        repo: GenomicResourceRepo = \
            _build_group_repository(repo_id, children, **definition_copy)
    else:
        repo = _build_real_repository(repo_type, repo_id, **definition_copy)
    repo.definition = definition

    return repo


def build_resource_implementation(
        res: GenomicResource) -> GenomicResourceImplementation:
    """Build a resource implementation from a resource."""
    # pylint: disable=import-outside-toplevel
    from gain.genomic_resources import get_resource_implementation_builder

    builder = get_resource_implementation_builder(res.get_type())
    if builder is None:
        raise ValueError(
            f"unsupported resource implementation type <{res.get_type()}> "
            f"for resource <{res.resource_id}>",
        )

    return builder(res)
