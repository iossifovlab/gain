# pylint: disable=C0114,C0116,W0621
import base64
import importlib
import logging
import logging.config
import socket
import threading
from collections.abc import Generator, Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import cast
from urllib.parse import parse_qs

import pytest
from django.http import HttpRequest, HttpResponse
from django.test import Client
from django.urls import path
from gain.utils.zulip_logging import ZulipHandler, zulip_logger

from web_annotation import settings_default

ZULIP_ENV = {
    "GPFWA_ZULIP_SITE": "https://zulip.example",
    "GPFWA_ZULIP_BOT_EMAIL": "gainweb-bot@zulip.example",
    "GPFWA_ZULIP_API_KEY": "s3cr3t-api-key",
    "GPFWA_ZULIP_STREAM": "gainweb-alerts",
}


def _boom_view(request: HttpRequest) -> HttpResponse:
    raise RuntimeError("boom")


# The test-only URL conf for ``pytest.mark.urls``.
urlpatterns = [path("zulip-boom/", _boom_view)]


@pytest.fixture
def restore_settings_default(
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[None, None, None]:
    """Reload settings_default so per-test env tweaks don't leak out."""
    yield
    monkeypatch.undo()
    importlib.reload(settings_default)


def _set_zulip_env(
    monkeypatch: pytest.MonkeyPatch, env: dict[str, str],
) -> None:
    for name in ZULIP_ENV:
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)


def _resolve(class_name: str) -> type:
    """Resolve a dotted class name the way ``dictConfig`` resolves it."""
    resolved: type = logging.config.BaseConfigurator({}).resolve(class_name)
    return resolved


def _handler_from_settings() -> ZulipHandler:
    """Build the ``"zulip"`` handler the way ``dictConfig`` builds it.

    The test does not call ``dictConfig``: a non-incremental call shuts
    down every handler in the process, the handlers of pytest included.
    """
    config = dict(settings_default.LOGGING["handlers"]["zulip"])
    handler_class = _resolve(config.pop("class"))
    handler: ZulipHandler = handler_class(**config)
    return handler


def test_all_four_variables_add_a_zulip_root_handler(
    monkeypatch: pytest.MonkeyPatch,
    restore_settings_default: None,
) -> None:
    _set_zulip_env(monkeypatch, ZULIP_ENV)

    importlib.reload(settings_default)

    assert "zulip" in settings_default.LOGGING["handlers"]
    assert "zulip" in settings_default.LOGGING["loggers"][""]["handlers"]


@pytest.mark.parametrize(
    "missing",
    [list(ZULIP_ENV), *([name] for name in ZULIP_ENV)],
    ids=["none-set", *(f"no-{name}" for name in ZULIP_ENV)],
)
def test_a_missing_variable_leaves_logging_unchanged(
    monkeypatch: pytest.MonkeyPatch,
    restore_settings_default: None,
    missing: list[str],
) -> None:
    _set_zulip_env(monkeypatch, {})
    importlib.reload(settings_default)
    baseline = settings_default.LOGGING
    _set_zulip_env(monkeypatch, {
        name: value for name, value in ZULIP_ENV.items()
        if name not in missing
    })

    importlib.reload(settings_default)

    assert "zulip" not in settings_default.LOGGING["handlers"]
    assert baseline == settings_default.LOGGING


def test_a_blank_variable_counts_as_missing(
    monkeypatch: pytest.MonkeyPatch,
    restore_settings_default: None,
) -> None:
    _set_zulip_env(monkeypatch, {**ZULIP_ENV, "GPFWA_ZULIP_STREAM": "  "})

    importlib.reload(settings_default)

    assert "zulip" not in settings_default.LOGGING["handlers"]
    assert settings_default.LOGGING["loggers"][""]["handlers"] == [
        "console", "logdebug",
    ]


def test_the_handler_class_builds_from_the_other_keys(
    monkeypatch: pytest.MonkeyPatch,
    restore_settings_default: None,
) -> None:
    _set_zulip_env(monkeypatch, ZULIP_ENV)
    importlib.reload(settings_default)

    class_name = settings_default.LOGGING["handlers"]["zulip"]["class"]

    handler = _handler_from_settings()

    assert _resolve(class_name) is ZulipHandler
    assert handler.url == "https://zulip.example/api/v1/messages"
    assert handler.stream == "gainweb-alerts"


def test_the_topic_is_the_public_name(
    monkeypatch: pytest.MonkeyPatch,
    restore_settings_default: None,
) -> None:
    _set_zulip_env(monkeypatch, ZULIP_ENV)
    monkeypatch.setenv("GPFWA_PUBLIC_NAME", "gain.example.org")

    importlib.reload(settings_default)

    zulip = settings_default.LOGGING["handlers"]["zulip"]
    assert zulip["topic"] == "gain.example.org"


def test_without_a_public_name_the_topic_is_the_hostname(
    monkeypatch: pytest.MonkeyPatch,
    restore_settings_default: None,
) -> None:
    _set_zulip_env(monkeypatch, ZULIP_ENV)
    monkeypatch.delenv("GPFWA_PUBLIC_NAME", raising=False)

    importlib.reload(settings_default)

    zulip = settings_default.LOGGING["handlers"]["zulip"]
    assert zulip["topic"] == socket.gethostname()


class _CaptureHandler(BaseHTTPRequestHandler):
    # ``http.server`` dispatches a POST to the method named ``do_POST``.
    def do_POST(self) -> None:  # pylint: disable=invalid-name
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8")
        # The stub types ``self.server`` as ``BaseServer``, but this handler
        # only ever serves a ``_CaptureServer``.
        server = cast(_CaptureServer, self.server)
        server.posts.append({k: v[0] for k, v in parse_qs(raw).items()})
        server.authorizations.append(self.headers.get("Authorization", ""))
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()


class _CaptureServer(HTTPServer):
    """Stand in for ``<site>/api/v1/messages`` and record each POST."""

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _CaptureHandler)
        self.posts: list[dict[str, str]] = []
        self.authorizations: list[str] = []

    @property
    def site(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}"


@pytest.fixture
def zulip_server() -> Iterator[_CaptureServer]:
    server = _CaptureServer()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()
    thread.join()


@pytest.fixture
def live_zulip_handler(
    monkeypatch: pytest.MonkeyPatch,
    restore_settings_default: None,
    zulip_server: _CaptureServer,
) -> Iterator[ZulipHandler]:
    """Add the handler that ``LOGGING`` describes to the root logger."""
    _set_zulip_env(
        monkeypatch, {**ZULIP_ENV, "GPFWA_ZULIP_SITE": zulip_server.site})
    importlib.reload(settings_default)
    handler = _handler_from_settings()
    root = logging.getLogger()
    root.addHandler(handler)
    yield handler
    root.removeHandler(handler)
    handler.close()


@pytest.mark.urls(__name__)
def test_a_django_500_posts_one_message(
    zulip_server: _CaptureServer,
    live_zulip_handler: ZulipHandler,
) -> None:
    client = Client(raise_request_exception=False)

    response = client.get("/zulip-boom/")

    assert response.status_code == 500
    assert len(zulip_server.posts) == 1
    content = zulip_server.posts[0]["content"]
    assert "RuntimeError" in content
    assert "boom" in content


def test_the_zulip_logger_posts_an_info_record(
    zulip_server: _CaptureServer,
    live_zulip_handler: ZulipHandler,
) -> None:
    zulip_logger().info("hello")

    assert len(zulip_server.posts) == 1
    assert "hello" in zulip_server.posts[0]["content"]


def test_the_post_authenticates_as_the_bot_email_and_api_key(
    zulip_server: _CaptureServer,
    live_zulip_handler: ZulipHandler,
) -> None:
    zulip_logger().info("hello")

    assert len(zulip_server.authorizations) == 1
    scheme, _, encoded = zulip_server.authorizations[0].partition(" ")
    assert scheme == "Basic"
    assert base64.b64decode(encoded).decode("utf-8") == (
        f"{ZULIP_ENV['GPFWA_ZULIP_BOT_EMAIL']}:"
        f"{ZULIP_ENV['GPFWA_ZULIP_API_KEY']}"
    )


def test_an_error_without_an_exception_posts_nothing(
    zulip_server: _CaptureServer,
    live_zulip_handler: ZulipHandler,
) -> None:
    logging.getLogger("web_annotation.jobs.views").error("job failed")

    assert zulip_server.posts == []
