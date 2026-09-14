"""Url credential redaction, and the log-record seam that applies it.

The redactors live *here* -- below the GRR, next to ``log_levels`` --
because ``gain/__init__`` has to import the seam before any module that
logs, and the GRR is one of those modules.

Two redactors, and one rule for choosing between them (ADR 0023):
**a display url takes ``strip_url_userinfo``; a message takes
``strip_url_credentials``.**  A display url -- a protocol's public url, a
cache-hit log line's path -- keeps its query string, which on a stored url
is part of the address; a message -- an exception's text, whatever url a
library embedded in it -- loses userinfo and query both, because a
presigned url carries its signature in the query.  An architecture test
holds the line.

``redact_url_userinfo_in_log_records`` is the fourth remedy that ADR's
gain#1363 amendment records: it wraps ``logging.LogRecord.getMessage``
process-wide, so a credential that reaches *any* log line -- gain's own or
fsspec's -- is stripped when the record is formatted, and the emitting site
needs to know nothing about the rule.  Like ``log_levels``, this module
installs its patch as an import side effect, so importing it is the whole
of what a bootstrap has to do.
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

    The host/port/path and any query string are preserved; only the
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


# Matches the ``?query`` of any url embedded in a string, keeping the url up
# to the ``?``. The credential of a PRESIGNED url lives there (gain#1339).
#
# The url body and the query both stop at whitespace OR at one of the
# delimiters a library actually wraps a url in, because the whole point is to
# act on a url embedded in a longer diagnostic message: pysam writes
# ``file `<url>` `` and htslib's stderr writes ``file "<url>"``. Stopping at
# whitespace alone would swallow those closing delimiters.
#
# The set is those two plus ``'`` and ``>``; it is deliberately NOT every
# character that could follow a url. A message spelling one as ``URL(<url>)``
# or ``<url>, retrying`` still loses its ``)`` or ``,`` to the query match.
# That over-deletes -- it never leaks -- and adding closers on speculation
# would start eating characters that are legal IN a query string. Only the
# CLOSING half of a bracket pair is needed: a match starts at the scheme, so
# an opening ``<`` is never inside either span.
#
# The scheme repeat is BOUNDED. Unbounded, the engine restarts a scan at
# every alphanumeric position and runs to the end before failing on ``://``,
# which is quadratic in the length of the message: a 6 KB error string -- an
# aiohttp message carrying a response body is that big -- costs ~48 ms per
# substitution against ~0.35 ms bounded, for identical output.
_URL_QUERY_RE = re.compile(
    r"(?P<url>[a-zA-Z][a-zA-Z0-9+.\-]{0,15}://[^\s?`\"'>]*)\?[^\s`\"'>]*")


def _strip_url_query(text: str) -> str:
    """Strip the ``?query`` from every url embedded in ``text``.

    The counterpart of :func:`strip_url_userinfo` for the OTHER place a url
    can carry a secret. An s3 GRR does not hand out its stored url: it hands
    out ``filesystem.sign(url)``, a presigned url that is a bearer credential
    for as long as it lives, and every part of that credential is a query
    parameter.

    The WHOLE query string goes, rather than a list of known-secret parameter
    names -- botocore emits two presigned spellings and a name list written
    from one passes the other through. The host and path, which say *which*
    GRR failed, are kept. ADR 0023's gain#1339 amendment has the argument.

    Private, so that the only way to strip a query is through the union:
    on its own this keeps userinfo, which makes it as narrow as the
    userinfo redactor and wrong for a message in the same way.
    """
    if "?" not in text:
        return text
    return _URL_QUERY_RE.sub(lambda match: match.group("url"), text)


def strip_url_credentials(text: str) -> str:
    """Strip every url credential this module recognises from ``text``.

    The union of the two redactors: a url can carry userinfo AND a
    query-string signature at the same time, and dropping only one of them
    still leaks. Each half returns ``text`` unchanged, at the cost of one
    substring test, when its literal is absent -- the common case, since a
    GRR that is neither url-authed nor s3 carries no credential at all.

    **The order is load-bearing.** Userinfo goes first because a password may
    itself contain ``?``; strip the query first and
    ``https://alice:p?w@host/f.gz`` becomes ``https://alice:p`` -- half the
    password kept and the host, which is what says *which* GRR failed, gone.
    Userinfo-first yields ``https://host/f.gz``.
    """
    return _strip_url_query(strip_url_userinfo(text))


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
