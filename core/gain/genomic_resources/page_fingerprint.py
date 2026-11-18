"""The stored form of the page fingerprint inside an info page.

The page step of ``grr_manage`` puts the page fingerprint of a resource
into both of its info pages, as the tag
``<meta name="gain-page-fingerprint" content="<digest>">`` right after
``<meta charset="utf-8">``. A repair reads the tag back from the head of
``index.html`` and skips the resource when the stored page fingerprint
equals the current one.
"""
from __future__ import annotations

import html
import re

#: The ``name`` of the meta tag that holds the page fingerprint.
PAGE_FINGERPRINT_META_NAME = "gain-page-fingerprint"

#: How much of the head of an info page the reader looks at.
PAGE_FINGERPRINT_READ_SIZE = 4096

_CHARSET_TAG = re.compile(r'^([ \t]*)<meta charset="utf-8">', re.MULTILINE)

_FINGERPRINT_TAG = re.compile(
    rf'<meta name="{PAGE_FINGERPRINT_META_NAME}" content="([^"]*)">')


def insert_page_fingerprint(page: str, fingerprint: str) -> str:
    """Return ``page`` with the page fingerprint tag inserted.

    The tag goes on its own line right after the first
    ``<meta charset="utf-8">``, at the same indentation. A page without
    that tag comes back unchanged, so a repair renders it on every run.
    """
    tag = (
        f'<meta name="{PAGE_FINGERPRINT_META_NAME}" '
        f'content="{html.escape(fingerprint)}">')
    return _CHARSET_TAG.sub(
        lambda match: f"{match.group(0)}\n{match.group(1)}{tag}",
        page, count=1)


def read_page_fingerprint(head: str) -> str | None:
    """Return the page fingerprint stored in the head of an info page.

    Only the first ``PAGE_FINGERPRINT_READ_SIZE`` characters of ``head``
    count. ``None`` means that no page fingerprint tag is there.
    """
    match = _FINGERPRINT_TAG.search(head[:PAGE_FINGERPRINT_READ_SIZE])
    if match is None:
        return None
    return html.unescape(match.group(1))
