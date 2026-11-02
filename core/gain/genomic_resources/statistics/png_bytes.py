"""Make the bytes of a statistics image depend only on its pixels.

Matplotlib writes a ``tEXt`` chunk ``Software`` with its own version into
every PNG it saves.  Two hosts with different matplotlib versions then
write different bytes from the same pixels, and the md5 of the image
changes in ``.MANIFEST`` for no real change (gain#2048).

The chunk is removed after the image is drawn, not kept out with
``savefig(metadata=...)``: a resource ``plot_function`` calls ``savefig``
itself, and gain does not control that code.

A leaf that imports nothing from ``gain``, as ``chart_style`` is: both
``histogram`` and this package write images, and the statistics package
does not import ``histogram``.
"""
from __future__ import annotations

import io
import struct
from collections.abc import Callable
from typing import BinaryIO

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

_SOFTWARE_TEXT_PREFIX = b"Software\x00"


def without_software_chunk(data: bytes) -> bytes:
    """Return ``data`` without its ``tEXt`` chunks keyed ``Software``.

    Every other chunk is copied byte for byte, so the image data and each
    CRC stay as they were.  Bytes that are not a PNG come back unchanged:
    a ``plot_function`` can write anything.
    """
    if not data.startswith(PNG_SIGNATURE):
        return data
    kept = [PNG_SIGNATURE]
    offset = len(PNG_SIGNATURE)
    while offset + 8 <= len(data):
        (length,) = struct.unpack(">I", data[offset:offset + 4])
        end = offset + 12 + length
        chunk = data[offset:end]
        if not (chunk[4:8] == b"tEXt"
                and chunk[8:].startswith(_SOFTWARE_TEXT_PREFIX)):
            kept.append(chunk)
        offset = end
    kept.append(data[offset:])
    return b"".join(kept)


def rendered_png(draw: Callable[[BinaryIO], object]) -> bytes:
    """Let ``draw`` write an image into memory, and return its bytes.

    The bytes come back without the ``Software`` chunk, ready for the
    caller to write into the resource.
    """
    buffer = io.BytesIO()
    draw(buffer)
    return without_software_chunk(buffer.getvalue())
