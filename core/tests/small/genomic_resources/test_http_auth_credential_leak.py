# pylint: disable=C0114,C0116,W0212
import logging

import pytest
from gain.genomic_resources.fsspec_protocol import build_fsspec_protocol
from gain.genomic_resources.repository_factory import (
    _REPO_DEFINITION_ADAPTER,
    HttpRepoDefinition,
    build_genomic_resource_repository,
)

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
