# pylint: disable=C0114,C0116,W0212
import dataclasses
import errno
import gzip
import hashlib
import io
import itertools
import logging
import os
import pathlib
import textwrap
import threading
import time
import traceback
import typing
import unittest.mock

import aiohttp
import pyBigWig
import pysam
import pytest
import pytest_mock
import yarl
from gain.genomic_resources import cli as grr_cli
from gain.genomic_resources.cli import cli_browse
from gain.genomic_resources.fsspec_protocol import (
    FsspecReadOnlyProtocol,
    FsspecReadWriteProtocol,
    FsspecRepositoryProtocol,
    build_fsspec_protocol,
)
from gain.genomic_resources.genomic_position_table.table_tabix import (
    TabixGenomicPositionTable,
)
from gain.genomic_resources.reference_genome import (
    build_reference_genome_from_resource,
)
from gain.genomic_resources.repository import (
    GR_MANIFEST_FILE_NAME,
    GenomicResource,
)
from gain.genomic_resources.repository_factory import (
    _REPO_DEFINITION_ADAPTER,
    HttpRepoDefinition,
    UrlRepoDefinition,
    build_genomic_resource_repository,
    redact_definition,
)
from gain.genomic_resources.testing import (
    build_faulty_test_protocol,
    build_filesystem_test_protocol,
    setup_directories,
    setup_genome_bgz,
)
from gain.genomic_resources.testing.faulty_filesystem import FaultyFileSystem
from multidict import CIMultiDict, CIMultiDictProxy
from pydantic import ValidationError

from .conftest import (
    BASIC_RESOURCE_ID,
    BASIC_RESOURCE_LAYOUT,
    serving_http,
)

_SECRET = "s3cr3t-do-not-log"  # ruff: ignore[hardcoded-password-string]


# ---------------------------------------------------------------------------
# Finding 1 — the plaintext password must never be written to the logs when
# a repository is built from an authed http definition.
# ---------------------------------------------------------------------------

def test_build_does_not_log_password(caplog: pytest.LogCaptureFixture) -> None:
    definition = {
        "id": "authed-grr",
        "type": "http",
        "url": "https://grr.example.com",
        "user": "alice",
        "password": _SECRET,
    }
    with caplog.at_level(logging.DEBUG):
        build_genomic_resource_repository(definition)
    assert _SECRET not in caplog.text
    assert "alice" not in caplog.text


def test_build_group_does_not_log_child_password(
    caplog: pytest.LogCaptureFixture,
) -> None:
    definition = {
        "id": "group-grr",
        "type": "group",
        "children": [
            {"type": "http", "url": "https://grr.example.com",
             "user": "alice", "password": _SECRET},
        ],
    }
    with caplog.at_level(logging.DEBUG):
        build_genomic_resource_repository(definition)
    assert _SECRET not in caplog.text


# ---------------------------------------------------------------------------
# Finding 2 — basic-auth credentials on a plain (non-localhost) http:// URL
# must emit a loud WARNING (but keep working); https and localhost stay quiet.
# ---------------------------------------------------------------------------

_WARN_LOGGER = "gain.genomic_resources.repository_factory"


def test_http_with_credentials_warns(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger=_WARN_LOGGER):
        _REPO_DEFINITION_ADAPTER.validate_python(
            {"type": "http", "url": "http://grr.example.com",
             "user": "alice", "password": _SECRET})
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert _SECRET not in caplog.text
    assert "alice" not in caplog.text


def test_https_with_credentials_does_not_warn(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger=_WARN_LOGGER):
        _REPO_DEFINITION_ADAPTER.validate_python(
            {"type": "http", "url": "https://grr.example.com",
             "user": "alice", "password": _SECRET})
    assert not [r for r in caplog.records if r.levelno == logging.WARNING]


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "[::1]"])
def test_http_localhost_with_credentials_does_not_warn(
    host: str, caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger=_WARN_LOGGER):
        _REPO_DEFINITION_ADAPTER.validate_python(
            {"type": "http", "url": f"http://{host}:8080",
             "user": "alice", "password": _SECRET})
    assert not [r for r in caplog.records if r.levelno == logging.WARNING]


# ---------------------------------------------------------------------------
# Finding 3 — repr()/str() of a credential-bearing definition must mask the
# secrets so they cannot leak through diagnostic dumps / f-string logging.
# ---------------------------------------------------------------------------

def test_repr_masks_credentials() -> None:
    definition = HttpRepoDefinition(
        type="http", url="https://grr.example.com",
        user="alice", password=_SECRET)
    assert _SECRET not in repr(definition)
    assert "alice" not in repr(definition)
    assert "***" in repr(definition)


def test_str_masks_credentials() -> None:
    definition = HttpRepoDefinition(
        type="http", url="https://grr.example.com",
        user="alice", password=_SECRET)
    assert _SECRET not in str(definition)
    assert "alice" not in str(definition)


def test_fstring_interpolation_masks_credentials() -> None:
    definition = HttpRepoDefinition(
        type="http", url="https://grr.example.com",
        user="alice", password=_SECRET)
    assert _SECRET not in f"{definition}"


def test_repr_without_credentials_is_unaffected() -> None:
    definition = HttpRepoDefinition(
        type="http", url="https://grr.example.com")
    assert "grr.example.com" in repr(definition)


# ---------------------------------------------------------------------------
# Finding 4 — the public URL of an authed http repo must be credential-free.
# ---------------------------------------------------------------------------

def test_public_url_is_credential_free() -> None:
    proto = build_fsspec_protocol(
        "authed", "https://grr.example.com",
        user="alice", password=_SECRET)
    assert _SECRET not in proto.get_public_url()
    assert "alice" not in proto.get_public_url()


def test_public_url_explicit_is_credential_free() -> None:
    # Its own protocol id: protocols are memoized on ``(proto_id, url)`` for
    # the life of the process, and an explicit ``public_url`` is a different
    # configuration of one -- sharing ``"authed"`` with the test above only
    # worked while a rebuild silently repointed the incumbent (#514).
    proto = build_fsspec_protocol(
        "authed-public-url", "https://grr.example.com",
        user="alice", password=_SECRET,
        public_url="https://public.example.com")
    assert _SECRET not in proto.get_public_url()


# ---------------------------------------------------------------------------
# Finding 5 — grr_browse prints the GRR definition to stdout; when it carries
# http basic-auth credentials, the plaintext user/password must NOT be echoed.
# ---------------------------------------------------------------------------

def test_cli_browse_does_not_print_credentials(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture,
    mocker: pytest_mock.MockerFixture,
) -> None:
    definition_file = tmp_path / ".grr_definition.yaml"
    definition_file.write_text(textwrap.dedent(f"""
        id: "authed_grr"
        type: "http"
        url: "https://grr.example.com"
        user: "alice"
        password: "{_SECRET}"
    """))
    # Stop before touching the network: we only care about the stdout dump of
    # the definition that happens before the repository is listed.
    mocker.patch.object(grr_cli, "run_list_command")

    cli_browse(["--grr", str(definition_file)])
    out, _err = capsys.readouterr()

    assert _SECRET not in out
    assert "alice" not in out
    assert "***" in out


# ---------------------------------------------------------------------------
# Finding 6 — model_dump()/model_dump_json() of an authed http definition must
# mask credentials (defense-in-depth for a downstream consumer that dumps a
# definition) WITHOUT breaking attribute access used to build the auth.
# ---------------------------------------------------------------------------

def test_model_dump_masks_credentials() -> None:
    definition = HttpRepoDefinition(
        type="http", url="https://grr.example.com",
        user="alice", password=_SECRET)
    dumped = definition.model_dump()
    assert dumped["password"] == "***"  # ruff: ignore[hardcoded-password-string]
    assert dumped["user"] == "***"
    assert _SECRET not in str(dumped)
    assert "alice" not in str(dumped)


def test_model_dump_json_masks_credentials() -> None:
    definition = HttpRepoDefinition(
        type="http", url="https://grr.example.com",
        user="alice", password=_SECRET)
    dumped = definition.model_dump_json()
    assert _SECRET not in dumped
    assert "alice" not in dumped
    assert "***" in dumped


def test_attribute_access_returns_real_credentials() -> None:
    # The auth-building path (build_fsspec_protocol -> ``Authorization``
    # header) reads the real credentials via attribute/dict access, NOT via
    # model_dump(), so masking the dump must not touch the plaintext value
    # stored on the model.
    definition = HttpRepoDefinition(
        type="http", url="https://grr.example.com",
        user="alice", password=_SECRET)
    assert definition.password == _SECRET
    assert definition.user == "alice"


def test_model_dump_without_credentials_is_unaffected() -> None:
    definition = HttpRepoDefinition(
        type="http", url="https://grr.example.com")
    dumped = definition.model_dump()
    assert dumped["user"] is None
    assert dumped["password"] is None


# ---------------------------------------------------------------------------
# Finding 7 — a malformed URL carrying credentials must not surface as a
# confusing pydantic ValidationError from the insecure-credentials validator;
# it should keep its prior (clearer) downstream failure path.
# ---------------------------------------------------------------------------

def test_malformed_url_with_credentials_does_not_raise_validationerror(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger=_WARN_LOGGER):
        # "http://[::1" has an unterminated IPv6 bracket: urlparse(...).hostname
        # raises ValueError. The validator must swallow that (warn/skip) so the
        # definition parses here and fails later with a clearer error.
        HttpRepoDefinition(
            type="http", url="http://[::1",
            user="alice", password=_SECRET)
    assert _SECRET not in caplog.text


def test_malformed_url_via_adapter_does_not_raise_validationerror() -> None:
    try:
        _REPO_DEFINITION_ADAPTER.validate_python(
            {"type": "http", "url": "http://[::1",
             "user": "alice", "password": _SECRET})
    except ValidationError as exc:  # pragma: no cover - fails pre-fix
        pytest.fail(
            f"malformed URL raised ValidationError from validator: {exc}")


# ---------------------------------------------------------------------------
# Finding 8 — a one-sided credential (only user OR only password, a plausible
# operator typo) trips the check_credentials_together validator. Pydantic
# embeds the ENTIRE input dict — including the plaintext password — into the
# ValidationError str(), traceback, .errors() and .json(). None of those code
# paths may surface the secret.
# ---------------------------------------------------------------------------

def _walk_exception_chain(exc: BaseException) -> list[BaseException]:
    """Return exc plus every exception linked via __cause__/__context__."""
    seen: list[BaseException] = []
    stack: list[BaseException | None] = [exc]
    while stack:
        current = stack.pop()
        if current is None or current in seen:
            continue
        seen.append(current)
        stack.extend((current.__cause__, current.__context__))
    return seen


@pytest.mark.parametrize("bad", [
    {"password": _SECRET},          # only password
    {"user": _SECRET},              # only user
])
def test_build_one_sided_credential_does_not_leak_secret(bad: dict) -> None:
    definition = {"type": "http", "url": "https://grr.example.com", **bad}
    with pytest.raises(ValueError) as excinfo:
        build_genomic_resource_repository(definition)
    exc = excinfo.value
    tb = "".join(traceback.format_exception(exc))
    assert _SECRET not in str(exc)
    assert _SECRET not in tb
    # The re-raised ValueError must carry NO attached ValidationError as
    # context: pydantic's ``.errors()``/``.json()`` still echo the plaintext
    # password, and error-aggregation tooling walks ``__context__`` regardless
    # of ``__suppress_context__``. Raising outside the ``except`` block leaves
    # the chain empty.
    assert exc.__context__ is None
    assert exc.__cause__ is None
    # Defense-in-depth: no exception reachable through the chain (should be
    # only ``exc`` itself) may surface the secret in any of its views.
    for linked in _walk_exception_chain(exc):
        assert _SECRET not in str(linked)
        if isinstance(linked, ValidationError):
            assert _SECRET not in str(linked.errors())
            assert _SECRET not in linked.json()
    # A useful, redacted diagnostic must still name what went wrong.
    assert "together" in str(exc)


def test_adapter_one_sided_credential_str_and_traceback_are_clean() -> None:
    # The bare adapter path (used directly by callers) must at least keep the
    # secret out of str() and the traceback via hide_input_in_errors=True.
    with pytest.raises(ValidationError) as excinfo:
        _REPO_DEFINITION_ADAPTER.validate_python(
            {"type": "http", "url": "https://grr.example.com",
             "password": _SECRET})
    exc = excinfo.value
    assert _SECRET not in str(exc)
    assert _SECRET not in "".join(traceback.format_exception(exc))


# ---------------------------------------------------------------------------
# Finding 9 — credentials embedded in a URL's userinfo (scheme://user:pass@host)
# must be scrubbed by redact_definition; plain URLs are left untouched.
# ---------------------------------------------------------------------------

def test_redact_definition_scrubs_url_userinfo() -> None:
    definition = {"type": "url", "url": f"http://alice:{_SECRET}@host/path"}
    redacted = redact_definition(definition)
    assert _SECRET not in redacted["url"]
    # the username is preserved for diagnostics, only the password is masked
    assert redacted["url"] == "http://alice:***@host/path"
    # original definition is not mutated
    assert _SECRET in definition["url"]


def test_redact_definition_scrubs_url_token_only_userinfo() -> None:
    # A bearer token / PAT embedded as the SOLE userinfo component (no colon):
    # scheme://<token>@host. The whole userinfo is the secret, so it must be
    # fully masked (***@host), never split into a fake ``token:***``.
    token = "ghp_SUPERSECRETTOKEN123"  # ruff: ignore[hardcoded-password-string]
    definition = {"type": "url", "url": f"https://{token}@grr.example.com/path"}
    redacted = redact_definition(definition)
    assert token not in redacted["url"]
    assert redacted["url"] == "https://***@grr.example.com/path"
    # original definition is not mutated
    assert token in definition["url"]


def test_redact_definition_scrubs_path_userinfo() -> None:
    redacted = redact_definition(
        {"type": "http", "url": f"https://bob:{_SECRET}@grr.example.com"})
    assert _SECRET not in redacted["url"]


def test_redact_definition_plain_url_unchanged() -> None:
    definition = {"type": "url", "url": "https://grr.example.com/path"}
    assert redact_definition(definition)["url"] == \
        "https://grr.example.com/path"


def test_redact_definition_non_url_string_unchanged() -> None:
    definition = {"type": "directory", "directory": "/data/grr@archive"}
    assert redact_definition(definition)["directory"] == "/data/grr@archive"


# ---------------------------------------------------------------------------
# Finding 10 — a definition whose ``url`` carries userinfo credentials but whose
# scheme does NOT match the declared repo ``type`` passes schema validation
# (``url`` is a bare ``str``; the scheme is only checked in the builder) and
# then reaches the scheme-mismatch ``raise ValueError`` branches in
# ``_build_real_repository``. Those messages interpolate the RAW ``root_url``,
# leaking the embedded password in ``str(exc)`` AND the traceback. The message
# must be redacted (``user:***@host``) while still naming the scheme problem
# and keeping the host for debuggability.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(("repo_type", "needle"), [
    ("http", "not an http(s) root url"),
    ("url", "unexpected GRR protocol scheme"),
    ("s3", "not an s3 root url"),
])
def test_build_scheme_mismatch_does_not_leak_url_credential(
    repo_type: str, needle: str,
) -> None:
    definition = {
        "type": repo_type,
        "url": f"ftp://alice:{_SECRET}@grr.example.com/path",
    }
    with pytest.raises(ValueError) as excinfo:
        build_genomic_resource_repository(definition)
    exc = excinfo.value
    tb = "".join(traceback.format_exception(exc))
    # The secret must not appear in the message or anywhere in the traceback.
    assert _SECRET not in str(exc)
    assert _SECRET not in tb
    # No linked exception (context/cause) may surface the secret either.
    for linked in _walk_exception_chain(exc):
        assert _SECRET not in str(linked)
    # The message still names the scheme problem (debuggability preserved).
    assert needle in str(exc)
    # ...and still shows the redacted host, so the error stays useful.
    assert "grr.example.com" in str(exc)
    assert "alice:***@grr.example.com" in str(exc)


# ---------------------------------------------------------------------------
# Finding 11 — credentials embedded in a repo url's userinfo
# (``scheme://user:pass@host``) are a functional HTTP Basic auth config, but
# they escape verbatim through the protocol's DISPLAY/IDENTITY url, which is
# never redacted. ``get_url()``/``get_public_url()`` (and everything that
# serializes them: the web_annotation JSON response, the persisted about.html
# docs, the ``logger.exception`` call in ``about.html`` generation) must expose
# a userinfo-FREE url, while the fetch path keeps the credentialed url so
# aiohttp/htslib can still authenticate.
# ---------------------------------------------------------------------------

def test_protocol_get_public_url_strips_url_userinfo() -> None:
    proto = build_fsspec_protocol(
        "f11-pub", f"https://alice:{_SECRET}@grr.example.com/path")
    pub = proto.get_public_url()
    assert _SECRET not in pub
    assert "alice" not in pub
    # host and path are preserved so the url stays useful.
    assert "grr.example.com" in pub
    assert pub.endswith("/path")


def test_protocol_get_url_strips_url_userinfo() -> None:
    proto = build_fsspec_protocol(
        "f11-url", f"https://alice:{_SECRET}@grr.example.com/path")
    url = proto.get_url()
    assert _SECRET not in url
    assert "alice" not in url
    assert "grr.example.com" in url
    assert url.endswith("/path")


def test_protocol_fetch_url_keeps_url_userinfo() -> None:
    # The credential MUST still reach aiohttp: for URL-embedded userinfo the
    # only place the credential travels is the fetched url string itself
    # (``_build_filesystem`` reads user/password from kwargs, which are empty
    # here). So the private fetch base — and every file url derived from it —
    # must retain the userinfo even though the display url does not.
    proto = build_fsspec_protocol(
        "f11-fetch", f"https://alice:{_SECRET}@grr.example.com/path")
    assert _SECRET in proto._fetch_url
    resource = GenomicResource("sub/res", (1, 0), proto, {})
    file_url = proto.get_resource_file_url(resource, "data.txt")
    assert _SECRET in file_url
    assert file_url.startswith(
        f"https://alice:{_SECRET}@grr.example.com/path/")


def test_repo_proto_public_url_strips_url_userinfo() -> None:
    repo = build_genomic_resource_repository(
        {"id": "f11-repo", "type": "http",
         "url": f"https://alice:{_SECRET}@grr.example.com"})
    pub = repo.proto.get_public_url()
    assert _SECRET not in pub
    assert "alice" not in pub
    assert "grr.example.com" in pub


def test_protocol_url_userinfo_keeps_port() -> None:
    proto = build_fsspec_protocol(
        "f11-port", f"https://u:{_SECRET}@grr.example.com:8443/x")
    url = proto.get_url()
    assert _SECRET not in url
    assert "grr.example.com:8443" in url


def test_protocol_url_userinfo_keeps_ipv6_host_port() -> None:
    proto = build_fsspec_protocol(
        "f11-ipv6", f"https://u:{_SECRET}@[::1]:9000/x")
    url = proto.get_url()
    assert _SECRET not in url
    assert "[::1]:9000" in url


def test_protocol_url_without_userinfo_is_unchanged() -> None:
    proto = build_fsspec_protocol(
        "f11-plain", "https://grr.example.com/path")
    assert proto.get_url() == "https://grr.example.com/path"
    assert proto.get_public_url() == "https://grr.example.com/path"


def test_protocol_canonical_credentials_public_url_is_clean() -> None:
    # Regression lock-in: the canonical user/password-kwargs path (no userinfo
    # in the url) already yields a clean public url — keep it that way.
    proto = build_fsspec_protocol(
        "f11-canon", "https://grr.example.com",
        user="alice", password=_SECRET)
    assert _SECRET not in proto.get_public_url()
    assert proto.get_public_url() == "https://grr.example.com"


def test_httprepodefinition_url_userinfo_masked_in_repr_and_dump() -> None:
    definition = HttpRepoDefinition(
        type="http", url=f"https://alice:{_SECRET}@grr.example.com/path")
    assert _SECRET not in repr(definition)
    assert _SECRET not in str(definition)
    assert _SECRET not in f"{definition}"
    assert _SECRET not in str(definition.model_dump())
    assert _SECRET not in definition.model_dump_json()
    # host is preserved; only the password userinfo is masked.
    assert "grr.example.com" in definition.model_dump_json()
    assert "alice:***@grr.example.com" in definition.model_dump_json()


def test_httprepodefinition_url_attribute_returns_real_value() -> None:
    # Masking is display/dump-only: the real ``.url`` must still carry the
    # credential so the build path (which reads the raw dict / attribute) can
    # authenticate.
    real = f"https://alice:{_SECRET}@grr.example.com/path"
    definition = HttpRepoDefinition(type="http", url=real)
    assert definition.url == real


def test_urlrepodefinition_url_userinfo_masked_in_dump() -> None:
    definition = UrlRepoDefinition(
        type="url", url=f"https://alice:{_SECRET}@grr.example.com/path")
    assert _SECRET not in repr(definition)
    assert _SECRET not in definition.model_dump_json()
    assert _SECRET not in str(definition.model_dump())
    # real attribute intact for the fetch path
    assert definition.url == f"https://alice:{_SECRET}@grr.example.com/path"


# ---------------------------------------------------------------------------
# Finding A-1 — the ``__new__`` protocol-cache-HIT DEBUG log line interpolates
# the raw url, leaking the URL-embedded password whenever the SAME authed
# url-userinfo protocol is built twice (per-worker / per-request re-entry) with
# DEBUG logging on. The log line must be userinfo-free. The credentialed cache
# KEY is retained (see the fix rationale: keying on the stripped url would let a
# second build with DIFFERENT credentials for the same host+path reuse the
# first protocol and authenticate with the WRONG credentials) but the cache
# dict/key is never logged, so it cannot leak.
# ---------------------------------------------------------------------------

def test_protocol_cache_hit_debug_log_is_credential_free(
    caplog: pytest.LogCaptureFixture,
) -> None:
    url = f"https://alice:{_SECRET}@grr.example.com/path"
    with caplog.at_level(logging.DEBUG):
        first = build_fsspec_protocol("a1-cache", url)
        second = build_fsspec_protocol("a1-cache", url)
    # The second build must have taken the cache-HIT branch (same instance).
    assert first is second
    # ...and that branch's DEBUG line must not carry the credential.
    assert _SECRET not in caplog.text
    assert "alice" not in caplog.text
    # the cache-hit line is still emitted (host kept for debuggability).
    assert "already exists" in caplog.text
    assert "grr.example.com" in caplog.text


def test_protocol_cache_different_credentials_do_not_collide() -> None:
    # Correctness lock-in for keeping the cache KEY credentialed: two builds
    # that differ ONLY in credentials for the same host+path must NOT share a
    # cached protocol, or the second would authenticate with the first's
    # credentials. Each must carry its own fetch url.
    first = build_fsspec_protocol(
        "a1-diff", "https://alice:secretA@grr.example.com/path")
    second = build_fsspec_protocol(
        "a1-diff", "https://bob:secretB@grr.example.com/path")
    assert first is not second
    assert "secretA" in first._fetch_url
    assert "secretB" in second._fetch_url


def test_protocol_cache_non_userinfo_url_keys_identically() -> None:
    # For a userinfo-free url the (stripped) display url == the url, so cache
    # behavior is unchanged: a second build hits the same instance.
    first = build_fsspec_protocol(
        "a1-plain", "https://grr.example.com/path")
    second = build_fsspec_protocol(
        "a1-plain", "https://grr.example.com/path")
    assert first is second


# ---------------------------------------------------------------------------
# Finding A-2 — a fetch failure on an authed url-userinfo protocol propagates
# an exception whose message + traceback embed the credential-bearing
# ``_fetch_url`` (e.g. ``FileNotFoundError: https://a:p@host/.CONTENTS.json``).
# GAIn-authored fetch entry points (``load_contents``/``md5_contents``) re-raise
# with the userinfo stripped, and the cache-run failure aggregation in
# ``cached_repository`` must redact the interpolated error, so neither the
# raised message nor the ERROR log leaks the secret. The credentialed
# ``_fetch_url`` is still used for the actual fetch.
# ---------------------------------------------------------------------------

def test_load_contents_fetch_failure_does_not_leak_url_credential() -> None:
    # Port 1 refuses immediately: a fast, network-free fetch failure.
    proto = build_fsspec_protocol(
        "a2-load", f"https://alice:{_SECRET}@127.0.0.1:1/path")
    with pytest.raises(OSError) as excinfo:
        proto.load_contents()
    exc = excinfo.value
    tb = "".join(traceback.format_exception(exc))
    assert _SECRET not in str(exc)
    assert _SECRET not in tb
    for linked in _walk_exception_chain(exc):
        assert _SECRET not in str(linked)
    # host kept so the error stays diagnosable.
    assert "127.0.0.1" in str(exc)


def test_md5_contents_fetch_failure_does_not_leak_url_credential() -> None:
    proto = build_fsspec_protocol(
        "a2-md5", f"https://alice:{_SECRET}@127.0.0.1:1/path")
    with pytest.raises(OSError) as excinfo:
        proto.md5_contents()
    exc = excinfo.value
    tb = "".join(traceback.format_exception(exc))
    assert _SECRET not in str(exc)
    assert _SECRET not in tb
    for linked in _walk_exception_chain(exc):
        assert _SECRET not in str(linked)


def test_fetch_failure_keeps_credential_in_fetch_url() -> None:
    # Regression: the credential must still travel on the private fetch url so
    # aiohttp can authenticate; only the surfaced error is redacted.
    proto = build_fsspec_protocol(
        "a2-keep", f"https://alice:{_SECRET}@127.0.0.1:1/path")
    assert _SECRET in proto._fetch_url


def test_open_raw_file_fetch_failure_does_not_leak_url_credential() -> None:
    # gain#629 — ``open_raw_file`` hands the credential-bearing resource file
    # url straight to fsspec; a failing open (port 1 refuses immediately) must
    # surface with the userinfo stripped from the message, the traceback and
    # every exception linked via ``__cause__``/``__context__``.
    proto = build_fsspec_protocol(
        "i629-open", f"https://alice:{_SECRET}@127.0.0.1:1/path")
    resource = GenomicResource("sub/res", (1, 0), proto, {})
    with pytest.raises(OSError) as excinfo:
        proto.open_raw_file(resource, "data.txt")
    exc = excinfo.value
    tb = "".join(traceback.format_exception(exc))
    assert _SECRET not in str(exc)
    assert "alice" not in str(exc)
    assert _SECRET not in tb
    for linked in _walk_exception_chain(exc):
        assert _SECRET not in str(linked)
    # host and path preserved so the error stays diagnosable.
    assert "127.0.0.1" in str(exc)
    assert "data.txt" in str(exc)


def test_open_raw_file_failure_without_userinfo_is_unchanged() -> None:
    # A url with no userinfo must propagate the original error untouched —
    # same type, message still carrying the full url for diagnosability.
    proto = build_fsspec_protocol("i629-plain", "https://127.0.0.1:1/path")
    resource = GenomicResource("sub/res", (1, 0), proto, {})
    with pytest.raises(OSError) as excinfo:
        proto.open_raw_file(resource, "data.txt")
    assert "https://127.0.0.1:1/path/sub/res(1.0)/data.txt" \
        in str(excinfo.value)
    # ...and it must be the ORIGINAL exception, not a redacted rebuild: the
    # rebuild path raises outside the ``except`` block, leaving the chain
    # empty, while fsspec's own error keeps the underlying aiohttp failure
    # linked via ``__cause__``/``__context__``.
    exc = excinfo.value
    assert exc.__cause__ is not None or exc.__context__ is not None


def test_open_raw_file_write_refusal_does_not_leak_url_credential() -> None:
    # gain#1106 — refusing a write against a READ-ONLY protocol interpolates
    # the resource file url into the message itself, before any handle
    # exists, so the handle wrapper of ADR 0023 cannot reach it. No I/O is
    # attempted: the refusal fires on the mode alone.
    proto = build_fsspec_protocol(
        "i1106-refuse", f"https://alice:{_SECRET}@example.org/repo")
    resource = GenomicResource("sub/res", (1, 0), proto, {})
    with pytest.raises(OSError) as excinfo:
        proto.open_raw_file(resource, "data.txt", "wt")
    exc = excinfo.value
    # Pinned whole, not probed for fragments: asserting only that the host and
    # the filename survive is satisfied by ``_strip_netloc_userinfo``, which
    # drops the scheme along with the userinfo and leaves a url that no longer
    # says what it addresses. No chain walk -- the ``raise`` is not inside an
    # ``except``, so there is nothing linked to walk.
    assert str(exc) == (
        "Read-Only protocol i1106-refuse trying to open "
        "https://example.org/repo/sub/res(1.0)/data.txt for writing")
    # Kept alongside the pin rather than subsumed by it: this is what still
    # fails if a future regression is "fixed" by pasting the new, leaking
    # message into the expected string above.
    assert _SECRET not in "".join(traceback.format_exception(exc))


def test_open_raw_file_forwards_the_credential(
    mocker: pytest_mock.MockerFixture,
) -> None:
    # The fence for gain#1106's redaction: it belongs on the message, not on
    # ``filepath`` itself. Redacting at the assignment reads as a tidier fix,
    # passes every leak test in this file, and silently breaks EVERY authed
    # read -- fsspec would be handed the credential-free display url. Only the
    # call args can show it, exactly as for ``get_file_content``.
    proto = build_fsspec_protocol(
        "i1106-forward", f"https://alice:{_SECRET}@example.org/repo")
    resource = GenomicResource("sub/res", (1, 0), proto, {})
    opened = mocker.patch.object(proto.filesystem, "open")

    proto.open_raw_file(resource, "data.txt")

    assert _SECRET in opened.call_args.args[0]
    assert opened.call_args.args[0].endswith("data.txt")


def test_open_raw_file_write_refusal_without_userinfo_is_unchanged() -> None:
    # The redaction must not cost the unauthenticated case anything: with no
    # userinfo to strip, the refusal still names the whole url. Guards against
    # "fixing" the leak by dropping the url from the message.
    proto = build_fsspec_protocol("i1106-plain", "https://example.org/repo")
    resource = GenomicResource("sub/res", (1, 0), proto, {})
    with pytest.raises(OSError) as excinfo:
        proto.open_raw_file(resource, "data.txt", "wt")
    assert str(excinfo.value) == (
        "Read-Only protocol i1106-plain trying to open "
        "https://example.org/repo/sub/res(1.0)/data.txt for writing")


def test_sqlite_metadata_db_fetch_failure_does_not_leak_url_credential(
    mocker: pytest_mock.MockerFixture,
) -> None:
    # The repository sqlite metadata DB is fetched from the same
    # credential-bearing base url. Force the existence probe to succeed so the
    # failing fsspec open (port 1 refuses) is what surfaces.
    proto = build_fsspec_protocol(
        "i629-sqlite", f"https://alice:{_SECRET}@127.0.0.1:1/path")
    mocker.patch.object(proto.filesystem, "exists", return_value=True)
    with pytest.raises(OSError) as excinfo:
        proto.open_repository_metadata()
    exc = excinfo.value
    tb = "".join(traceback.format_exception(exc))
    assert _SECRET not in str(exc)
    assert _SECRET not in tb
    for linked in _walk_exception_chain(exc):
        assert _SECRET not in str(linked)
    assert "127.0.0.1" in str(exc)


def test_sqlite_metadata_db_read_failure_does_not_leak_url_credential(
    mocker: pytest_mock.MockerFixture,
) -> None:
    # The sqlite metadata DB is opened AND read in one call; a failure
    # surfacing at read time (range-GET after a successful open) embeds the
    # credential-bearing url just like an open failure and must be redacted
    # the same way.
    url = f"https://alice:{_SECRET}@grr.example.com/path/.CONTENTS.sqlite3.gz"
    proto = build_fsspec_protocol(
        "i629-sqlite-read", f"https://alice:{_SECRET}@grr.example.com/path")
    mocker.patch.object(proto.filesystem, "exists", return_value=True)
    handle = mocker.MagicMock()
    handle.__enter__.return_value.read.side_effect = FileNotFoundError(url)
    mocker.patch.object(proto.filesystem, "open", return_value=handle)
    with pytest.raises(OSError) as excinfo:
        proto.open_repository_metadata()
    exc = excinfo.value
    tb = "".join(traceback.format_exception(exc))
    assert _SECRET not in str(exc)
    assert _SECRET not in tb
    for linked in _walk_exception_chain(exc):
        assert _SECRET not in str(linked)
    assert "grr.example.com" in str(exc)


def test_cache_worklist_failure_aggregation_redacts_url_credential(
    caplog: pytest.LogCaptureFixture,
    mocker: pytest_mock.MockerFixture,
) -> None:
    from gain.genomic_resources import cached_repository as cr

    url = f"https://alice:{_SECRET}@grr.example.com/res/data.txt"
    cached_proto = mocker.MagicMock()
    cached_proto.classify_cached_resource_file.side_effect = \
        FileNotFoundError(url)
    resource = mocker.MagicMock()
    resource.resource_id = "res"

    with caplog.at_level(logging.ERROR):
        _worklist, _total, _cached, failures = cr._build_cache_worklist(
            cached_proto, resource, ["data.txt"], workers=1)

    assert _SECRET not in caplog.text
    assert "alice" not in caplog.text
    assert all(_SECRET not in failure for failure in failures)
    # host preserved so the failure summary stays useful.
    assert any("grr.example.com" in failure for failure in failures)


# ---------------------------------------------------------------------------
# Finding A-3 — the download retry loop in ``copy_resource_file`` renders the
# error it caught, both into its retry WARNING and into the exception it
# finally re-raises. An ``aiohttp`` error carries the credential-bearing fetch
# url in its own message, so an authed url-userinfo GRR writes the password to
# the log once per retry and again wherever the propagated error is rendered.
#
# The module's existing redaction does not reach here: ``open_raw_file``
# redacts the OPEN only -- by its own documented contract -- and the download
# streams through reads on the handle that open returned. See gain#620.
#
# Redaction must not change RETRYABILITY: a rebuilt error is classified
# differently from the one aiohttp raised, so redacting before the retry
# decision would silently turn a transient failure into a hard one. That is
# what ``_EXPECTED_BACKOFFS`` pins.
# ---------------------------------------------------------------------------

#: The one file ``BASIC_RESOURCE_LAYOUT`` puts in the resource.
_DOWNLOAD_FILE_NAME = "data.txt"

#: The source file as the source protocol serves it, and the temp file the
#: download stages into. Both are globs: the resource directory carries a
#: version suffix, and the ``.part`` name has a uuid the protocol mints
#: itself, so neither can be named exactly.
_DOWNLOAD_SOURCE_FILE = f"*/{_DOWNLOAD_FILE_NAME}"
_PARTIAL_DOWNLOAD = "*.part"

#: The authority the fetch url carries. The non-default port is deliberate:
#: what must survive redaction is the whole authority, not just the host.
_AUTHED_HOST_PORT = "grr.example.com:8443"

#: The credential-bearing url an authed GRR fetches through, and which
#: aiohttp embeds in the errors it raises.
_AUTHED_FETCH_URL = (
    f"https://alice:{_SECRET}@{_AUTHED_HOST_PORT}"
    f"/repo/{_DOWNLOAD_FILE_NAME}")

#: One backoff between each pair of the four attempts the protocol makes.
#: Deliberately a literal and NOT derived from ``_COPY_MAX_ATTEMPTS``: derived,
#: it would move with the constant, so a redaction that reclassified the error
#: out of the retryable set would lower the expectation to 0 and still pass.
_EXPECTED_BACKOFFS = 3


def _an_aiohttp_error_carrying(url: str) -> aiohttp.ClientResponseError:
    """Return the error aiohttp raises for a 500 on ``url``.

    Deliberately a real ``ClientResponseError`` built from a real
    ``RequestInfo`` rather than one carrying a hand-written message: what
    leaks is aiohttp's OWN rendering of the request url, so a planted
    message string would prove nothing about the leak.
    """
    request_info = aiohttp.RequestInfo(
        yarl.URL(url), "GET", CIMultiDictProxy(CIMultiDict()), yarl.URL(url))
    return aiohttp.ClientResponseError(
        request_info, (), status=500, message="internal server error")


class _AFailingDownload(typing.NamedTuple):
    """Everything a test needs to drive one failing download."""

    dest_proto: FsspecReadWriteProtocol
    src_res: GenomicResource
    dest_res: GenomicResource
    dest_fs: FaultyFileSystem
    sleep: unittest.mock.MagicMock


def _a_failing_download(
    tmp_path: pathlib.Path, mocker: pytest_mock.MockerFixture,
    error: BaseException,
) -> _AFailingDownload:
    """Arrange a download whose every source read fails with ``error``.

    The backoff is patched here rather than in each test, because a test
    that forgot to would not fail -- it would sleep through the protocol's
    real 5s, 15s and 45s schedule.
    """
    src_proto, src_fs = build_faulty_test_protocol(
        tmp_path / "src", BASIC_RESOURCE_LAYOUT)
    src_res = src_proto.get_resource(BASIC_RESOURCE_ID)
    dest_proto, dest_fs = build_faulty_test_protocol(tmp_path / "dst")
    dest_res = GenomicResource(
        src_res.resource_id, src_res.version, dest_proto)
    src_fs.fail_read(_DOWNLOAD_SOURCE_FILE, error)
    sleep = mocker.patch("gain.genomic_resources.fsspec_protocol.time.sleep")
    return _AFailingDownload(dest_proto, src_res, dest_res, dest_fs, sleep)


def _the_error_a_failed_copy_raises(
    download: _AFailingDownload,
) -> BaseException:
    """Run the copy to exhaustion and hand back the error it raised.

    Deliberately not ``pytest.raises(SomeType)``: what type survives
    redaction is itself under test below, so the harness that drives the
    copy must not pin it.
    """
    try:
        download.dest_proto.copy_resource_file(
            download.src_res, download.dest_res, _DOWNLOAD_FILE_NAME)
    except Exception as error:  # ruff: ignore[blind-except] - the type is what we assert on
        return error
    pytest.fail("the download was expected to fail")


def test_download_retry_warning_does_not_leak_url_credential(
    tmp_path: pathlib.Path, mocker: pytest_mock.MockerFixture,
    caplog: pytest.LogCaptureFixture,
) -> None:
    download = _a_failing_download(
        tmp_path, mocker, _an_aiohttp_error_carrying(_AUTHED_FETCH_URL))

    with caplog.at_level(logging.WARNING):
        _the_error_a_failed_copy_raises(download)

    assert _SECRET not in caplog.text
    assert "alice" not in caplog.text
    # host AND port preserved, so the retry line stays diagnosable.
    assert _AUTHED_HOST_PORT in caplog.text


def test_download_terminal_failure_does_not_leak_url_credential(
    tmp_path: pathlib.Path, mocker: pytest_mock.MockerFixture,
) -> None:
    download = _a_failing_download(
        tmp_path, mocker, _an_aiohttp_error_carrying(_AUTHED_FETCH_URL))

    error = _the_error_a_failed_copy_raises(download)

    assert _SECRET not in str(error)
    assert "alice" not in str(error)
    # host, port, path and status preserved so the failure stays
    # diagnosable -- only the userinfo is dropped.
    assert _AUTHED_HOST_PORT in str(error)
    assert f"/repo/{_DOWNLOAD_FILE_NAME}" in str(error)
    assert "500" in str(error)


def test_download_terminal_failure_chain_does_not_leak_url_credential(
    tmp_path: pathlib.Path, mocker: pytest_mock.MockerFixture,
) -> None:
    download = _a_failing_download(
        tmp_path, mocker, _an_aiohttp_error_carrying(_AUTHED_FETCH_URL))

    error = _the_error_a_failed_copy_raises(download)

    # The redacted rebuild is raised outside the ``except`` block, so the
    # error aiohttp raised is not linked to it and cannot leak through a
    # chain walk or a rendered traceback.
    assert _SECRET not in "".join(traceback.format_exception(error))
    for linked in _walk_exception_chain(error):
        assert _SECRET not in str(linked)


def test_download_redaction_keeps_the_error_retryable(
    tmp_path: pathlib.Path, mocker: pytest_mock.MockerFixture,
) -> None:
    download = _a_failing_download(
        tmp_path, mocker, _an_aiohttp_error_carrying(_AUTHED_FETCH_URL))

    _the_error_a_failed_copy_raises(download)

    # Every attempt was made: redaction on the way out did not reclassify
    # the error out of the retryable set.
    assert download.sleep.call_count == _EXPECTED_BACKOFFS


def test_download_failure_without_userinfo_propagates_unchanged(
    tmp_path: pathlib.Path, mocker: pytest_mock.MockerFixture,
) -> None:
    plain_error = _an_aiohttp_error_carrying(
        f"https://{_AUTHED_HOST_PORT}/repo/{_DOWNLOAD_FILE_NAME}")
    download = _a_failing_download(tmp_path, mocker, plain_error)

    error = _the_error_a_failed_copy_raises(download)

    # Nothing to redact, so the ORIGINAL object propagates -- identity, and
    # with it the exception type and the traceback a rebuild would cost.
    assert error is plain_error


def test_download_cleanup_failure_does_not_leak_url_credential(
    tmp_path: pathlib.Path, mocker: pytest_mock.MockerFixture,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # The temp-file cleanup runs from the download's ``finally``, so it runs
    # WHILE the credential-bearing read error is propagating. Logging the
    # cleanup failure with ``exc_info`` renders the whole active chain, which
    # is the read error -- so the secret reaches the log by a second route,
    # one the retry loop's own redaction never sees.
    download = _a_failing_download(
        tmp_path, mocker, _an_aiohttp_error_carrying(_AUTHED_FETCH_URL))
    download.dest_fs.fail_rm(
        _PARTIAL_DOWNLOAD, OSError("the store refused the removal"))

    with caplog.at_level(logging.WARNING):
        _the_error_a_failed_copy_raises(download)

    assert _SECRET not in caplog.text
    assert "alice" not in caplog.text
    # the cleanup failure is still reported, just without the secret.
    assert "refused the removal" in caplog.text


# ---------------------------------------------------------------------------
# gain#1017 — the fasta index copy reads inside the handle ``open_raw_file``
# returns. That open is redacted; the read on the returned handle is not, so
# a failure mid-read surfaces the credential-bearing fetch url verbatim.
# ---------------------------------------------------------------------------

#: The bgzipped genome ``open_fasta_file`` is asked for. Its ``.fai``/``.gzi``
#: indexes are what get copied local, and so what leaks on a failing read.
_FASTA_FILE_NAME = "genome.fa.gz"


def _a_remote_fasta_whose_index_read_fails(
    proto_id: str, url: str, mocker: pytest_mock.MockerFixture,
) -> tuple[FsspecRepositoryProtocol, GenomicResource, BaseException]:
    """Arrange an https protocol whose every index read fails.

    Returns the protocol, the resource, and the very error the read raises,
    so a test can assert on identity and not merely on the message. The
    failure is planted on the READ and not on the open, because the open is
    already redacted and would prove nothing.

    The existence probe is forced to succeed so the ``.gzi`` precondition
    passes and the index copy -- not the guard above it -- is what surfaces.
    ``pysam`` is never reached: the copy raises first.
    """
    proto = build_fsspec_protocol(proto_id, url)
    resource = GenomicResource("sub/res", (1, 0), proto, {})
    index_url = proto.get_resource_file_url(
        resource, f"{_FASTA_FILE_NAME}.fai")
    planted = FileNotFoundError(index_url)
    mocker.patch.object(proto.filesystem, "exists", return_value=True)
    handle = mocker.MagicMock()
    handle.__enter__.return_value.read.side_effect = planted
    mocker.patch.object(proto.filesystem, "open", return_value=handle)
    return proto, resource, planted


def test_fasta_index_read_failure_does_not_leak_url_credential(
    mocker: pytest_mock.MockerFixture,
) -> None:
    proto, resource, planted = _a_remote_fasta_whose_index_read_fails(
        "i1017-fasta", f"https://alice:{_SECRET}@127.0.0.1:1/path", mocker)

    with pytest.raises(OSError) as excinfo:
        proto.open_fasta_file(resource, _FASTA_FILE_NAME)

    exc = excinfo.value
    tb = "".join(traceback.format_exception(exc))
    assert _SECRET not in str(exc)
    assert "alice" not in str(exc)
    assert _SECRET not in tb
    for linked in _walk_exception_chain(exc):
        assert _SECRET not in str(linked)
    # host, port and filename preserved so the error stays diagnosable.
    assert "127.0.0.1:1" in str(exc)
    assert f"{_FASTA_FILE_NAME}.fai" in str(exc)
    # a redacted rebuild, not the error the read raised.
    assert exc is not planted


def test_fasta_index_read_failure_without_userinfo_is_unchanged(
    mocker: pytest_mock.MockerFixture,
) -> None:
    # Nothing to redact must mean nothing touched: the read's own error
    # propagates as-is, keeping its traceback and the full url a diagnosis
    # needs. Asserted on identity, because a rebuild carrying an identical
    # message would satisfy any assertion on the message alone.
    proto, resource, planted = _a_remote_fasta_whose_index_read_fails(
        "i1017-fasta-plain", "https://127.0.0.1:1/path", mocker)

    with pytest.raises(OSError) as excinfo:
        proto.open_fasta_file(resource, _FASTA_FILE_NAME)

    assert excinfo.value is planted


def test_fasta_index_copy_lands_the_bytes_it_read(
    tmp_path: pathlib.Path, mocker: pytest_mock.MockerFixture,
) -> None:
    # The success path is what the redaction must not disturb: the copy
    # still writes the file it read, byte for byte, at the path it returns.
    proto = build_fsspec_protocol(
        "i1017-fasta-ok", f"https://alice:{_SECRET}@127.0.0.1:1/path")
    resource = GenomicResource("sub/res", (1, 0), proto, {})
    payload = b"\x1f\x8b\x08index-bytes"
    handle = mocker.MagicMock()
    handle.__enter__.return_value.read.return_value = payload
    opened = mocker.patch.object(
        proto.filesystem, "open", return_value=handle)

    dest = proto._copy_resource_file_to_local(
        resource, f"{_FASTA_FILE_NAME}.fai", str(tmp_path))

    assert pathlib.Path(dest).read_bytes() == payload
    assert pathlib.Path(dest).name == f"{_FASTA_FILE_NAME}.fai"
    # The credential must still travel on the fetch url so aiohttp can
    # authenticate -- only the surfaced error is redacted. Asserted on the
    # call args, because resolving to the credential-free display url
    # instead would break every authed read with this suite still green.
    assert _SECRET in opened.call_args.args[0]
    assert opened.call_args.args[0].endswith(f"{_FASTA_FILE_NAME}.fai")
    # read as stored: the old `uncompress=False` named nothing, and
    # `_read_fetch_file` must not be handed a compression either.
    assert opened.call_args.kwargs["compression"] is None


# ---------------------------------------------------------------------------
# gain#1058 — ``get_file_content`` is the same open-then-read-on-the-returned-
# handle shape, and it sits IN FRONT of the path gain#1017 fixed:
# ``ReferenceGenome.open`` reads the ``.fai`` through ``get_file_content``
# before the backend open ever copies it. It also backs ``load_manifest`` and
# ``load_yaml``, putting the shape on essentially every authed remote read.
# ---------------------------------------------------------------------------

def _a_remote_resource_whose_read_fails(
    proto_id: str, url: str, filename: str,
    mocker: pytest_mock.MockerFixture,
    config: dict | None = None,
) -> tuple[FsspecRepositoryProtocol, GenomicResource, BaseException]:
    """Arrange an https protocol whose every file read fails.

    Returns the protocol, the resource, and the very error the read raises,
    so a test can assert on identity and not merely on the message. The
    failure is planted on the READ and not on the open, because the open is
    already redacted and would prove nothing.

    ``config`` is what a caller reading the file through a typed façade needs
    -- ``ReferenceGenome`` refuses a resource that is not ``type: genome``.
    """
    proto = build_fsspec_protocol(proto_id, url)
    resource = GenomicResource("sub/res", (1, 0), proto, config or {})
    planted = FileNotFoundError(
        proto.get_resource_file_url(resource, filename))
    handle = mocker.MagicMock()
    handle.__enter__.return_value.read.side_effect = planted
    mocker.patch.object(proto.filesystem, "open", return_value=handle)
    return proto, resource, planted


def test_get_file_content_read_failure_does_not_leak_url_credential(
    mocker: pytest_mock.MockerFixture,
) -> None:
    proto, resource, planted = _a_remote_resource_whose_read_fails(
        "i1058-read", f"https://alice:{_SECRET}@127.0.0.1:1/path",
        "data.txt", mocker)

    with pytest.raises(OSError) as excinfo:
        proto.get_file_content(resource, "data.txt")

    exc = excinfo.value
    tb = "".join(traceback.format_exception(exc))
    assert _SECRET not in str(exc)
    assert "alice" not in str(exc)
    assert _SECRET not in tb
    for linked in _walk_exception_chain(exc):
        assert _SECRET not in str(linked)
    # host, port and filename preserved so the error stays diagnosable.
    assert "127.0.0.1:1" in str(exc)
    assert "data.txt" in str(exc)
    # a redacted rebuild, not the error the read raised.
    assert exc is not planted


def test_reference_genome_open_index_read_does_not_leak_url_credential(
    mocker: pytest_mock.MockerFixture,
) -> None:
    # The route from the issue: ``ReferenceGenome.open`` reads the ``.fai``
    # through ``get_file_content`` BEFORE the backend open, so a transient
    # index-read failure surfaces here and never reaches the redacted copy
    # gain#1017 added. End-to-end through the public genome interface.
    _proto, resource, _planted = _a_remote_resource_whose_read_fails(
        "i1058-genome", f"https://alice:{_SECRET}@127.0.0.1:1/path",
        f"{_FASTA_FILE_NAME}.fai", mocker,
        config={"type": "genome", "filename": _FASTA_FILE_NAME})

    with pytest.raises(OSError) as excinfo:
        build_reference_genome_from_resource(resource).open()

    exc = excinfo.value
    tb = "".join(traceback.format_exception(exc))
    assert _SECRET not in str(exc)
    assert "alice" not in str(exc)
    assert _SECRET not in tb
    for linked in _walk_exception_chain(exc):
        assert _SECRET not in str(linked)
    assert "127.0.0.1:1" in str(exc)
    assert f"{_FASTA_FILE_NAME}.fai" in str(exc)


def test_loaded_manifest_is_none_when_the_redacted_manifest_read_fails(
    mocker: pytest_mock.MockerFixture,
) -> None:
    # ``get_loaded_manifest`` reads a ``FileNotFoundError`` out of
    # ``load_manifest`` as "this resource has no manifest" -- the pure-read
    # path that must not build one. The redaction rebuilds the error via
    # ``type(exc)(message)``, so that control flow only survives while the
    # rebuild keeps the type: an ``OSError`` here would propagate instead of
    # answering ``None``. Planted with userinfo in the message so the rebuild
    # path, not the propagate-unchanged path, is what runs.
    #
    # (``get_manifest`` -- the building sibling -- catches nothing and
    # propagates, both before and after this change.)
    _proto, resource, planted = _a_remote_resource_whose_read_fails(
        "i1058-manifest", f"https://alice:{_SECRET}@127.0.0.1:1/path",
        ".MANIFEST", mocker)

    assert resource.get_loaded_manifest() is None
    # the arrangement really did exercise the rebuild: the planted error
    # carried a credential, so it cannot have propagated in place.
    assert _SECRET in str(planted)


def test_get_file_content_read_failure_without_userinfo_is_unchanged(
    mocker: pytest_mock.MockerFixture,
) -> None:
    # Nothing to redact must mean nothing touched: the read's own error
    # propagates as-is, keeping its traceback and the full url a diagnosis
    # needs. Asserted on identity, because a rebuild carrying an identical
    # message would satisfy any assertion on the message alone.
    proto, resource, planted = _a_remote_resource_whose_read_fails(
        "i1058-plain", "https://127.0.0.1:1/path", "data.txt", mocker)

    with pytest.raises(OSError) as excinfo:
        proto.get_file_content(resource, "data.txt")

    assert excinfo.value is planted


@pytest.mark.parametrize("mode", ["t", "b"])
def test_get_file_content_forwards_the_mode_and_the_credential(
    mode: str, mocker: pytest_mock.MockerFixture,
) -> None:
    # What only a mock can show: the url fsspec is handed. The ``str``/
    # ``bytes`` split and the as-stored read are demonstrated for real in
    # ``..._reads_a_real_gzip_file_as_stored`` -- a mocked handle answers the
    # same payload whatever mode it was opened in, so it could only argue
    # them.
    proto = build_fsspec_protocol(
        f"i1058-ok-{mode}", f"https://alice:{_SECRET}@127.0.0.1:1/path")
    resource = GenomicResource("sub/res", (1, 0), proto, {})
    handle = mocker.MagicMock()
    handle.__enter__.return_value.read.return_value = "content"
    opened = mocker.patch.object(
        proto.filesystem, "open", return_value=handle)

    proto.get_file_content(resource, "data.txt", mode=mode)

    # the base's ``mode="t"``/``"b"`` still becomes fsspec's ``"rt"``/``"rb"``,
    # which is what decides ``str`` against ``bytes``.
    assert opened.call_args.args[1] == f"r{mode}"
    # The credential must still travel on the fetch url so aiohttp can
    # authenticate -- only the surfaced error is redacted. Asserted on the
    # call args, because resolving to the credential-free display url
    # instead would break every authed read with this suite still green.
    assert _SECRET in opened.call_args.args[0]
    assert opened.call_args.args[0].endswith("data.txt")
    # read as stored: ``uncompress`` named nothing here either -- only
    # ``open_raw_file``'s ``compression`` kwarg did, and no caller supplies
    # it -- so ``_read_fetch_file`` must not be handed a compression.
    assert opened.call_args.kwargs["compression"] is None


def test_get_file_content_reads_a_real_gzip_file_as_stored(
    tmp_path: pathlib.Path,
) -> None:
    # Against a real filesystem and a real gzip file, not a mock: a mocked
    # handle answers the same payload whatever mode it is opened in, so it
    # can argue the ``str``/``bytes`` split but not demonstrate it, and it
    # cannot demonstrate "as stored" at all. Here the gzip magic surviving
    # into the answer IS the proof that nothing decompressed it.
    root = tmp_path / "grr"
    stored = gzip.compress(b"payload")
    setup_directories(root, {
        "sub": {"res(1.0)": {
            "plain.txt": "plain-text",
            "data.txt.gz": stored,
        }},
    })

    proto = build_fsspec_protocol("i1058-real", f"file://{root}")
    resource = GenomicResource("sub/res", (1, 0), proto, {})

    text = proto.get_file_content(resource, "plain.txt")
    assert text == "plain-text"
    assert isinstance(text, str)

    # ``uncompress`` is the base contract's default and decompresses nothing
    # here, in either position: both callers get the bytes as stored, gzip
    # magic and all. Preserved, not repaired.
    for uncompress in (True, False):
        raw = proto.get_file_content(
            resource, "data.txt.gz", mode="b", uncompress=uncompress)
        assert isinstance(raw, bytes)
        assert raw == stored
        assert raw[:2] == b"\x1f\x8b"


# ---------------------------------------------------------------------------
# gain#1078 — ``open_raw_file`` redacted the OPEN only. Every I/O on the handle
# it handed back -- ``read``, a bounded ``read(n)``, ``readline``, iteration,
# ``seek`` -- ran unwrapped, so a failure mid-stream surfaced the
# credential-bearing fetch url verbatim. #1017 and #1058 each closed one
# caller by routing it through ``_read_fetch_file``; that helper reads the
# whole file, so it can never serve the callers that hold the handle, iterate
# it lazily, or read it in chunks on purpose. The handle itself is redacted
# here instead, which closes all of them at the one choke point.
# ---------------------------------------------------------------------------


def _a_remote_handle_whose_io_fails(
    proto_id: str, url: str, filename: str,
    mocker: pytest_mock.MockerFixture,
    *, failing: str,
) -> tuple[FsspecRepositoryProtocol, GenomicResource, BaseException]:
    """Arrange an https protocol whose ``failing`` op on the handle raises.

    Returns the protocol, the resource and the very error planted, so a test
    can assert on identity and not merely on the message.

    The failure is planted on the handle and not on the ``open``, because the
    open has been redacted since #620 and would prove nothing.

    ``__enter__`` is made to answer the handle itself, which is what a real
    fsspec file does. A bare ``MagicMock`` answers a *different* child mock,
    so a proxy that redacted only what it wrapped would still see an
    unredacted object handed to the ``with`` body and the test would pass for
    the wrong reason.
    """
    proto = build_fsspec_protocol(proto_id, url)
    resource = GenomicResource("sub/res", (1, 0), proto, {})
    planted = FileNotFoundError(
        proto.get_resource_file_url(resource, filename))
    handle = mocker.MagicMock()
    handle.__enter__.return_value = handle
    # ``iter(handle)`` must answer the handle, so that a ``next()`` on it
    # reaches the planted ``__next__``. A bare ``MagicMock`` iterates empty,
    # which would end the loop with ``StopIteration`` and let an unredacted
    # build pass this test for the wrong reason.
    handle.__iter__.return_value = handle
    getattr(handle, failing).side_effect = planted
    mocker.patch.object(proto.filesystem, "open", return_value=handle)
    return proto, resource, planted


def _assert_no_credential_escaped(exc: BaseException) -> None:
    """The secret must survive nowhere the raised error can be rendered.

    Callers that know the exact expected text pin the message whole on top of
    this; this covers the surfaces a message pin cannot see -- the rendered
    traceback, and every exception linked through
    ``__cause__``/``__context__`` (the walk includes ``exc`` itself, so the
    message is covered here too).
    """
    assert _SECRET not in "".join(traceback.format_exception(exc))
    for linked in _walk_exception_chain(exc):
        assert _SECRET not in str(linked)


def _assert_redacted(
    exc: BaseException, planted: BaseException, filename: str,
) -> None:
    """The whole redaction contract, asserted in one place."""
    _assert_no_credential_escaped(exc)
    assert "alice" not in str(exc)
    # host, port and filename preserved so the error stays diagnosable.
    assert "127.0.0.1:1" in str(exc)
    assert filename in str(exc)
    # a redacted rebuild, not the error the read raised.
    assert exc is not planted


@dataclasses.dataclass(frozen=True)
class _HandleRead:
    """One way a caller consumes a raw-file handle, and where it fails.

    ``consume`` is the call a caller makes; ``failing_attr`` is the handle
    attribute that call actually reaches, which is not always the same name
    -- a bounded ``read(4)`` still fails on ``read``, and iteration fails on
    ``__next__``.
    """

    consume: typing.Callable[[typing.Any], typing.Any]
    failing_attr: str


# Every way a caller consumes one of these handles somewhere in the tree:
# the whole-file slurp, the tabix index header's bounded ``read(n)``, the
# header and chrom-mapping ``readline()`` loops, the tabular readers'
# ``for line in handle``, the reference genome's byte-offset ``seek``, and
# ``readall``, which is what a consumer that buffers the handle
# (``io.BufferedReader``) resolves a full read to.
_HANDLE_READS: dict[str, _HandleRead] = {
    "read": _HandleRead(lambda handle: handle.read(), "read"),
    "read-bounded": _HandleRead(lambda handle: handle.read(4), "read"),
    "readall": _HandleRead(lambda handle: handle.readall(), "readall"),
    "readline": _HandleRead(lambda handle: handle.readline(), "readline"),
    "readlines": _HandleRead(
        lambda handle: handle.readlines(), "readlines"),
    "iterate": _HandleRead(lambda handle: next(iter(handle)), "__next__"),
    "seek": _HandleRead(lambda handle: handle.seek(0), "seek"),
    "tell": _HandleRead(lambda handle: handle.tell(), "tell"),
}


@pytest.mark.parametrize("consume", list(_HANDLE_READS))
def test_raw_file_io_failure_does_not_leak_url_credential(
    consume: str, mocker: pytest_mock.MockerFixture,
) -> None:
    handle_read = _HANDLE_READS[consume]
    proto, resource, planted = _a_remote_handle_whose_io_fails(
        f"i1078-io-{consume}", f"https://alice:{_SECRET}@127.0.0.1:1/path",
        "data.txt", mocker, failing=handle_read.failing_attr)

    with pytest.raises(OSError) as excinfo, \
            proto.open_raw_file(resource, "data.txt", "rt") as infile:
        handle_read.consume(infile)

    _assert_redacted(excinfo.value, planted, "data.txt")


@pytest.mark.parametrize("read_args", [(), (4,)])
def test_buffered_read_failure_does_not_leak_url_credential(
    read_args: tuple[int, ...], mocker: pytest_mock.MockerFixture,
) -> None:
    """A consumer that BUFFERS the handle must not get around the wrapper.

    ``io.BufferedReader(handle).read()`` resolves to ``readall`` and
    ``read(n)`` to ``readinto``. Delegating either -- rather than wrapping
    it -- runs it on the inner handle, which then drives the inner
    ``readinto`` itself, so neither the wrapper's ``read`` nor a wrapped
    ``readinto`` is ever consulted and the failure arrives unredacted.
    Exercised with a real ``BufferedReader`` over a real raw handle, because
    that bypass is a property of the io stack and a mock would not reproduce
    it.
    """
    proto = build_fsspec_protocol(
        f"i1078-buffered-{len(read_args)}",
        f"https://alice:{_SECRET}@127.0.0.1:1/path")
    resource = GenomicResource("sub/res", (1, 0), proto, {})
    planted = FileNotFoundError(
        proto.get_resource_file_url(resource, "data.txt"))

    class _FailingRaw(io.RawIOBase):
        def readinto(self, _buffer: typing.Any) -> int:
            raise planted

        def readable(self) -> bool:
            return True

    mocker.patch.object(
        proto.filesystem, "open", return_value=_FailingRaw())

    with pytest.raises(OSError) as excinfo:
        io.BufferedReader(
            proto.open_raw_file(resource, "data.txt", "rb"),
        ).read(*read_args)

    _assert_redacted(excinfo.value, planted, "data.txt")


def test_raw_file_release_failure_does_not_leak_url_credential(
    mocker: pytest_mock.MockerFixture,
) -> None:
    """Leaving the ``with`` block is an I/O point of its own.

    A store finishes a write when the handle is released, so the failure can
    arrive there and nowhere else. Every write site in this tree spells that
    ``with ... as outfile:``, which resolves to ``__exit__`` and never to a
    bare ``close()`` -- so wrapping ``close`` alone would leave the live path
    open.
    """
    proto, resource, planted = _a_remote_handle_whose_io_fails(
        "i1078-exit", f"https://alice:{_SECRET}@127.0.0.1:1/path",
        "data.txt", mocker, failing="__exit__")

    # The contexts unwind in reverse, so the handle is released -- and the
    # planted failure raised -- inside ``pytest.raises``.
    with pytest.raises(OSError) as excinfo, \
            proto.open_raw_file(resource, "data.txt", "rt"):
        pass

    _assert_redacted(excinfo.value, planted, "data.txt")


def test_raw_file_read_failure_without_userinfo_propagates_unchanged(
    mocker: pytest_mock.MockerFixture,
) -> None:
    proto, resource, planted = _a_remote_handle_whose_io_fails(
        "i1078-plain", "https://127.0.0.1:1/path", "data.txt",
        mocker, failing="read")

    with pytest.raises(OSError) as excinfo, \
            proto.open_raw_file(resource, "data.txt", "rt") as infile:
        infile.read()

    # Nothing to redact, so the ORIGINAL object propagates -- identity, and
    # with it the exception type and the traceback a rebuild would cost.
    # This is the common unauthenticated case: it must stay free.
    assert excinfo.value is planted


def _a_real_resource(
    tmp_path: pathlib.Path, proto_id: str,
) -> tuple[FsspecRepositoryProtocol, GenomicResource]:
    """A resource on a real filesystem, holding one file of each shape."""
    root = tmp_path / "grr"
    setup_directories(root, {
        "sub": {"res(1.0)": {
            "lines.txt": "alpha\nbeta\ngamma\n",
            "data.txt.gz": gzip.compress(b"payload"),
        }},
    })
    proto = build_fsspec_protocol(proto_id, f"file://{root}")
    return proto, GenomicResource("sub/res", (1, 0), proto, {})


def test_raw_file_handle_still_reads_a_real_file(
    tmp_path: pathlib.Path,
) -> None:
    # Against a real filesystem, because a mocked handle answers whatever it
    # is told to and so cannot show that the wrapper preserves the payload,
    # the text/bytes split, or seek arithmetic.
    proto, resource = _a_real_resource(tmp_path, "i1078-real-read")

    with proto.open_raw_file(resource, "lines.txt", "rt") as infile:
        assert infile.read() == "alpha\nbeta\ngamma\n"

    with proto.open_raw_file(resource, "lines.txt", "rb") as infile:
        assert infile.read(5) == b"alpha"
        assert infile.tell() == 5
        infile.seek(0)
        assert infile.read(5) == b"alpha"

    with proto.open_raw_file(resource, "lines.txt", "rt") as infile:
        assert infile.readline() == "alpha\n"


def test_raw_file_handle_still_iterates_a_real_file(
    tmp_path: pathlib.Path,
) -> None:
    # ``for line in handle`` is how the tabular readers, the chrom-mapping
    # loader and the gene-set readers consume a file. It resolves
    # ``__iter__`` on the TYPE, so a proxy that delegates only through
    # ``__getattr__`` breaks every one of them.
    proto, resource = _a_real_resource(tmp_path, "i1078-real-iter")

    with proto.open_raw_file(resource, "lines.txt", "rt") as infile:
        assert list(infile) == ["alpha\n", "beta\n", "gamma\n"]


def test_raw_file_handle_still_decompresses_and_closes(
    tmp_path: pathlib.Path,
) -> None:
    proto, resource = _a_real_resource(tmp_path, "i1078-real-gzip")

    with proto.open_raw_file(
            resource, "data.txt.gz", "rb", compression=True) as infile:
        assert infile.read() == b"payload"

    handle = proto.open_raw_file(resource, "lines.txt", "rt")
    with handle:
        pass
    # the ``with`` released the underlying handle, not merely the wrapper
    assert handle.closed


def test_raw_file_handle_delegates_unknown_attributes(
    tmp_path: pathlib.Path,
) -> None:
    # The handle escapes to ``gzip.open``, pandas, ``json.load`` and the
    # gene-set readers, which reach for attributes the wrapper does not name.
    proto, resource = _a_real_resource(tmp_path, "i1078-real-attrs")

    with proto.open_raw_file(resource, "lines.txt", "rb") as infile:
        assert infile.readable() is True
        assert infile.seekable() is True
        assert infile.closed is False
        assert infile.mode is not None


def test_raw_file_handle_advertises_exactly_what_it_wraps(
    tmp_path: pathlib.Path,
) -> None:
    """The wrapper must not change what the handle appears able to do.

    Consumers feature-detect. pandas probes for ``read1`` when it picks a
    read path; ``io`` picks among ``readall``/``readinto``/``read1`` the same
    way. Declaring one of those as a method makes ``hasattr`` answer True for
    a handle that does not have it, the consumer takes that path, and the
    forwarding call dies with ``AttributeError`` on the inner handle -- which
    is how this reached CI as ``'S3File' object has no attribute 'read1'``
    after the local and in-memory backends, which DO have it, stayed green.

    So the property is equality with the wrapped object, not presence: every
    optional operation is mirrored, never declared. ``readall`` is the one
    that discriminates here (no fsspec backend has it); the s3 arms of the
    data_frame suite cover ``read1``.
    """
    proto, resource = _a_real_resource(tmp_path, "i1078-capabilities")

    with proto.open_raw_file(resource, "lines.txt", "rb") as infile:
        inner = infile._inner
        for op in (
            "readall", "readinto", "readinto1", "read1",
            "truncate", "writelines", "read", "seek",
        ):
            assert hasattr(infile, op) == hasattr(inner, op), op
        # the guard is only meaningful while something is actually absent
        assert not hasattr(inner, "readall")


def test_tabix_index_check_decline_does_not_leak_url_credential(
    mocker: pytest_mock.MockerFixture, caplog: pytest.LogCaptureFixture,
) -> None:
    """The one site that puts the fetch url in a LOG rather than an error.

    Every other caller propagates the read failure. ``_validate_index_columns``
    catches it -- deliberately, so a transient fault does not refuse a
    resource htslib has just read (gain#628) -- and reports the decline with
    ``str(error)`` in the message, which is a warning written to wherever the
    logs are shipped and kept.

    Driven through ``_validate_index_columns`` rather than ``open()``: the
    latter opens the file with pysam first, and pysam cannot be pointed at an
    authed remote url from a unit test. This is the seam the credential
    actually crosses.
    """
    _, resource, _ = _a_remote_handle_whose_io_fails(
        "i1078-tabix", f"https://alice:{_SECRET}@127.0.0.1:1/path",
        "data.txt.gz.tbi", mocker, failing="read")
    table = TabixGenomicPositionTable(resource, {
        "filename": "data.txt.gz",
        "index_filename": "data.txt.gz.tbi",
    })

    with caplog.at_level(logging.WARNING):
        table._validate_index_columns()

    # the check declined -- it neither passed nor refused the resource
    assert "NOT validated" in caplog.text
    assert _SECRET not in caplog.text
    assert "alice" not in caplog.text
    # host and port survive, so the decline stays diagnosable
    assert "127.0.0.1:1" in caplog.text


def test_raw_file_handle_reads_incrementally(
    tmp_path: pathlib.Path,
) -> None:
    """A bounded read takes its bytes and leaves the rest on the stream.

    The wrapper must not turn a chunked or lazy read into a slurp. Three
    callers depend on that and none of them could be served by
    ``_read_fetch_file``: ``compute_md5_sum`` and the download copy loop
    stream multi-GB files a chunk at a time, and the in-memory position
    table holds its handle and iterates it lazily.
    """
    root = tmp_path / "grr"
    setup_directories(root, {
        "sub": {"res(1.0)": {"lines.txt": "alpha\nbeta\ngamma\n"}},
    })
    proto = build_fsspec_protocol("i1078-incremental", f"file://{root}")
    resource = GenomicResource("sub/res", (1, 0), proto, {})

    with proto.open_raw_file(resource, "lines.txt", "rb") as infile:
        assert infile.read(6) == b"alpha\n"
        # the remainder is still there: the first read consumed 6 bytes, not
        # the file
        assert infile.read(5) == b"beta\n"
        assert infile.read() == b"gamma\n"


def test_md5_sum_reads_a_multi_chunk_file_in_bounded_reads(
    tmp_path: pathlib.Path, mocker: pytest_mock.MockerFixture,
) -> None:
    """The md5 loop must stay bounded, not become a slurp.

    Asserting the digest alone would not show this: a wrapper that read the
    whole file into memory and served it back in slices produces the very
    same md5. What distinguishes them is the SHAPE of the reads reaching the
    store, so that is what is recorded -- every read bounded, and more than
    one of them. ``compute_md5_sum`` streams multi-GB resource files, so
    turning its loop into one unbounded read is a memory regression that no
    correctness assertion can catch.
    """
    # Spanning three chunks is the whole requirement, and the content is
    # irrelevant -- the assertions read the SHAPE of the reads, not the
    # bytes. A repeated byte builds ~50x faster than joining 400k lines.
    payload = b"x" * (2 * FsspecReadWriteProtocol.CHUNK_SIZE + 1)
    assert len(payload) > 2 * FsspecReadWriteProtocol.CHUNK_SIZE

    root = tmp_path / "grr"
    setup_directories(root, {"sub": {"res(1.0)": {"big.bin": payload}}})
    proto = build_fsspec_protocol("i1078-md5", f"file://{root}")
    resource = GenomicResource("sub/res", (1, 0), proto, {})

    sizes: list[int | None] = []
    real_open = proto.filesystem.open

    def recording_open(*args: typing.Any, **kwargs: typing.Any) -> typing.Any:
        handle = real_open(*args, **kwargs)
        real_read = handle.read

        def read(*read_args: typing.Any) -> typing.Any:
            sizes.append(read_args[0] if read_args else None)
            return real_read(*read_args)

        handle.read = read  # type: ignore[method-assign]
        return handle

    mocker.patch.object(proto.filesystem, "open", side_effect=recording_open)

    assert proto.compute_md5_sum(resource, "big.bin") == \
        hashlib.md5(payload).hexdigest()  # ruff: ignore[hashlib-insecure-hash-function]

    # more than one read, and not one of them unbounded
    assert len(sizes) > 1
    assert all(size is not None for size in sizes)


# ---------------------------------------------------------------------------
# gain#1314 — the four opens that hand a url to pysam/pyBigWig. ADR 0023's
# ``_RedactingFile`` cannot reach them: the library owns the transport, so
# there is no handle to wrap, and GAIn composes none of the messages. pysam
# embeds the credential-bearing url in the error it raises, and htslib writes
# it to stderr on top of that.
# ---------------------------------------------------------------------------

_TABIX_FILE_NAME = "data.txt.gz"
_VCF_FILE_NAME = "data.vcf.gz"
_BIGWIG_FILE_NAME = "data.bw"

#: The bearer half of a presigned url. Distinct from ``_SECRET`` because it
#: leaks through a different door: an s3 GRR has no userinfo at all, and
#: carries its credential in the query string instead.
_SIGNATURE = "Ns1gNaTuReDoNoTlOg%3D"


def _libbigwig_refused_open_line(url: str) -> str:
    """The ``[urlOpen]`` line libBigWig writes when it cannot open ``url``.

    Its wording is a property of the pyBigWig BUILD, not of gain. The PyPI
    wheel is compiled ``NOCURL`` (``pyBigWig.remote == 0``): it takes every
    url for a local path, and the failed ``fopen`` is reported naming the
    url. A build with libcurl (``remote == 1``: conda-forge, and the sdist
    that ``python:3.14-slim`` compiles because 0.3.25 ships no cp314 wheel)
    takes the curl branch and reports ``curl_easy_strerror`` instead, naming
    no url. Only the prefix is pinned on that branch: the strerror text is
    libcurl's and has been reworded across its releases (gain#1392).
    """
    if pyBigWig.remote:  # pylint: disable=I1101
        return "[urlOpen] curl_easy_perform received an error: "
    return f"[urlOpen] Couldn't open {url} for reading"


def _a_refused_protocol(
    proto_id: str, *, authed: bool = True,
) -> tuple[FsspecRepositoryProtocol, GenomicResource]:
    """A protocol whose host refuses every connection.

    Port 1 is never listening, so the open fails offline and instantly --
    the arrangement the rest of this module uses. It has to be a real url and
    a real failure: pysam and pyBigWig bypass fsspec entirely, so planting a
    fault on the filesystem the way the handle-level tests do would never be
    reached.
    """
    userinfo = f"alice:{_SECRET}@" if authed else ""
    proto = build_fsspec_protocol(
        proto_id, f"https://{userinfo}127.0.0.1:1/path")
    return proto, GenomicResource("sub/res", (1, 0), proto, {})


def test_tabix_open_failure_does_not_leak_url_credential(
    capfd: pytest.CaptureFixture[str],
) -> None:
    proto, resource = _a_refused_protocol("i1314-tabix")

    with pytest.raises(OSError) as excinfo:
        proto.open_tabix_file(
            resource, _TABIX_FILE_NAME, f"{_TABIX_FILE_NAME}.tbi")

    exc = excinfo.value
    # Pinned WHOLE. A fragment probe (``"127.0.0.1" in str(exc)``) passes just
    # as well for an over-redaction that drops the scheme or the host along
    # with the userinfo, leaving a message that can no longer say which GRR
    # failed -- which is the point of redacting rather than suppressing.
    assert str(exc) == (
        "could not open file "
        f"`https://127.0.0.1:1/path/sub/res(1.0)/{_TABIX_FILE_NAME}`")
    _assert_no_credential_escaped(exc)
    # The second channel, independent of the exception: htslib writes
    # ``[E::hts_open_format] Failed to open file "<url>"`` to fd 2 itself, so
    # no redaction of the raised error can reach it, and under CI or
    # supervisord it lands in the same log stream as the ERROR line. Asserted
    # at FD level -- the write comes from C, and ``capsys``, which only
    # replaces ``sys.stderr``, never sees it.
    assert _SECRET not in capfd.readouterr().err


def test_tabix_open_still_hands_pysam_the_credential(
    mocker: pytest_mock.MockerFixture,
) -> None:
    # What only a mock can show: the url pysam is HANDED. Redacting it before
    # the call reads as the tidier fix and breaks every authed read, and no
    # leak test can see that -- every failure this suite plants is on the way
    # out. Both urls carry it: htslib authenticates the index fetch too.
    # Precedent: ``..._forwards_the_mode_and_the_credential``.
    proto, resource = _a_refused_protocol("i1314-tabix-cred")
    opened = mocker.patch.object(pysam, "TabixFile")

    proto.open_tabix_file(
        resource, _TABIX_FILE_NAME, f"{_TABIX_FILE_NAME}.tbi")

    assert _SECRET in opened.call_args.args[0]
    assert _SECRET in opened.call_args.kwargs["index"]


def test_tabix_open_without_userinfo_raises_the_librarys_own_error(
    mocker: pytest_mock.MockerFixture,
) -> None:
    # Nothing to redact must mean nothing touched: the library's own error
    # propagates as-is, keeping its type, its traceback and its chain.
    # Asserted on identity, because a rebuild carrying an identical message
    # would satisfy any assertion on the message alone.
    proto, resource = _a_refused_protocol("i1314-tabix-plain", authed=False)
    planted = OSError(
        "could not open file "
        f"`https://127.0.0.1:1/path/sub/res(1.0)/{_TABIX_FILE_NAME}`")
    mocker.patch.object(pysam, "TabixFile", side_effect=planted)

    with pytest.raises(OSError) as excinfo:
        proto.open_tabix_file(
            resource, _TABIX_FILE_NAME, f"{_TABIX_FILE_NAME}.tbi")

    assert excinfo.value is planted


def test_tabix_open_without_userinfo_keeps_htslib_diagnostics(
    capfd: pytest.CaptureFixture[str],
) -> None:
    # The silencing costs htslib's own account of why the open failed, so it
    # is spent only on the configuration it protects. An unauthed GRR -- every
    # deployment today -- has no credential to lose and keeps the diagnosis.
    proto, resource = _a_refused_protocol("i1314-tabix-loud", authed=False)

    with pytest.raises(OSError):
        proto.open_tabix_file(
            resource, _TABIX_FILE_NAME, f"{_TABIX_FILE_NAME}.tbi")

    assert "[E::hts_open_format]" in capfd.readouterr().err


def test_failed_authed_open_does_not_silence_the_next_one(
    capfd: pytest.CaptureFixture[str],
) -> None:
    # The verbosity level is PROCESS-global, so a bracket that does not
    # restore turns one authed open into permanent silence for every htslib
    # user after it. Asserted on that consequence rather than on the
    # ``set_verbosity`` calls, so it stays true if the mechanism changes.
    authed, authed_res = _a_refused_protocol("i1314-restore-fail")
    plain, plain_res = _a_refused_protocol(
        "i1314-restore-fail-plain", authed=False)

    with pytest.raises(OSError):
        authed.open_tabix_file(
            authed_res, _TABIX_FILE_NAME, f"{_TABIX_FILE_NAME}.tbi")
    capfd.readouterr()

    with pytest.raises(OSError):
        plain.open_tabix_file(
            plain_res, _TABIX_FILE_NAME, f"{_TABIX_FILE_NAME}.tbi")

    assert "[E::hts_open_format]" in capfd.readouterr().err


def test_successful_authed_open_does_not_silence_the_next_one(
    capfd: pytest.CaptureFixture[str],
) -> None:
    # The same guarantee on the path that does not raise -- restoring only
    # where the open failed would leave every successful authed read silencing
    # htslib for good.
    authed, authed_res = _a_refused_protocol("i1314-restore-ok")
    plain, plain_res = _a_refused_protocol(
        "i1314-restore-ok-plain", authed=False)

    with unittest.mock.patch.object(pysam, "TabixFile"):
        authed.open_tabix_file(
            authed_res, _TABIX_FILE_NAME, f"{_TABIX_FILE_NAME}.tbi")
    capfd.readouterr()

    with pytest.raises(OSError):
        plain.open_tabix_file(
            plain_res, _TABIX_FILE_NAME, f"{_TABIX_FILE_NAME}.tbi")

    assert "[E::hts_open_format]" in capfd.readouterr().err


@pytest.mark.parametrize(
    ("index_filename", "index_exists"),
    [(None, False), (f"{_VCF_FILE_NAME}.tbi", True)],
    ids=["unindexed", "indexed"])
def test_vcf_open_failure_does_not_leak_url_credential(
    index_filename: str | None, index_exists: bool,
    capfd: pytest.CaptureFixture[str], mocker: pytest_mock.MockerFixture,
) -> None:
    # This method reaches ``pysam.VariantFile`` at TWO call sites -- the
    # indexed open a configured table takes, and the early return for a file
    # that ships no index at all (a VCF header sidecar, say). Wrapping one and
    # not the other would leave half the method leaking, so both are pinned
    # here. The existence probe decides which branch is taken.
    proto, resource = _a_refused_protocol(f"i1314-vcf-{index_exists}")
    mocker.patch.object(
        proto.filesystem, "exists", return_value=index_exists)

    with pytest.raises(OSError) as excinfo:
        proto.open_vcf_file(resource, _VCF_FILE_NAME, index_filename)

    assert str(excinfo.value) == (
        "[Errno 111] Could not open variant file: Connection refused: "
        f"'https://127.0.0.1:1/path/sub/res(1.0)/{_VCF_FILE_NAME}'")
    _assert_no_credential_escaped(excinfo.value)
    assert _SECRET not in capfd.readouterr().err


def test_vcf_unindexed_open_still_hands_pysam_the_credential(
    mocker: pytest_mock.MockerFixture,
) -> None:
    # The index-less branch needs its own forwarding fence. Redacting the url
    # on this branch alone would break every unindexed authed VCF read while
    # the leak test above went on passing -- it pins the message, and a
    # credential-free url produces a clean message for the wrong reason.
    proto, resource = _a_refused_protocol("i1314-vcf-plain-cred")
    opened = mocker.patch.object(pysam, "VariantFile")

    proto.open_vcf_file(resource, _VCF_FILE_NAME)

    assert _SECRET in opened.call_args.args[0]


def test_vcf_indexed_open_still_hands_pysam_the_credential(
    mocker: pytest_mock.MockerFixture,
) -> None:
    # Both urls carry it: htslib fetches the index over the same authenticated
    # transport as the data file.
    proto, resource = _a_refused_protocol("i1314-vcf-cred")
    mocker.patch.object(proto.filesystem, "exists", return_value=True)
    opened = mocker.patch.object(pysam, "VariantFile")

    proto.open_vcf_file(resource, _VCF_FILE_NAME, f"{_VCF_FILE_NAME}.tbi")

    assert _SECRET in opened.call_args.args[0]
    assert _SECRET in opened.call_args.kwargs["index_filename"]


def test_bigwig_open_failure_does_not_leak_url_credential(
    mocker: pytest_mock.MockerFixture,
) -> None:
    # pyBigWig's own exception carries no url today ("Received an error during
    # file opening!"), so this closes a LATENT leak: the wrapper is what keeps
    # a future libBigWig message -- or a failure from any layer in between --
    # from carrying the credential out. The message is planted for that
    # reason, not to mimic today's text.
    #
    # The channel that did leak was stderr: libBigWig writes the url with its
    # own ``fprintf``, which ``pysam.set_verbosity`` cannot reach. That half
    # was gain#1333, and it is closed below by taking fd 2 away rather than by
    # anything this test can see -- the two are independent, which is why both
    # are pinned.
    proto, resource = _a_refused_protocol("i1314-bw")
    file_url = proto.get_resource_file_url(resource, _BIGWIG_FILE_NAME)
    mocker.patch.object(
        pyBigWig, "open",
        side_effect=RuntimeError(f"Couldn't open {file_url} for reading"))

    with pytest.raises(RuntimeError) as excinfo:
        proto.open_bigwig_file(resource, _BIGWIG_FILE_NAME)

    # The type survives too: ``RuntimeError`` rebuilds from a single message,
    # so nothing is demoted to the ``OSError`` fallback.
    assert str(excinfo.value) == (
        "Couldn't open "
        f"https://127.0.0.1:1/path/sub/res(1.0)/{_BIGWIG_FILE_NAME} "
        "for reading")
    _assert_no_credential_escaped(excinfo.value)


def test_bigwig_open_still_hands_pybigwig_the_credential(
    mocker: pytest_mock.MockerFixture,
) -> None:
    proto, resource = _a_refused_protocol("i1314-bw-cred")
    opened = mocker.patch.object(pyBigWig, "open")

    proto.open_bigwig_file(resource, _BIGWIG_FILE_NAME)

    assert _SECRET in opened.call_args.args[0]


def test_bigwig_open_failure_does_not_leak_url_credential_to_stderr(
    capfd: pytest.CaptureFixture[str],
) -> None:
    # libBigWig writes the url it was handed to fd 2 through its own
    # ``fprintf``. ``pysam.set_verbosity`` is htslib's knob and does not
    # reach another library, and replacing ``sys.stderr`` does not either --
    # the writer holds the descriptor directly. This is the channel gain#1333
    # closes; the exception half was already clean.
    proto, resource = _a_refused_protocol("i1333-bw")

    with pytest.raises(RuntimeError):
        proto.open_bigwig_file(resource, _BIGWIG_FILE_NAME)

    assert _SECRET not in capfd.readouterr().err


def test_bigwig_open_failure_does_not_leak_a_presigned_signature(
    capfd: pytest.CaptureFixture[str], mocker: pytest_mock.MockerFixture,
) -> None:
    # An s3 GRR carries its credential in the QUERY STRING rather than in
    # userinfo -- ``_get_file_url`` presigns for that scheme -- so the gate
    # cannot be the userinfo predicate alone. Nor can it be the scheme:
    # ``sign()`` on an anonymous filesystem returns a bare url with nothing to
    # protect. Nor a list of ``X-Amz-*`` parameter names: s3fs defaults to
    # SigV2, spelled ``AWSAccessKeyId``/``Signature``/``Expires``, so a name
    # list would miss the shape GAIn produces by default. The presence of a
    # query string is what is accurate for both signature versions.
    proto, resource = _a_refused_protocol("i1333-bw-signed", authed=False)
    mocker.patch.object(
        proto, "_get_file_url",
        return_value=(
            f"https://127.0.0.1:1/path/sub/res(1.0)/{_BIGWIG_FILE_NAME}"
            f"?AWSAccessKeyId=AKIAEXAMPLE&Signature={_SIGNATURE}"
            "&Expires=1789000000"))

    with pytest.raises(RuntimeError):
        proto.open_bigwig_file(resource, _BIGWIG_FILE_NAME)

    assert _SIGNATURE not in capfd.readouterr().err


def test_bigwig_open_without_a_credential_keeps_libbigwig_diagnostics(
    capfd: pytest.CaptureFixture[str],
) -> None:
    # Taking fd 2 away costs libBigWig's account of why the open failed, so
    # it is spent only on the urls that need protecting. An unauthed GRR --
    # every deployment today -- has no credential to lose and keeps the
    # diagnosis. This is also what keeps the assertions above honest: without
    # it they would pass just as well for a suppression that never lifts.
    proto, resource = _a_refused_protocol("i1333-bw-plain", authed=False)

    with pytest.raises(RuntimeError):
        proto.open_bigwig_file(resource, _BIGWIG_FILE_NAME)

    assert _libbigwig_refused_open_line(
        f"https://127.0.0.1:1/path/sub/res(1.0)/{_BIGWIG_FILE_NAME}",
    ) in capfd.readouterr().err


def test_bigwig_open_on_an_anonymous_s3_url_keeps_libbigwig_diagnostics(
    capfd: pytest.CaptureFixture[str], mocker: pytest_mock.MockerFixture,
) -> None:
    # ``sign()`` on an anonymous s3 filesystem returns a BARE url -- no query
    # string, nothing to protect. Pinned because the tempting simplification
    # of the gate, "suppress whenever the scheme is s3", silences exactly
    # this case for no benefit.
    proto, resource = _a_refused_protocol("i1333-bw-anon-s3", authed=False)
    bare_url = f"https://bucket.example.invalid/sub/res(1.0)/{_BIGWIG_FILE_NAME}"
    mocker.patch.object(proto, "_get_file_url", return_value=bare_url)

    with pytest.raises(RuntimeError):
        proto.open_bigwig_file(resource, _BIGWIG_FILE_NAME)

    assert _libbigwig_refused_open_line(bare_url) in capfd.readouterr().err


def test_bigwig_open_on_a_file_scheme_path_keeps_libbigwig_diagnostics(
    tmp_path: pathlib.Path, capfd: pytest.CaptureFixture[str],
) -> None:
    # A local GRR takes a DIFFERENT ``_get_file_url`` branch from the two
    # controls above -- the url is reduced to a bare filesystem path -- and
    # one that can carry no credential at all, whatever the base looks like.
    proto = build_filesystem_test_protocol(tmp_path)
    resource = GenomicResource("sub/res", (1, 0), proto, {})

    with pytest.raises(RuntimeError):
        proto.open_bigwig_file(resource, _BIGWIG_FILE_NAME)

    captured = capfd.readouterr().err
    assert "[urlOpen] Couldn't open " in captured
    assert _BIGWIG_FILE_NAME in captured


def test_bigwig_open_survives_a_closed_stderr(
    mocker: pytest_mock.MockerFixture,
) -> None:
    # With fd 2 closed there is nothing to suppress and nothing to put back,
    # so the open runs unsuppressed. A read that worked before this guard
    # existed must not start raising because of it -- the guard exists to
    # withhold a diagnostic, never to refuse the open.
    proto, resource = _a_refused_protocol("i1333-bw-no-stderr")
    mocker.patch.object(
        os, "dup", side_effect=OSError(errno.EBADF, "Bad file descriptor"))

    with pytest.raises(RuntimeError) as excinfo:
        proto.open_bigwig_file(resource, _BIGWIG_FILE_NAME)

    assert str(excinfo.value) == "Received an error during file opening!"


def test_bigwig_open_failure_still_reports_the_failure(
    capfd: pytest.CaptureFixture[str],
) -> None:
    # Silencing the descriptor must not swallow the failure itself. The
    # caller still learns the open failed, through an exception that names no
    # url -- which is why the exception half needed no fixing.
    proto, resource = _a_refused_protocol("i1333-bw-raises")

    with pytest.raises(RuntimeError) as excinfo:
        proto.open_bigwig_file(resource, _BIGWIG_FILE_NAME)

    assert str(excinfo.value) == "Received an error during file opening!"
    _assert_no_credential_escaped(excinfo.value)
    assert _SECRET not in capfd.readouterr().err


def test_failed_authed_bigwig_open_does_not_silence_the_next_one(
    capfd: pytest.CaptureFixture[str],
) -> None:
    # fd 2 is PROCESS-global, so a suppression that does not lift turns one
    # authed open into permanent silence for everything that writes to stderr
    # afterwards -- far worse than the leak it closes. Asserted on that
    # consequence rather than on the ``dup2`` calls, so it stays true if the
    # mechanism changes. The failing path is the one that matters: a failed
    # open is exactly when libBigWig writes.
    authed, authed_res = _a_refused_protocol("i1333-restore-fail")
    plain, plain_res = _a_refused_protocol(
        "i1333-restore-fail-plain", authed=False)

    with pytest.raises(RuntimeError):
        authed.open_bigwig_file(authed_res, _BIGWIG_FILE_NAME)
    capfd.readouterr()

    with pytest.raises(RuntimeError):
        plain.open_bigwig_file(plain_res, _BIGWIG_FILE_NAME)

    assert "[urlOpen]" in capfd.readouterr().err


def test_successful_authed_bigwig_open_does_not_silence_the_next_one(
    capfd: pytest.CaptureFixture[str],
) -> None:
    # The same guarantee on the path that does not raise: restoring only
    # where the open failed would leave every successful authed read
    # swallowing stderr for good.
    authed, authed_res = _a_refused_protocol("i1333-restore-ok")
    plain, plain_res = _a_refused_protocol(
        "i1333-restore-ok-plain", authed=False)

    with unittest.mock.patch.object(pyBigWig, "open"):
        authed.open_bigwig_file(authed_res, _BIGWIG_FILE_NAME)
    capfd.readouterr()

    with pytest.raises(RuntimeError):
        plain.open_bigwig_file(plain_res, _BIGWIG_FILE_NAME)

    assert "[urlOpen]" in capfd.readouterr().err


def test_concurrent_authed_bigwig_opens_do_not_strand_stderr(
    capfd: pytest.CaptureFixture[str],
) -> None:
    """Overlapping suppressions must not leave the process without stderr.

    fd 2 is PROCESS-global, and bigwig opens ARE driven from a thread pool:
    ``web_api``'s pipeline cache loads pipelines on a ``ThreadedTaskExecutor``
    (8 loaders by default, and its gunicorn settings run 16 annotation
    workers), and opening a pipeline opens the bigwig tables in it.

    Two suppressions that overlap strand the descriptor. The second thread
    saves the FIRST thread's null device as its "previous" fd 2 and restores
    *that* on the way out, so the process is left with no stderr at all for
    the rest of its life -- every log line and every traceback written to it
    silently discarded, and nothing raised to say so. A worse outcome than
    the credential leak the suppression exists to close.

    The interleaving is FORCED rather than raced for. Thread two is started
    only once thread one is known to be inside the window, and thread one is
    held there until thread two has entered it too -- so thread two saves the
    null device, and sleeps long enough on the way out to restore it last.
    Left to chance this reproduces only sometimes: whether fd 2 ends up
    stranded depends on which thread happens to restore last, so the same
    test passed and then failed on consecutive runs before it was pinned this
    way.

    Thread one's wait is bounded because on CORRECT code thread two never
    enters the window at all -- it blocks on the lock, which is the fix --
    so the wait must end by itself rather than deadlock. It is kept short by
    waiting for thread two to REACH the open first: once that is known, the
    only remaining gap is the microseconds thread two would need to get
    inside were it not blocked, so the bound covers scheduling jitter rather
    than thread start-up. Waiting for entry directly would have to cover both
    and would be paid in full on every green run.
    """
    authed, resource = _a_refused_protocol("i1333-bw-threads")
    first_inside = threading.Event()
    second_started = threading.Event()
    second_inside = threading.Event()
    counter = itertools.count()

    def fake_open(*_args: typing.Any, **_kwargs: typing.Any) -> None:
        if next(counter) == 0:
            first_inside.set()
            assert second_started.wait(timeout=10.0), "second thread stalled"
            second_inside.wait(timeout=0.25)
        else:
            second_inside.set()
            # Outlast the first thread's restore, so this one -- holding the
            # null device as its "previous" -- is the one that restores last.
            time.sleep(0.1)

    # Restored unconditionally: a REGRESSION here strands fd 2, which would
    # otherwise take the rest of the suite's stderr down with it and report as
    # a cascade of unrelated failures somewhere else entirely.
    saved_stderr = os.dup(2)
    try:
        def work(*, second: bool = False) -> None:
            if second:
                # Set BEFORE the call, so the wait above covers only the gap
                # between reaching the open and being inside it.
                second_started.set()
            authed.open_bigwig_file(resource, _BIGWIG_FILE_NAME)

        # The real ``pyBigWig.open`` is restored before the probe below,
        # which needs libBigWig to actually write.
        with unittest.mock.patch.object(
                pyBigWig, "open", side_effect=fake_open):
            first = threading.Thread(target=work, daemon=True)
            first.start()
            assert first_inside.wait(timeout=5.0), "first thread never opened"
            second = threading.Thread(
                target=work, kwargs={"second": True}, daemon=True)
            second.start()
            first.join(timeout=30.0)
            second.join(timeout=30.0)
            assert not first.is_alive()
            assert not second.is_alive()
        capfd.readouterr()

        plain, plain_res = _a_refused_protocol(
            "i1333-bw-threads-plain", authed=False)
        with pytest.raises(RuntimeError):
            plain.open_bigwig_file(plain_res, _BIGWIG_FILE_NAME)

        assert "[urlOpen]" in capfd.readouterr().err
    finally:
        os.dup2(saved_stderr, 2)
        os.close(saved_stderr)


def test_fasta_open_failure_does_not_leak_url_credential(
    tmp_path: pathlib.Path, capfd: pytest.CaptureFixture[str],
) -> None:
    """A fasta open that actually reaches pysam must not leak the credential.

    This one needs a live server, and that is the whole point: the ``.gzi``
    precondition raises before any url is built, so a resource with no files
    -- the arrangement every other test in this section uses -- reports the
    method clean when it is not. The indexes are served for real so the
    precondition passes and the copy succeeds; only the multi-GB data file,
    which stays remote, is missing, so ``pysam.FastaFile`` is what fails.
    """
    resource_dir = tmp_path / "sub" / "res(1.0)"
    setup_genome_bgz(resource_dir / _FASTA_FILE_NAME, """
        >chr1
        NNACCCAAAC
        GGGCCTTCCN
    """)
    # Remove the data file, keeping its indexes: htslib then fails on the one
    # url it was handed remotely.
    (resource_dir / _FASTA_FILE_NAME).unlink()

    with serving_http(tmp_path) as base_url:
        host = base_url.removeprefix("http://")
        proto = build_fsspec_protocol(
            "i1314-fasta", f"http://alice:{_SECRET}@{host}")
        resource = GenomicResource("sub/res", (1, 0), proto, {})

        with pytest.raises(OSError) as excinfo:
            proto.open_fasta_file(resource, _FASTA_FILE_NAME)

    assert str(excinfo.value) == (
        "error when opening file "
        f"`http://{host}/sub/res(1.0)/{_FASTA_FILE_NAME}`")
    _assert_no_credential_escaped(excinfo.value)
    assert _SECRET not in capfd.readouterr().err


def test_fasta_open_still_hands_pysam_the_credential(
    mocker: pytest_mock.MockerFixture,
) -> None:
    # The data file stays remote and is fetched by htslib itself, so it is the
    # one url that must keep the credential. The two small indexes do not:
    # they are copied local first and pysam is handed temp paths.
    proto, resource = _a_refused_protocol("i1314-fasta-cred")
    mocker.patch.object(proto.filesystem, "exists", return_value=True)
    mocker.patch.object(
        FsspecReadOnlyProtocol, "_copy_resource_file_to_local",
        return_value="/tmp/index")  # ruff: ignore[hardcoded-temp-file]
    opened = mocker.patch.object(pysam, "FastaFile")

    proto.open_fasta_file(resource, _FASTA_FILE_NAME)

    assert _SECRET in opened.call_args.args[0]


# ---------------------------------------------------------------------------
# gain#1339 — the s3 shape. ``_get_file_url`` does not hand pysam the stored
# url for an s3 GRR: it hands ``filesystem.sign(url)``, a PRESIGNED url whose
# credential lives in the query string. gain#1314's remedies both key off
# ``user:pass@`` userinfo, which a presigned url does not carry, so neither
# channel it closed was closed for s3.
# ---------------------------------------------------------------------------

#: A presigned query string in each of the two shapes botocore actually
#: emits. Which one you get is a property of the endpoint and region, NOT of
#: anything GAIn configures -- it passes no ``signature_version``, and the
#: default against a custom endpoint is the OLDER of the two. That is why the
#: redactor drops the whole query string rather than a list of parameter
#: names: a name-keyed redactor written from the SigV4 spelling alone would
#: leak the shape most deployments actually produce.
_SIGV2_QUERY = (
    f"AWSAccessKeyId=alice&Signature={_SIGNATURE}&Expires=1789022041")
_SIGV4_QUERY = (
    "X-Amz-Algorithm=AWS4-HMAC-SHA256"
    "&X-Amz-Credential=alice%2F20260910%2Fus-east-1%2Fs3%2Faws4_request"
    "&X-Amz-Date=20260910T063222Z&X-Amz-Expires=100"
    f"&X-Amz-SignedHeaders=host&X-Amz-Signature={_SIGNATURE}"
)


def _a_refused_s3_protocol(
    proto_id: str, *, query: str | None = _SIGV2_QUERY,
) -> tuple[FsspecReadOnlyProtocol, GenomicResource]:
    """An s3 protocol whose presigned urls point at a refused port.

    ``build_fsspec_protocol`` cannot serve this arrangement: building an s3
    filesystem CONNECTS, so it cannot be pointed at a dead endpoint the way
    ``_a_refused_protocol`` points an http one. The filesystem is injected
    instead -- ADR 0021 names it as the protocol's real contract boundary --
    and ``sign`` is the single thing scripted on it, because it is the one
    thing an s3 GRR does here that an http one does not.

    ``FaultyFileSystem`` rather than a bare ``MagicMock``, and that is not
    only for consistency with the rest of this suite. A ``MagicMock``'s
    ``read`` never returns empty, so ANY path that reads through it -- the
    unindexed VCF branch consults the manifest, and ``yaml``'s encoding
    sniffer loops until the buffer is the size of memory -- hangs instead of
    failing. This filesystem is a real ``AbstractFileSystem`` over
    ``MemoryFileSystem``, so a read that is not scripted returns real bytes
    or a real error.

    ``query=None`` is the ANONYMOUS s3 GRR: ``sign`` on a filesystem with no
    credentials returns the url with no query string at all. It is a real
    configuration, not a hypothetical, and it is why the predicate cannot
    simply ask whether the scheme is s3.
    """
    def presign(url: str, **_kwargs: object) -> str:
        # What s3fs does in one line: the s3:// url becomes an https url on
        # the endpoint, with the credential appended as query parameters.
        signed = f"https://127.0.0.1:1/{url.removeprefix('s3://')}"
        return signed if query is None else f"{signed}?{query}"

    filesystem = FaultyFileSystem()
    filesystem.sign = presign  # type: ignore[method-assign]
    # A refused endpoint serves no ``.MANIFEST`` either, and the empty
    # ``MemoryFileSystem`` underneath would answer a manifest read with a
    # bare ``FileNotFoundError`` for a path this test never wrote. Scripted
    # explicitly so the arrangement states it.
    filesystem.fail_open("*", FileNotFoundError(GR_MANIFEST_FILE_NAME))
    proto = FsspecReadOnlyProtocol(
        proto_id, "s3://bucket/path", filesystem=filesystem)
    return proto, GenomicResource("sub/res", (1, 0), proto, {})


def _a_presigned_url(filename: str, *, query: str = _SIGV2_QUERY) -> str:
    """The url ``_a_refused_s3_protocol`` signs for ``filename``.

    Saves a test reaching into ``_get_file_url`` to find out.
    """
    return (
        f"https://127.0.0.1:1/bucket/path/sub/res(1.0)/{filename}?{query}")


#: Both presigned spellings, run through every leak fence. Parametrised
#: rather than covered by one representative shape because the two are not
#: interchangeable to a redactor: they share no parameter name, so an
#: implementation that recognises either one by name passes for it and leaks
#: the other.
_PRESIGNED_SHAPES = pytest.mark.parametrize(
    "query", [_SIGV2_QUERY, _SIGV4_QUERY], ids=["sigv2", "sigv4"])


@_PRESIGNED_SHAPES
def test_s3_tabix_open_failure_does_not_leak_presigned_signature(
    query: str, capfd: pytest.CaptureFixture[str],
) -> None:
    proto, resource = _a_refused_s3_protocol("i1339-tabix", query=query)

    with pytest.raises(OSError) as excinfo:
        proto.open_tabix_file(
            resource, _TABIX_FILE_NAME, f"{_TABIX_FILE_NAME}.tbi")

    # Pinned WHOLE, and the pin is what proves the ACCESS KEY ID went with the
    # signature: both are query parameters, so an assertion that the message
    # equals the query-free url is the one assertion that covers every
    # parameter the signer might add, named or not.
    assert str(excinfo.value) == (
        "could not open file "
        f"`https://127.0.0.1:1/bucket/path/sub/res(1.0)/{_TABIX_FILE_NAME}`")
    _assert_no_credential_escaped(excinfo.value)
    # The second channel, and the one no redaction of the raised error can
    # reach: htslib writes ``[E::hts_open_format] Failed to open file
    # "<url>"`` to fd 2 itself. gain#1314 closes it by bracketing the open at
    # verbosity 0, but only for urls its predicate calls credential-bearing --
    # and a presigned url was not one, so this channel stayed wide open for
    # every s3 GRR. Asserted at FD level: the write comes from C, and
    # ``capsys``, which only replaces ``sys.stderr``, never sees it.
    assert _SIGNATURE not in capfd.readouterr().err


def test_s3_tabix_open_still_hands_pysam_the_presigned_url(
    mocker: pytest_mock.MockerFixture,
) -> None:
    # The fence that no leak test can stand in for. Redacting the url before
    # handing it over reads as the tidier fix, leaves every assertion above
    # GREEN -- a credential-free url produces a clean message for the wrong
    # reason -- and breaks every authed s3 read in production, because the
    # signature IS the authorisation. Both urls carry it: htslib fetches the
    # index over the same presigned transport.
    proto, resource = _a_refused_s3_protocol("i1339-tabix-cred")
    opened = mocker.patch.object(pysam, "TabixFile")

    proto.open_tabix_file(
        resource, _TABIX_FILE_NAME, f"{_TABIX_FILE_NAME}.tbi")

    assert _SIGNATURE in opened.call_args.args[0]
    assert _SIGNATURE in opened.call_args.kwargs["index"]


def test_anonymous_s3_tabix_open_keeps_htslib_diagnostics(
    capfd: pytest.CaptureFixture[str],
) -> None:
    # An anonymous s3 GRR signs nothing: ``sign`` hands back the url with no
    # query string. It has no credential to protect, so it must keep htslib's
    # account of why the open failed -- the same bargain gain#1314 struck for
    # unauthed http, and the reason the predicate asks what the url CARRIES
    # rather than what its scheme is. Keying on ``scheme == "s3"`` passes
    # every leak test in this section and silences this one.
    proto, resource = _a_refused_s3_protocol("i1339-anon", query=None)

    with pytest.raises(OSError):
        proto.open_tabix_file(
            resource, _TABIX_FILE_NAME, f"{_TABIX_FILE_NAME}.tbi")

    assert "[E::hts_open_format]" in capfd.readouterr().err


def test_anonymous_s3_tabix_open_raises_the_librarys_own_error(
    mocker: pytest_mock.MockerFixture,
) -> None:
    # Nothing to redact must mean nothing touched: with no query string there
    # is no rebuild, so the library's own error propagates with its type, its
    # traceback and its chain intact. Asserted on IDENTITY, because a rebuild
    # carrying an identical message satisfies any assertion on the message.
    proto, resource = _a_refused_s3_protocol("i1339-anon-plain", query=None)
    planted = OSError(
        "could not open file "
        f"`https://127.0.0.1:1/bucket/path/sub/res(1.0)/{_TABIX_FILE_NAME}`")
    mocker.patch.object(pysam, "TabixFile", side_effect=planted)

    with pytest.raises(OSError) as excinfo:
        proto.open_tabix_file(
            resource, _TABIX_FILE_NAME, f"{_TABIX_FILE_NAME}.tbi")

    assert excinfo.value is planted


# What the redactor must make of a message a library hands back. One table
# rather than a function per shape, because every row is the same
# arrangement -- plant this message on the open, assert that one -- and the
# interesting content IS the pairs.
#
# The delimiter rows exist because the redactor acts on a url embedded in
# someone else's prose and the libraries disagree on how to fence it: pysam
# writes backticks, htslib's stderr writes double quotes. A pattern that ran
# to whitespace would eat the closing delimiter along with the query, so the
# delimiter and the text after it are pinned as carefully as the secret's
# absence.
_REDACTED_MESSAGES = [
    pytest.param(
        # Both shapes on one url -- a presigned url derived from an endpoint
        # that itself carries userinfo. Dropping either alone still leaks, so
        # the two redactors have to compose rather than choose.
        f"could not open file `https://alice:{_SECRET}@host/f.gz"
        f"?Signature={_SIGNATURE}`",
        "could not open file `https://host/f.gz`",
        id="userinfo-and-query"),
    pytest.param(
        # The redactors do not commute. A password may itself contain ``?``;
        # strip the query FIRST and the url is cut there, keeping half the
        # password and deleting the host -- so the message both leaks and
        # stops naming which GRR failed.
        f"could not open file `https://alice:{_SECRET}?x@host/f.gz`",
        "could not open file `https://host/f.gz`",
        id="password-containing-question-mark"),
    pytest.param(
        # The substitution must be global. Both of a tabix open's urls are
        # presigned -- htslib fetches the index over the same signed
        # transport -- so a message naming two must lose both.
        f"copy `https://host/a.gz?Signature={_SIGNATURE}1` -> "
        f"`https://host/b.gz?Signature={_SIGNATURE}2` failed",
        "copy `https://host/a.gz` -> `https://host/b.gz` failed",
        id="two-urls"),
    *(
        pytest.param(
            f"could not open file {opened}https://host/f.gz"
            f"?Signature={_SIGNATURE}{closed} : Connection refused",
            f"could not open file {opened}https://host/f.gz{closed}"
            " : Connection refused",
            id=f"delimiter-{name}")
        for name, opened, closed in [
            ("backtick", "`", "`"),
            ("double-quote", '"', '"'),
            ("single-quote", "'", "'"),
            ("angle", "<", ">"),
        ]
    ),
]


@pytest.mark.parametrize(("planted", "expected"), _REDACTED_MESSAGES)
def test_s3_open_failure_redacts_the_message(
    planted: str, expected: str, mocker: pytest_mock.MockerFixture,
) -> None:
    proto, resource = _a_refused_s3_protocol("i1339-messages")
    mocker.patch.object(pysam, "TabixFile", side_effect=OSError(planted))

    with pytest.raises(OSError) as excinfo:
        proto.open_tabix_file(
            resource, _TABIX_FILE_NAME, f"{_TABIX_FILE_NAME}.tbi")

    assert str(excinfo.value) == expected
    _assert_no_credential_escaped(excinfo.value)


def test_s3_open_failure_keeps_a_query_free_url_whole(
    mocker: pytest_mock.MockerFixture,
) -> None:
    # A url with no query string comes through untouched even when the
    # message around it contains a ``?`` -- which is what keeps the message
    # able to say WHICH resource failed.
    #
    # Scoped deliberately: the ``?`` here is separated from the url by the
    # closing backtick. A url ending in a BARE ``?`` is a different case, and
    # the redactor does consume it -- ``_url_carries_credentials`` answers
    # True for ``https://host/f.gz?``, so the bracket would fire for a url
    # carrying nothing. That shape is unreachable: every url this predicate
    # sees comes from ``_get_file_url``, which either signs (a query) or
    # returns a ``_fetch_url``-derived url, and ``_fetch_url_form`` rebuilds
    # ``scheme://netloc/path`` and so cannot carry a ``?`` at all.
    proto, resource = _a_refused_s3_protocol("i1339-noquery")
    planted = OSError(
        "could not open file `https://host/f.gz`: is it there? no")
    mocker.patch.object(pysam, "TabixFile", side_effect=planted)

    with pytest.raises(OSError) as excinfo:
        proto.open_tabix_file(
            resource, _TABIX_FILE_NAME, f"{_TABIX_FILE_NAME}.tbi")

    # Identity, not equality: with nothing to redact there must be no rebuild
    # at all, so the traceback and chain survive.
    assert excinfo.value is planted


def test_failed_presigned_open_does_not_silence_the_next_one(
    capfd: pytest.CaptureFixture[str],
) -> None:
    # The verbosity level is PROCESS-global, so a bracket that does not
    # restore turns one presigned open into permanent silence for every
    # htslib user after it. gain#1314 pins this for the userinfo shape; the
    # widened predicate brings a second way into the bracket, and it needs the
    # same guarantee. Asserted on the consequence, not on ``set_verbosity``
    # calls, so it stays true if the mechanism changes.
    signed, signed_res = _a_refused_s3_protocol("i1339-restore-fail")
    anon, anon_res = _a_refused_s3_protocol(
        "i1339-restore-fail-anon", query=None)

    with pytest.raises(OSError):
        signed.open_tabix_file(
            signed_res, _TABIX_FILE_NAME, f"{_TABIX_FILE_NAME}.tbi")
    capfd.readouterr()

    with pytest.raises(OSError):
        anon.open_tabix_file(
            anon_res, _TABIX_FILE_NAME, f"{_TABIX_FILE_NAME}.tbi")

    assert "[E::hts_open_format]" in capfd.readouterr().err


def test_successful_presigned_open_does_not_silence_the_next_one(
    capfd: pytest.CaptureFixture[str],
) -> None:
    # The same guarantee on the path that does not raise -- restoring only
    # where the open failed would leave every successful presigned read
    # silencing htslib for good, which on an s3 GRR is every read.
    signed, signed_res = _a_refused_s3_protocol("i1339-restore-ok")
    anon, anon_res = _a_refused_s3_protocol(
        "i1339-restore-ok-anon", query=None)

    with unittest.mock.patch.object(pysam, "TabixFile"):
        signed.open_tabix_file(
            signed_res, _TABIX_FILE_NAME, f"{_TABIX_FILE_NAME}.tbi")
    capfd.readouterr()

    with pytest.raises(OSError):
        anon.open_tabix_file(
            anon_res, _TABIX_FILE_NAME, f"{_TABIX_FILE_NAME}.tbi")

    assert "[E::hts_open_format]" in capfd.readouterr().err


@pytest.mark.parametrize(
    ("index_filename", "index_exists"),
    [(None, False), (f"{_VCF_FILE_NAME}.tbi", True)],
    ids=["unindexed", "indexed"])
def test_s3_vcf_open_failure_does_not_leak_presigned_signature(
    index_filename: str | None, index_exists: bool,
    capfd: pytest.CaptureFixture[str], mocker: pytest_mock.MockerFixture,
) -> None:
    # Both of this method's routes to pysam, for the same reason gain#1314
    # pins both: the indexed open a configured table takes, and the early
    # return for a file that ships no index at all.
    proto, resource = _a_refused_s3_protocol(f"i1339-vcf-{index_exists}")
    mocker.patch.object(
        proto.filesystem, "exists", return_value=index_exists)

    with pytest.raises(OSError) as excinfo:
        proto.open_vcf_file(resource, _VCF_FILE_NAME, index_filename)

    assert str(excinfo.value) == (
        "[Errno 111] Could not open variant file: Connection refused: "
        f"'https://127.0.0.1:1/bucket/path/sub/res(1.0)/{_VCF_FILE_NAME}'")
    _assert_no_credential_escaped(excinfo.value)
    assert _SIGNATURE not in capfd.readouterr().err


def test_s3_fasta_open_failure_does_not_leak_presigned_signature(
    tmp_path: pathlib.Path,
    capfd: pytest.CaptureFixture[str], mocker: pytest_mock.MockerFixture,
) -> None:
    # Only the data file stays remote and reaches htslib with a signed url;
    # the two small indexes are copied local first and pysam is handed those
    # paths. They have to be REAL files: pysam stats both before it opens
    # anything, so a stand-in path fails the precondition and the method never
    # reaches the url this test is about. Built for real and handed over in
    # the order the method copies them -- ``.fai`` then ``.gzi``.
    resource_dir = tmp_path / "sub" / "res(1.0)"
    setup_genome_bgz(resource_dir / _FASTA_FILE_NAME, """
        >chr1
        NNACCCAAAC
        GGGCCTTCCN
    """)
    proto, resource = _a_refused_s3_protocol("i1339-fasta")
    mocker.patch.object(proto.filesystem, "exists", return_value=True)
    mocker.patch.object(
        FsspecReadOnlyProtocol, "_copy_resource_file_to_local",
        side_effect=[
            str(resource_dir / f"{_FASTA_FILE_NAME}.fai"),
            str(resource_dir / f"{_FASTA_FILE_NAME}.gzi"),
        ])

    with pytest.raises(OSError) as excinfo:
        proto.open_fasta_file(resource, _FASTA_FILE_NAME)

    assert str(excinfo.value) == (
        "error when opening file "
        f"`https://127.0.0.1:1/bucket/path/sub/res(1.0)/{_FASTA_FILE_NAME}`")
    _assert_no_credential_escaped(excinfo.value)
    assert _SIGNATURE not in capfd.readouterr().err


def test_s3_bigwig_open_failure_does_not_leak_presigned_signature(
    mocker: pytest_mock.MockerFixture,
) -> None:
    # ``open_bigwig_file`` takes the widened REDACTION and still no bracket:
    # libBigWig is not htslib and ``pysam.set_verbosity`` cannot reach it, so
    # its own stderr line stays gain#1333's -- for the presigned url exactly
    # as for the userinfo one. The message is planted because pyBigWig's own
    # error carries no url today; the wrapper is what keeps a future one, or a
    # failure from any layer in between, from carrying the signature out.
    proto, resource = _a_refused_s3_protocol("i1339-bw")
    mocker.patch.object(
        pyBigWig, "open",
        side_effect=RuntimeError(
            f"Couldn't open {_a_presigned_url(_BIGWIG_FILE_NAME)} "
            "for reading"))

    with pytest.raises(RuntimeError) as excinfo:
        proto.open_bigwig_file(resource, _BIGWIG_FILE_NAME)

    assert str(excinfo.value) == (
        "Couldn't open https://127.0.0.1:1/bucket/path/sub/res(1.0)/"
        f"{_BIGWIG_FILE_NAME} for reading")
    _assert_no_credential_escaped(excinfo.value)
