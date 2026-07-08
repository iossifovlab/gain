# pylint: disable=C0114,C0116,W0212
import logging
import pathlib
import textwrap
import traceback

import pytest
import pytest_mock
from gain.genomic_resources import cli as grr_cli
from gain.genomic_resources.cli import cli_browse
from gain.genomic_resources.fsspec_protocol import build_fsspec_protocol
from gain.genomic_resources.repository import GenomicResource
from gain.genomic_resources.repository_factory import (
    _REPO_DEFINITION_ADAPTER,
    HttpRepoDefinition,
    UrlRepoDefinition,
    build_genomic_resource_repository,
    redact_definition,
)
from pydantic import ValidationError

_SECRET = "s3cr3t-do-not-log"  # noqa: S105


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
    proto = build_fsspec_protocol(
        "authed", "https://grr.example.com",
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
    mocker.patch.object(grr_cli, "_run_list_command")

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
    assert dumped["password"] == "***"  # noqa: S105
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
    # The auth-building path (build_fsspec_protocol -> aiohttp.BasicAuth) reads
    # the real credentials via attribute/dict access, NOT via model_dump(), so
    # masking the dump must not touch the plaintext value stored on the model.
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
    token = "ghp_SUPERSECRETTOKEN123"  # noqa: S105
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
# (``scheme://user:pass@host``) are a functional aiohttp BasicAuth config, but
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
