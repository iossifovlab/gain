"""Post log records to a Zulip stream.

``ZulipHandler`` is a ``logging.Handler`` that posts each record it
accepts as one message to a Zulip stream, through the Zulip REST API
(``POST <site>/api/v1/messages``). It uses only the standard library.

The message content is one bold title line, then the formatted record,
traceback included, in a four-backtick fence::

    **<levelname> <topic> <logger name>: <first line of the message>**

    ````
    <formatted record>
    ````

The handler strips url credentials from the whole content, the
formatted traceback included.
"""
from __future__ import annotations

import base64
import time
import urllib.request
from collections.abc import Callable
from urllib.parse import urlencode

from gain import logging
from gain.utils.url_redaction import strip_url_credentials

_TIMEOUT_SECONDS = 5
_FENCE = "````"

ZULIP_LOGGER_NAME = "gain.zulip"
_DJANGO_SECURITY = "django.security"


def zulip_logger() -> logging.GainLogger:
    """Return the logger whose records always pass ``ZulipFilter``."""
    return logging.getLogger(ZULIP_LOGGER_NAME)


def _is_or_descends_from(name: str, ancestor: str) -> bool:
    return name == ancestor or name.startswith(f"{ancestor}.")


class ZulipFilter(logging.Filter):
    """Pass the records that a Zulip stream receives.

    A record passes when one of these holds:

    - It carries an active exception at ``ERROR`` or above, and its
      logger is not ``django.security`` or a child of it. A record logged
      with ``exc_info`` outside an ``except`` block carries no exception.
    - Its logger is ``ZULIP_LOGGER_NAME`` or a child of it.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if _is_or_descends_from(record.name, ZULIP_LOGGER_NAME):
            return True
        return (
            record.exc_info is not None
            and record.exc_info[0] is not None
            and record.levelno >= logging.ERROR
            and not _is_or_descends_from(record.name, _DJANGO_SECURITY)
        )


class ZulipHandler(logging.Handler):
    """Post log records as messages to a Zulip stream.

    The handler carries a ``ZulipFilter``, which decides what it posts.
    When the post fails, the handler calls ``handleError`` and the
    logging call does not raise.

    Parameters
    ----------
    site
        The Zulip server origin, for example ``https://zulip.example``.
    bot_email
        The email address of the Zulip bot that posts the messages.
    api_key
        The API key of the bot.
    stream
        The stream that receives the messages.
    topic
        The default topic of the messages. A record that carries a
        ``zulip_topic`` attribute, set through ``extra=``, posts to that
        topic instead.
    max_posts
        The number of records that the handler posts in one window. The
        handler counts the other records of the window as suppressed.
    window_seconds
        The length of a window. A window is fixed and starts at the first
        post after the previous window ends. On the first record after a
        window with suppressed records, the handler posts one summary
        before the record. The summary does not count against the new
        window.
    clock
        The monotonic time source of the window, in seconds.
    max_chars
        The maximum length of the message content. The Zulip default is
        10,000. The handler cuts a longer formatted record from the
        front and puts the line ``... (cut <N> characters)`` first in
        the fence. The title stays. The end of the record stays, so the
        last line stays whole only when it fits: the handler cuts a
        longer last line from its front too. The title takes at most a
        quarter of the limit: the handler cuts a longer title from the
        end and ends it with ``...``. A limit under 16 characters cannot
        hold the title markup and the fences.
    """

    def __init__(
        self,
        site: str,
        bot_email: str,
        api_key: str,
        stream: str,
        topic: str,
        *,
        max_posts: int = 10,
        window_seconds: int = 600,
        clock: Callable[[], float] = time.monotonic,
        max_chars: int = 10_000,
    ) -> None:
        super().__init__()
        self.addFilter(ZulipFilter())
        self.url = f"{site.rstrip('/')}/api/v1/messages"
        self.stream = stream
        self.topic = topic
        credentials = f"{bot_email}:{api_key}".encode()
        self._authorization = \
            f"Basic {base64.b64encode(credentials).decode('ascii')}"
        self.max_posts = max_posts
        self.window_seconds = window_seconds
        self._clock = clock
        self.max_chars = max_chars
        self._window_start: float | None = None
        self._posts_in_window = 0
        self._suppressed = 0

    def _content(self, record: logging.LogRecord, topic: str) -> str:
        first_line = (record.getMessage().splitlines() or [""])[0]
        heading = strip_url_credentials(
            f"{record.levelname} {topic} {record.name}: {first_line}")
        heading = self._clip(heading, self.max_chars // 4 - len("****"))
        body = strip_url_credentials(self.format(record))
        head = f"**{heading}**\n\n{_FENCE}\n"
        tail = f"\n{_FENCE}"
        budget = self.max_chars - len(head) - len(tail)
        return f"{head}{self._cut(body, budget)}{tail}"

    @staticmethod
    def _clip(text: str, limit: int) -> str:
        """Cut ``text`` from the end to at most ``limit`` characters.

        A clipped text ends with ``...``.
        """
        limit = max(limit, 0)
        if len(text) <= limit:
            return text
        if limit < len("..."):
            return text[:limit]
        return f"{text[:limit - len('...')]}..."

    @staticmethod
    def _cut(body: str, budget: int) -> str:
        """Cut ``body`` from the front to at most ``budget`` characters.

        A cut body starts with the line ``... (cut <N> characters)``. When
        the budget cannot hold that line, the body keeps only its last
        ``budget`` characters.
        """
        budget = max(budget, 0)
        if len(body) <= budget:
            return body
        cut = len(body) - budget
        while True:
            marker = f"... (cut {cut} characters)\n"
            if len(marker) > budget:
                return body[len(body) - budget:]
            keep = budget - len(marker)
            if len(body) - keep == cut:
                return f"{marker}{body[len(body) - keep:]}"
            cut = len(body) - keep

    def _post(self, topic: str, content: str) -> None:
        data = urlencode({
            "type": "stream",
            "to": self.stream,
            "topic": topic,
            "content": content,
        }).encode("utf-8")
        # S310 guards against a ``file:`` or custom scheme in a URL from
        # untrusted input. ``self.url`` comes from the ``site`` that the
        # deployment configures, never from a log record.
        request = urllib.request.Request(  # ruff: ignore[suspicious-url-open-usage]
            self.url,
            data=data,
            headers={
                "Authorization": self._authorization,
                "Content-Type": "application/x-www-form-urlencoded",
            },
            method="POST",
        )
        # S310: the same configured URL as the ``Request`` above.
        with urllib.request.urlopen(  # ruff: ignore[suspicious-url-open-usage]
                request, timeout=_TIMEOUT_SECONDS):
            pass

    def _admit(self) -> int | None:
        """Admit a record into the current window, or start a new window.

        Return ``None`` when the current window is full: the record is
        suppressed. Otherwise return the suppressed count of the window
        that ends here, or zero when no window ends.
        """
        now = self._clock()
        if (self._window_start is None
                or now - self._window_start >= self.window_seconds):
            ended_suppressed = self._suppressed
            self._window_start = now
            self._posts_in_window = 1
            self._suppressed = 0
            return ended_suppressed
        if self._posts_in_window >= self.max_posts:
            self._suppressed += 1
            return None
        self._posts_in_window += 1
        return 0

    def _post_summary(
        self, suppressed: int, record: logging.LogRecord,
    ) -> None:
        """Post the summary of an ended window.

        When the post fails, report the failure through ``handleError``
        and carry the count into the current window, so that the next
        summary includes it. The record that ended the window still posts.
        """
        minutes = self.window_seconds // 60
        try:
            self._post(
                self.topic,
                f"**{self.topic}: suppressed {suppressed} messages "
                f"in the last {minutes} min**")
        except Exception:  # ruff: ignore[blind-except] -- see emit
            self._suppressed += suppressed
            self.handleError(record)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            # ``Handler.handle`` holds ``self.lock`` around ``emit``, so
            # the window state needs no lock of its own.
            suppressed = self._admit()
            if suppressed is None:
                return
            if suppressed > 0:
                self._post_summary(suppressed, record)
            topic = str(getattr(record, "zulip_topic", self.topic))
            self._post(topic, self._content(record, topic))
        # A logging handler must never raise into the caller. The
        # ``logging.Handler`` contract is to catch every error in ``emit``
        # and report it through ``handleError``.
        except Exception:  # ruff: ignore[blind-except]
            self.handleError(record)
