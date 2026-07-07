# pylint: disable=C0114,C0116
import pytest
from gain.genomic_resources.repository_factory import (
    _REPO_DEFINITION_ADAPTER,
    build_genomic_resource_repository,
)
from pydantic import ValidationError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _valid(definition: dict) -> None:
    _REPO_DEFINITION_ADAPTER.validate_python(definition)


def _invalid(definition: dict) -> ValidationError:
    with pytest.raises(ValidationError) as exc_info:
        _REPO_DEFINITION_ADAPTER.validate_python(definition)
    return exc_info.value


# ---------------------------------------------------------------------------
# Valid definitions — one per type
# ---------------------------------------------------------------------------

def test_http_minimal() -> None:
    _valid({"type": "http", "url": "https://grr.example.com"})


def test_http_with_auth() -> None:
    _valid({"type": "http", "url": "https://grr.example.com",
            "user": "alice", "password": "s3cr3t"})


def test_http_with_all_fields() -> None:
    _valid({"id": "my-grr", "type": "http", "url": "https://grr.example.com",
            "user": "alice", "password": "s3cr3t",
            "cache_dir": "/var/cache/grr",
            "public_url": "https://pub.example.com"})


def test_file_types() -> None:
    for repo_type in ("file", "dir", "directory"):
        _valid({"type": repo_type, "directory": "/data/grr"})


def test_file_read_only() -> None:
    _valid({"type": "directory", "directory": "/data/grr", "read_only": True})


def test_s3_minimal() -> None:
    _valid({"type": "s3", "url": "s3://my-bucket/grr"})


def test_s3_with_endpoint() -> None:
    _valid({"type": "s3", "url": "s3://my-bucket/grr",
            "endpoint_url": "http://localhost:9000"})


def test_url_minimal() -> None:
    _valid({"type": "url", "url": "https://grr.example.com"})


def test_embedded_empty() -> None:
    _valid({"type": "embedded", "content": {}})


def test_memory_empty() -> None:
    _valid({"type": "memory"})


def test_group_minimal() -> None:
    _valid({"type": "group", "children": [
        {"type": "http", "url": "https://grr.example.com"},
        {"type": "embedded", "content": {}},
    ]})


def test_group_nested() -> None:
    _valid({"type": "group", "children": [
        {"type": "group", "children": [
            {"type": "http", "url": "https://grr.example.com",
             "user": "u", "password": "p"},
        ]},
    ]})


# ---------------------------------------------------------------------------
# user / password must be given together
# ---------------------------------------------------------------------------

def test_http_only_user_is_rejected() -> None:
    err = _invalid({"type": "http", "url": "https://x.com", "user": "alice"})
    assert err.error_count() == 1
    assert "together" in str(err)


def test_http_only_password_is_rejected() -> None:
    secret = "s3cr3t-schema"  # noqa: S105
    err = _invalid({"type": "http", "url": "https://x.com",
                    "password": secret})
    assert err.error_count() == 1
    assert "together" in str(err)
    # the plaintext password must not be echoed back in str()/traceback
    # (hide_input_in_errors=True). The bare adapter's .errors()/.json() are
    # NOT scrubbed by that flag and still carry the secret here; the build path
    # closes that leak by discarding the ValidationError entirely (it raises a
    # redacted ValueError with no ValidationError on its chain), asserted by
    # test_build_one_sided_credential_does_not_leak_secret in
    # test_http_auth_credential_leak.py.
    assert secret not in str(err)


# ---------------------------------------------------------------------------
# user / password are forbidden on non-http types
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("repo_type,extra", [
    ("s3", {"url": "s3://bucket/grr"}),
    ("url", {"url": "https://grr.example.com"}),
    ("directory", {"directory": "/data/grr"}),
    ("embedded", {"content": {}}),
])
def test_auth_fields_forbidden_on_non_http(
    repo_type: str, extra: dict,
) -> None:
    _invalid({"type": repo_type, "user": "alice", "password": "p", **extra})


# ---------------------------------------------------------------------------
# Typos / unknown fields are caught
# ---------------------------------------------------------------------------

def test_http_unknown_field_is_rejected() -> None:
    _invalid({"type": "http", "url": "https://x.com", "pasword": "oops"})


def test_http_username_instead_of_user_is_rejected() -> None:
    _invalid({"type": "http", "url": "https://x.com",
              "username": "alice", "password": "s3cr3t"})


def test_file_unknown_field_is_rejected() -> None:
    _invalid({"type": "directory", "directory": "/data", "xtra": "oops"})


# ---------------------------------------------------------------------------
# Missing required fields
# ---------------------------------------------------------------------------

def test_http_missing_url_is_rejected() -> None:
    _invalid({"type": "http"})


def test_file_missing_directory_is_rejected() -> None:
    _invalid({"type": "directory"})


def test_s3_missing_url_is_rejected() -> None:
    _invalid({"type": "s3"})


def test_group_missing_children_is_rejected() -> None:
    _invalid({"type": "group"})


# ---------------------------------------------------------------------------
# Unknown type
# ---------------------------------------------------------------------------

def test_unknown_type_is_rejected() -> None:
    _invalid({"type": "ftp", "url": "ftp://grr.example.com"})


# ---------------------------------------------------------------------------
# Integration — validation fires inside build_genomic_resource_repository
# ---------------------------------------------------------------------------

# build_genomic_resource_repository re-raises validation failures as a plain
# ValueError (with a redacted message) to close the .errors()/.json() leak;
# ValidationError is itself a ValueError, so ValueError is the correct guard.
def test_build_raises_on_credential_mismatch() -> None:
    with pytest.raises(ValueError):
        build_genomic_resource_repository(
            {"type": "http", "url": "https://x.com", "user": "alice"})


def test_build_raises_on_unknown_field() -> None:
    with pytest.raises(ValueError):
        build_genomic_resource_repository(
            {"type": "http", "url": "https://x.com", "pasword": "oops"})
