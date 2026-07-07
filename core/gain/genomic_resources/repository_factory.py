"""Provides a factory for building genomic resources repostiories."""

from __future__ import annotations

import copy
import os
import pathlib
import tempfile
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
)
from .resource_implementation import GenomicResourceImplementation

logger = logging.getLogger(__name__)


_PathOrStr = str | pathlib.Path

# Hosts for which HTTP basic auth over plain http:// is not flagged: a
# credential never leaves the local machine, so cleartext is harmless (and
# localhost/dev GRRs legitimately use it).
_LOCALHOST_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


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
        # fsspec_protocol.py) so dask workers can authenticate.
        for key, value in super().__repr_args__():
            if key in _CREDENTIAL_KEYS and value is not None:
                yield key, "***"
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
    """Definition for a group of genomic resource repositories."""

    type: Literal["group"]
    children: list[RepoDefinition]
    cache_dir: _PathOrStr | None = None


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
    """Mask ``user:pass`` embedded in a ``scheme://user:pass@host`` URL.

    Only touches strings that parse as a URL whose ``netloc`` carries userinfo
    (``@``); plain paths and other strings are returned unchanged. The password
    component is replaced with ``***`` (and the username preserved for
    diagnostics — it is not itself masked in a URL, mirroring how the ``user``
    key is only masked as a top-level credential field).
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
    username = userinfo.split(":", 1)[0]
    redacted_netloc = f"{username}:***@{hostinfo}"
    return parsed._replace(netloc=redacted_netloc).geturl()


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


def _build_real_repository(
        proto_type: str = "",
        repo_id: str = "",
        **kwargs: Any) -> GenomicResourceRepo:
    # pylint: disable=too-many-branches
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
                "absolute directory name: %s", root_url)
            raise ValueError(f"not an absolute directory name: {root_url}")
        root_url = f"file://{root_url}"
        protocol = build_fsspec_protocol(repo_id, root_url, **kwargs)
        repo = GenomicResourceProtocolRepo(protocol)

    elif proto_type == "url":
        root_url = kwargs.pop("url")
        parsed = urlparse(root_url)
        if parsed.scheme not in {"http", "https", "s3"}:
            raise ValueError(f"unexpected GRR protocol scheme {root_url}")
        protocol = build_fsspec_protocol(repo_id, root_url, **kwargs)
        repo = GenomicResourceProtocolRepo(protocol)

    elif proto_type == "http":
        root_url = kwargs.pop("url")

        if urlparse(root_url).scheme not in {"http", "https"}:
            raise ValueError(f"not an http(s) root url: {root_url}")
        protocol = build_fsspec_protocol(repo_id, root_url, **kwargs)
        repo = GenomicResourceProtocolRepo(protocol)

    elif proto_type == "s3":
        root_url = kwargs.pop("url")

        if urlparse(root_url).scheme != "s3":
            raise ValueError(f"not an s3 root url: {root_url}")
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
    return GenomicResourceCachedRepo(repo, f"file://{cache_dir}")


def _build_group_repository(
        repo_id: str,
        children: list[dict], **kwargs: Any) -> GenomicResourceRepo:

    result: list[GenomicResourceRepo] = []
    for child in children:
        child_id: str = child.pop("id", "")
        proto_type = child.pop("type")
        if proto_type == "group":
            repo: GenomicResourceRepo = \
                _build_group_repository(
                    child_id, child.pop("children"), **child)
            result.append(repo)
            continue

        repo = _build_real_repository(
            proto_type=proto_type, repo_id=child_id, **child)
        result.append(repo)

    repo = GenomicResourceGroupRepo(result, repo_id)

    if "cache_dir" not in kwargs:
        return repo

    cache_dir = kwargs.pop("cache_dir")
    return GenomicResourceCachedRepo(repo, f"file://{cache_dir}")


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

    try:
        _REPO_DEFINITION_ADAPTER.validate_python(definition)
    except ValidationError as exc:
        # ``hide_input_in_errors=True`` already keeps the raw input out of
        # ``str(exc)`` and the traceback, but ``ValidationError.errors()`` and
        # ``.json()`` still echo it verbatim — including a plaintext password.
        # Re-raise a plain ``ValueError`` (the type this factory already uses
        # for bad definitions) whose message is built only from the redacted
        # definition and the secret-free ``str(exc)`` (which retains the
        # useful which-field-is-wrong detail). ``from None`` drops the leaky
        # ValidationError from the chain so no code path can reach its input.
        raise ValueError(
            f"invalid GRR definition {redact_definition(definition)}: "
            f"{exc}") from None

    logger.info("GRR definition in use: %s", redact_definition(definition))

    definition_copy = copy.deepcopy(definition)

    repo_type = definition_copy.pop("type")
    repo_id = definition_copy.pop("id", None)

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
