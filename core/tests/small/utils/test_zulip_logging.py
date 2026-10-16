# pylint: disable=W0621,C0114,C0116
# The gain test header: pytest fixtures shadow their names (W0621), and
# test modules and functions carry no docstrings (C0114, C0116).
"""``ZulipHandler`` posts log records to a Zulip stream (gain#2002).

The tests observe the handler the only way Zulip would: a local
``http.server`` stands in for ``<site>/api/v1/messages`` and records
each POST.  No mock replaces the HTTP call.
"""
from __future__ import annotations

import base64
import socket
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs

import pytest
from gain import logging
from gain.utils.zulip_logging import ZulipHandler, zulip_logger

BOT_EMAIL = "gain-bot@zulip.example"
API_KEY = "s3cr3t-api-key"
STREAM = "gain-alerts"
TOPIC = "gain-test"


@dataclass(frozen=True)
class Post:
    path: str
    authorization: str
    form: dict[str, str]


class _CaptureHandler(BaseHTTPRequestHandler):
    # ``http.server`` dispatches a POST to the method named ``do_POST``.
    def do_POST(self) -> None:  # pylint: disable=invalid-name
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8")
        # The stub types ``self.server`` as ``BaseServer``, but this handler
        # only ever serves a ``_CaptureServer``.
        server: _CaptureServer = self.server  # type: ignore[assignment]
        post = Post(
            path=self.path,
            authorization=self.headers.get("Authorization", ""),
            form={k: v[0] for k, v in parse_qs(raw).items()},
        )
        server.posts.append(post)
        failing = (server.failing_prefix is not None
                   and post.form.get("content", "").startswith(
                       server.failing_prefix))
        self.send_response(500 if failing else server.status)
        self.send_header("Content-Length", "0")
        self.end_headers()


def _raise_value_error(message: str) -> None:
    raise ValueError(message)


class _CaptureServer(HTTPServer):
    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _CaptureHandler)
        self.posts: list[Post] = []
        self.status = 200
        # A post whose content starts with this prefix gets a 500.
        self.failing_prefix: str | None = None

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


class _CountingZulipHandler(ZulipHandler):
    """Count the ``handleError`` calls instead of printing to stderr.

    The handler posts one record in each window of the given ``clock``.
    """

    def __init__(
        self, site: str, clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(
            site, BOT_EMAIL, API_KEY, STREAM, TOPIC,
            max_posts=1, window_seconds=600, clock=clock)
        self.errors: list[logging.LogRecord] = []

    def handleError(self, record: logging.LogRecord) -> None:
        self.errors.append(record)


def _refused_site() -> str:
    """Return the origin of a localhost port that nothing listens on."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    return f"http://127.0.0.1:{port}"


def _log_exception_through(handler: ZulipHandler) -> None:
    logger = logging.getLogger("gain_test.zulip_logging.failure")
    logger.propagate = False
    logger.addHandler(handler)
    try:
        try:
            _raise_value_error("kaput")
        except ValueError:
            logger.exception("boom")
    finally:
        logger.removeHandler(handler)


def _handler(site: str) -> ZulipHandler:
    return ZulipHandler(site, BOT_EMAIL, API_KEY, STREAM, TOPIC)


@pytest.fixture
def test_logger(
    zulip_server: _CaptureServer,
) -> Iterator[logging.Logger]:
    logger = logging.getLogger("gain_test.zulip_logging")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    handler = _handler(zulip_server.site)
    logger.addHandler(handler)
    yield logger
    logger.removeHandler(handler)


@pytest.fixture
def gain_root_handler(
    zulip_server: _CaptureServer,
) -> Iterator[ZulipHandler]:
    """Attach the handler to ``gain``, the parent of every gain logger."""
    gain_logger = logging.getLogger("gain")
    saved_level = gain_logger.level
    gain_logger.setLevel(logging.DEBUG)
    handler = _handler(zulip_server.site)
    gain_logger.addHandler(handler)
    yield handler
    gain_logger.removeHandler(handler)
    gain_logger.setLevel(saved_level)


def test_exception_posts_the_traceback_to_the_stream(
    zulip_server: _CaptureServer, test_logger: logging.Logger,
) -> None:
    try:
        _raise_value_error("kaput")
    except ValueError:
        test_logger.exception("boom")

    assert len(zulip_server.posts) == 1
    post = zulip_server.posts[0]
    expected_auth = base64.b64encode(
        f"{BOT_EMAIL}:{API_KEY}".encode()).decode()
    assert post.path == "/api/v1/messages"
    assert post.authorization == f"Basic {expected_auth}"
    assert post.form["type"] == "stream"
    assert post.form["to"] == STREAM
    assert post.form["topic"] == TOPIC
    content = post.form["content"]
    assert content.startswith(
        f"**ERROR {TOPIC} gain_test.zulip_logging: boom**\n\n````\n")
    assert "boom" in content
    assert "Traceback (most recent call last):" in content
    assert "ValueError: kaput" in content
    assert content.endswith("\n````")


def test_error_without_exc_info_posts_nothing(
    zulip_server: _CaptureServer, test_logger: logging.Logger,
) -> None:
    test_logger.error("x")

    assert zulip_server.posts == []


def test_warning_with_exc_info_posts_nothing(
    zulip_server: _CaptureServer, test_logger: logging.Logger,
) -> None:
    try:
        _raise_value_error("kaput")
    except ValueError:
        test_logger.warning("x", exc_info=True)

    assert zulip_server.posts == []


@pytest.mark.usefixtures("gain_root_handler")
def test_zulip_logger_info_posts(zulip_server: _CaptureServer) -> None:
    zulip_logger().info("re-index done")

    assert len(zulip_server.posts) == 1
    content = zulip_server.posts[0].form["content"]
    assert content.startswith(f"**INFO {TOPIC} gain.zulip: re-index done**")


@pytest.mark.usefixtures("gain_root_handler")
def test_other_gain_logger_info_posts_nothing(
    zulip_server: _CaptureServer,
) -> None:
    logging.getLogger("gain.other").info("x")

    assert zulip_server.posts == []


def test_django_security_exception_posts_nothing(
    zulip_server: _CaptureServer,
) -> None:
    logger = logging.getLogger("django.security.DisallowedHost")
    handler = _handler(zulip_server.site)
    logger.addHandler(handler)
    try:
        try:
            _raise_value_error("bad host")
        except ValueError:
            logger.exception("Invalid HTTP_HOST header")
    finally:
        logger.removeHandler(handler)

    assert zulip_server.posts == []


@pytest.mark.usefixtures("gain_root_handler")
def test_zulip_topic_extra_overrides_the_handler_topic(
    zulip_server: _CaptureServer,
) -> None:
    zulip_logger().info("x", extra={"zulip_topic": "re-index"})

    assert [p.form["topic"] for p in zulip_server.posts] == ["re-index"]
    assert zulip_server.posts[0].form["content"].startswith(
        "**INFO re-index gain.zulip: x**")


@pytest.mark.usefixtures("gain_root_handler")
def test_message_without_extra_posts_with_the_handler_topic(
    zulip_server: _CaptureServer,
) -> None:
    zulip_logger().info("x")

    assert [p.form["topic"] for p in zulip_server.posts] == [TOPIC]


def test_server_error_calls_handle_error_once(
    zulip_server: _CaptureServer,
) -> None:
    zulip_server.status = 500
    handler = _CountingZulipHandler(zulip_server.site)

    _log_exception_through(handler)

    assert len(zulip_server.posts) == 1
    assert [r.getMessage() for r in handler.errors] == ["boom"]


def test_refused_connection_calls_handle_error_once() -> None:
    handler = _CountingZulipHandler(_refused_site())

    _log_exception_through(handler)

    assert [r.getMessage() for r in handler.errors] == ["boom"]


def test_exception_outside_an_except_block_posts_nothing(
    zulip_server: _CaptureServer, test_logger: logging.Logger,
) -> None:
    # The test logs with ``exc_info`` and no active exception on purpose:
    # the record then carries ``(None, None, None)``.
    test_logger.exception(  # ruff: ignore[log-exception-outside-except-handler]
        "no active exception")
    test_logger.error(
        "no active exception",
        exc_info=True,  # ruff: ignore[exc-info-outside-except-handler]
    )

    assert zulip_server.posts == []


@pytest.mark.usefixtures("gain_root_handler")
def test_zulip_logger_child_info_posts(zulip_server: _CaptureServer) -> None:
    logging.getLogger("gain.zulip.reindex").info("x")

    assert len(zulip_server.posts) == 1
    assert zulip_server.posts[0].form["content"].startswith(
        f"**INFO {TOPIC} gain.zulip.reindex: x**")


@pytest.mark.usefixtures("gain_root_handler")
def test_zulip_lookalike_logger_info_posts_nothing(
    zulip_server: _CaptureServer,
) -> None:
    logging.getLogger("gain.zulipx").info("x")

    assert zulip_server.posts == []


def test_django_security_lookalike_exception_posts(
    zulip_server: _CaptureServer,
) -> None:
    logger = logging.getLogger("django.security_extra")
    handler = _handler(zulip_server.site)
    logger.addHandler(handler)
    try:
        try:
            _raise_value_error("kaput")
        except ValueError:
            logger.exception("boom")
    finally:
        logger.removeHandler(handler)

    assert len(zulip_server.posts) == 1
    assert zulip_server.posts[0].form["content"].startswith(
        f"**ERROR {TOPIC} django.security_extra: boom**")


def test_critical_with_exc_info_posts(
    zulip_server: _CaptureServer, test_logger: logging.Logger,
) -> None:
    try:
        _raise_value_error("kaput")
    except ValueError:
        test_logger.critical("boom", exc_info=True)

    assert len(zulip_server.posts) == 1
    assert zulip_server.posts[0].form["content"].startswith(
        f"**CRITICAL {TOPIC} gain_test.zulip_logging: boom**\n\n````\n")


@pytest.mark.usefixtures("gain_root_handler")
def test_multi_line_message_puts_only_its_first_line_in_the_title(
    zulip_server: _CaptureServer,
) -> None:
    zulip_logger().info("first line\nsecond line")

    content = zulip_server.posts[0].form["content"]
    title, body = content.split("\n\n", 1)
    assert title == f"**INFO {TOPIC} gain.zulip: first line**"
    assert body == "````\nfirst line\nsecond line\n````"


class _FakeClock:
    """A monotonic clock that moves only when the test advances it."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock() -> _FakeClock:
    return _FakeClock()


@pytest.fixture
def throttled_logger(
    zulip_server: _CaptureServer, clock: _FakeClock,
) -> Iterator[logging.Logger]:
    logger = logging.getLogger("gain.zulip.throttle_test")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    handler = ZulipHandler(
        zulip_server.site, BOT_EMAIL, API_KEY, STREAM, TOPIC,
        max_posts=10, window_seconds=600, clock=clock)
    logger.addHandler(handler)
    yield logger
    logger.removeHandler(handler)


def _titles(server: _CaptureServer) -> list[str]:
    return [p.form["content"].split("\n", 1)[0] for p in server.posts]


def _record_title(message: str) -> str:
    return f"**INFO {TOPIC} gain.zulip.throttle_test: {message}**"


def test_flood_posts_max_posts_then_one_summary_after_the_window(
    zulip_server: _CaptureServer,
    throttled_logger: logging.Logger,
    clock: _FakeClock,
) -> None:
    for i in range(25):
        throttled_logger.info("record %d", i)
    clock.advance(600)
    throttled_logger.info("after the window")

    expected = [_record_title(f"record {i}") for i in range(10)]
    expected += [
        f"**{TOPIC}: suppressed 15 messages in the last 10 min**",
        _record_title("after the window"),
    ]
    assert _titles(zulip_server) == expected
    assert len(zulip_server.posts) == 12


def test_window_without_suppressed_records_posts_no_summary(
    zulip_server: _CaptureServer,
    throttled_logger: logging.Logger,
    clock: _FakeClock,
) -> None:
    for i in range(9):
        throttled_logger.info("record %d", i)
    clock.advance(600)
    throttled_logger.info("after the window")

    expected = [_record_title(f"record {i}") for i in range(9)]
    expected.append(_record_title("after the window"))
    assert _titles(zulip_server) == expected


def test_window_after_a_summary_allows_max_posts_again(
    zulip_server: _CaptureServer,
    throttled_logger: logging.Logger,
    clock: _FakeClock,
) -> None:
    for i in range(11):
        throttled_logger.info("first %d", i)
    clock.advance(600)
    for i in range(11):
        throttled_logger.info("second %d", i)
    clock.advance(600)
    throttled_logger.info("third")

    second = [_record_title(f"second {i}") for i in range(10)]
    summary = f"**{TOPIC}: suppressed 1 messages in the last 10 min**"
    assert _titles(zulip_server)[10:] == [
        summary,
        *second,
        summary,
        _record_title("third"),
    ]


def test_refused_post_counts_against_the_window(clock: _FakeClock) -> None:
    handler = _CountingZulipHandler(_refused_site(), clock=clock)

    _log_exception_through(handler)
    _log_exception_through(handler)

    assert [r.getMessage() for r in handler.errors] == ["boom"]


_SUMMARY_PREFIX = f"**{TOPIC}: suppressed"


def test_failed_summary_still_posts_the_record(
    zulip_server: _CaptureServer,
    throttled_logger: logging.Logger,
    clock: _FakeClock,
) -> None:
    for i in range(11):
        throttled_logger.info("record %d", i)
    zulip_server.failing_prefix = _SUMMARY_PREFIX
    clock.advance(600)

    throttled_logger.info("after the window")

    assert _titles(zulip_server)[10:] == [
        f"**{TOPIC}: suppressed 1 messages in the last 10 min**",
        _record_title("after the window"),
    ]


def test_failed_summary_keeps_its_count_for_the_next_summary(
    zulip_server: _CaptureServer,
    throttled_logger: logging.Logger,
    clock: _FakeClock,
) -> None:
    for i in range(11):
        throttled_logger.info("first %d", i)
    zulip_server.failing_prefix = _SUMMARY_PREFIX
    clock.advance(600)
    throttled_logger.info("second")
    zulip_server.failing_prefix = None
    clock.advance(600)

    throttled_logger.info("third")

    summary = f"**{TOPIC}: suppressed 1 messages in the last 10 min**"
    assert _titles(zulip_server)[10:] == [
        summary,
        _record_title("second"),
        summary,
        _record_title("third"),
    ]
