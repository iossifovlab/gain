# pylint: disable=W0621,W0622,C0114,C0116
import base64
import contextlib
import pathlib
from collections.abc import Generator

import pytest
from gain.genomic_resources.fsspec_protocol import build_fsspec_protocol

from .conftest import QuietHTTPRequestHandler, serving_http

_TEST_USER = "testuser"
_TEST_PASSWORD = "testpass"  # ruff: ignore[hardcoded-password-string]
_TEST_FILE = "hello.txt"
_TEST_CONTENT = "hello world\n"


class _BasicAuthHTTPHandler(QuietHTTPRequestHandler):
    """SimpleHTTPRequestHandler that requires HTTP Basic authentication.

    The credential is decoded as UTF-8, per RFC 7617; subclasses override
    ``expected_user`` / ``expected_password`` to accept other credentials.
    """

    expected_user = _TEST_USER
    expected_password = _TEST_PASSWORD

    def _is_authorized(self) -> bool:
        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Basic "):
            return False
        try:
            credentials = base64.b64decode(auth[6:]).decode("utf-8")
        except UnicodeDecodeError:
            return False
        username, _, password = credentials.partition(":")
        return (username == self.expected_user
                and password == self.expected_password)

    def _send_401(self) -> None:
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="test"')
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:
        if not self._is_authorized():
            self._send_401()
            return
        super().do_GET()

    def do_HEAD(self) -> None:
        if not self._is_authorized():
            self._send_401()
            return
        super().do_HEAD()


@contextlib.contextmanager
def _auth_http_server(
    serve_dir: pathlib.Path,
    handler_cls: type[_BasicAuthHTTPHandler] = _BasicAuthHTTPHandler,
) -> Generator[str, None, None]:
    """Spin up a localhost HTTP server requiring Basic auth over `serve_dir`."""
    with serving_http(serve_dir, handler_cls) as base_url:
        yield base_url


@pytest.fixture
def auth_server(tmp_path: pathlib.Path) -> Generator[str, None, None]:
    (tmp_path / _TEST_FILE).write_text(_TEST_CONTENT)
    with _auth_http_server(tmp_path) as base_url:
        yield base_url


def test_http_basic_auth_success(auth_server: str) -> None:
    """Correct credentials allow reading a file."""
    proto = build_fsspec_protocol(
        f"auth-ok:{auth_server}", auth_server,
        user=_TEST_USER, password=_TEST_PASSWORD,
    )
    with proto.filesystem.open(
            f"{auth_server}/{_TEST_FILE}", "rt") as f:
        assert f.read() == _TEST_CONTENT


def test_http_basic_auth_no_credentials(auth_server: str) -> None:
    """No credentials → server returns 401 → exception raised."""
    proto = build_fsspec_protocol(f"auth-none:{auth_server}", auth_server)
    with pytest.raises(Exception), proto.filesystem.open(  # ruff: ignore[assert-raises-exception]
            f"{auth_server}/{_TEST_FILE}", "rt") as f:
        f.read()


def test_http_basic_auth_wrong_credentials(auth_server: str) -> None:
    """Wrong credentials → server returns 401 → exception raised."""
    proto = build_fsspec_protocol(
        f"auth-wrong:{auth_server}", auth_server,
        user="wronguser", password="wrongpass",
    )
    with pytest.raises(Exception), proto.filesystem.open(  # ruff: ignore[assert-raises-exception]
            f"{auth_server}/{_TEST_FILE}", "rt") as f:
        f.read()


def _userinfo_url(base_url: str, user: str, password: str) -> str:
    scheme, _, hostinfo = base_url.partition("://")
    return f"{scheme}://{user}:{password}@{hostinfo}"


def test_http_url_userinfo_auth_still_reads(auth_server: str) -> None:
    """URL-embedded userinfo (no user/password kwargs) still authenticates.

    This is the end-to-end proof that stripping userinfo from the DISPLAY url
    does not break auth: the credential travels only in the credential-bearing
    fetch base (``_fetch_url``), and aiohttp extracts Basic auth from it.
    """
    url = _userinfo_url(auth_server, _TEST_USER, _TEST_PASSWORD)
    proto = build_fsspec_protocol(f"auth-userinfo:{url}", url)
    # Reading through the protocol's own credential-bearing fetch base succeeds.
    with proto.filesystem.open(
            f"{proto._fetch_url}/{_TEST_FILE}", "rt") as f:
        assert f.read() == _TEST_CONTENT
    # ...while the display url exposes no credential.
    assert _TEST_PASSWORD not in proto.get_public_url()
    assert _TEST_USER not in proto.get_public_url()
    assert _TEST_PASSWORD not in proto.get_url()


def test_http_url_userinfo_and_kwargs_together_still_reads(
    auth_server: str,
) -> None:
    """URL-embedded userinfo alongside user/password kwargs still reads.

    aiohttp refuses a request that carries credentials both in the url and
    as an ``Authorization`` header, so the url's credential is the one
    that travels when both are given.
    """
    url = _userinfo_url(auth_server, _TEST_USER, _TEST_PASSWORD)
    proto = build_fsspec_protocol(
        f"auth-both:{url}", url, user=_TEST_USER, password=_TEST_PASSWORD)
    with proto.filesystem.open(
            f"{proto._fetch_url}/{_TEST_FILE}", "rt") as f:
        assert f.read() == _TEST_CONTENT


def test_http_basic_auth_colon_in_user_is_rejected(auth_server: str) -> None:
    """A ``:`` in the user is refused up front (RFC 7617 §2)."""
    with pytest.raises(ValueError, match='":"'):
        build_fsspec_protocol(
            f"auth-colon:{auth_server}", auth_server,
            user="al:ice", password=_TEST_PASSWORD,
        )


def test_http_url_userinfo_wrong_password_401(auth_server: str) -> None:
    """A wrong URL-embedded password still reaches the server and 401s."""
    url = _userinfo_url(auth_server, _TEST_USER, "wrongpass")
    proto = build_fsspec_protocol(f"auth-userinfo-bad:{url}", url)
    with pytest.raises(Exception), proto.filesystem.open(  # ruff: ignore[assert-raises-exception]
            f"{proto._fetch_url}/{_TEST_FILE}", "rt") as f:
        f.read()


def test_http_basic_auth_does_not_need_aiohttp_basic_auth(
    auth_server: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Credentialed reads work without ``aiohttp.BasicAuth`` (gone in 4.0).

    The credential travels as an ``Authorization: Basic …`` session header
    the protocol builds itself, so the auth path survives the removal of
    ``aiohttp.BasicAuth`` (deprecated since aiohttp 3.14).
    """
    monkeypatch.delattr("aiohttp.BasicAuth")
    proto = build_fsspec_protocol(
        f"auth-no-basicauth:{auth_server}", auth_server,
        user=_TEST_USER, password=_TEST_PASSWORD,
    )
    with proto.filesystem.open(
            f"{auth_server}/{_TEST_FILE}", "rt") as f:
        assert f.read() == _TEST_CONTENT


_UNICODE_USER = "tëstüser"
_UNICODE_PASSWORD = "pässwörd"  # ruff: ignore[hardcoded-password-string]


class _UnicodeBasicAuthHTTPHandler(_BasicAuthHTTPHandler):
    expected_user = _UNICODE_USER
    expected_password = _UNICODE_PASSWORD


@pytest.fixture
def unicode_auth_server(tmp_path: pathlib.Path) -> Generator[str, None, None]:
    (tmp_path / _TEST_FILE).write_text(_TEST_CONTENT)
    with _auth_http_server(tmp_path, _UnicodeBasicAuthHTTPHandler) as base_url:
        yield base_url


def test_http_basic_auth_non_ascii_credentials_are_utf8(
    unicode_auth_server: str,
) -> None:
    """Non-ASCII credentials reach the server UTF-8 encoded (RFC 7617)."""
    proto = build_fsspec_protocol(
        f"auth-utf8:{unicode_auth_server}", unicode_auth_server,
        user=_UNICODE_USER, password=_UNICODE_PASSWORD,
    )
    with proto.filesystem.open(
            f"{unicode_auth_server}/{_TEST_FILE}", "rt") as f:
        assert f.read() == _TEST_CONTENT
