# pylint: disable=C0114,C0116,W0212
import logging
import pathlib
import textwrap

import pytest
import pytest_mock
from gain.genomic_resources import cli as grr_cli
from gain.genomic_resources.cli import cli_browse
from gain.genomic_resources.fsspec_protocol import build_fsspec_protocol
from gain.genomic_resources.repository_factory import (
    _REPO_DEFINITION_ADAPTER,
    HttpRepoDefinition,
    build_genomic_resource_repository,
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
