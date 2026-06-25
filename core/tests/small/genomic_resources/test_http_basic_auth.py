# pylint: disable=W0621,W0622,C0114,C0116
import base64
import contextlib
import functools
import http.server
import pathlib
import threading
from collections.abc import Generator
from typing import Any

import pytest
from gain.genomic_resources.fsspec_protocol import build_fsspec_protocol

_TEST_USER = "testuser"
_TEST_PASSWORD = "testpass"  # noqa: S105
_TEST_FILE = "hello.txt"
_TEST_CONTENT = "hello world\n"


class _BasicAuthHTTPHandler(http.server.SimpleHTTPRequestHandler):
    """SimpleHTTPRequestHandler that requires HTTP Basic authentication."""

    def _is_authorized(self) -> bool:
        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Basic "):
            return False
        credentials = base64.b64decode(auth[6:]).decode()
        username, _, password = credentials.partition(":")
        return username == _TEST_USER and password == _TEST_PASSWORD

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

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        pass  # suppress server output in tests


@contextlib.contextmanager
def _auth_http_server(
    serve_dir: pathlib.Path,
) -> Generator[str, None, None]:
    """Spin up a localhost HTTP server requiring Basic auth over `serve_dir`."""
    handler = functools.partial(
        _BasicAuthHTTPHandler, directory=str(serve_dir))
    with http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler) as httpd:
        port = httpd.server_address[1]
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{port}"
        finally:
            httpd.shutdown()


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
    with pytest.raises(Exception), proto.filesystem.open(  # noqa: B017
            f"{auth_server}/{_TEST_FILE}", "rt") as f:
        f.read()


def test_http_basic_auth_wrong_credentials(auth_server: str) -> None:
    """Wrong credentials → server returns 401 → exception raised."""
    proto = build_fsspec_protocol(
        f"auth-wrong:{auth_server}", auth_server,
        user="wronguser", password="wrongpass",
    )
    with pytest.raises(Exception), proto.filesystem.open(  # noqa: B017
            f"{auth_server}/{_TEST_FILE}", "rt") as f:
        f.read()
