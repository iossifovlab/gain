"""Url userinfo redaction, and the log-record seam that applies it.

Two things live here, and they live *here* -- below the GRR, next to
``log_levels`` -- because ``gain/__init__`` has to import them before any
module that logs, and the GRR is one of those modules.

``strip_url_userinfo`` is the redactor the GRR has always used for display
urls and hand-redacted messages (ADR 0023).  ``redact_url_userinfo_in_log_
records`` is the fourth remedy that ADR's gain#1363 amendment records: it
wraps ``logging.LogRecord.getMessage`` process-wide, so a credential that
reaches *any* log line -- gain's own or fsspec's -- is stripped when the
record is formatted, and the emitting site needs to know nothing about the
rule.  Like ``log_levels``, this module installs its patch as an import
side effect, so importing it is the whole of what a bootstrap has to do.
"""
from __future__ import annotations

import logging
import re

# Matches the ``scheme://user:pass@`` prefix of any url embedded in a string.
# The userinfo (``[^/@\s]+``) carries the secret and is dropped, keeping the
# scheme and everything from the host onward. Works both on a bare url and on a
# longer diagnostic message that embeds one (e.g. an fsspec
# ``FileNotFoundError`` whose text IS the credential-bearing fetch url).
_URL_USERINFO_RE = re.compile(
    r"(?P<scheme>[a-zA-Z][a-zA-Z0-9+.\-]*://)[^/@\s]+@")


def strip_url_userinfo(text: str) -> str:
    """Strip ``user:pass@`` userinfo from every ``scheme://user:pass@host``.

    Used to build credential-free display urls, cache-hit log lines and
    redacted fetch-error messages. The host/port/path are preserved; only the
    userinfo is removed. A string with no userinfo is returned unchanged.

    The ``@`` test up front is what keeps the log-record seam below cheap:
    it runs on every formatted log line in the process, almost none of
    which carry an ``@``, and the regex re-anchors at every letter of a
    line that has none -- a few microseconds a line, against a few
    nanoseconds for the test.
    """
    if "@" not in text:
        return text
    return _URL_USERINFO_RE.sub(lambda match: match.group("scheme"), text)


#: Set on the ``getMessage`` this module installs, and nowhere else. It is
#: what makes a second install a no-op: the marker lives on the function in
#: the ``LogRecord`` slot, not in this module's globals, so it survives an
#: ``importlib.reload`` that would reset a module-level flag and stack a
#: second redaction on top of the first.
_REDACTS_URL_USERINFO = "__gain_redacts_url_userinfo__"


def redact_url_userinfo_in_log_records() -> None:
    """Make every ``LogRecord`` render its message with userinfo stripped.

    Wraps ``logging.LogRecord.getMessage`` -- the one method every stdlib
    and third-party ``Formatter`` asks for the message text -- so the
    redaction runs exactly when a handler formats the record and never at
    emission. Idempotent: installing over an installed seam changes
    nothing.
    """
    unredacted = logging.LogRecord.getMessage
    if getattr(unredacted, _REDACTS_URL_USERINFO, False):
        return

    def get_message(self: logging.LogRecord) -> str:
        return strip_url_userinfo(unredacted(self))

    setattr(get_message, _REDACTS_URL_USERINFO, True)
    logging.LogRecord.getMessage = get_message  # type: ignore[method-assign]


redact_url_userinfo_in_log_records()
