"""Read the chunks of a PNG file, for the tests of the statistics images.

The reader is independent of the code under test: it walks the chunk
layout of the PNG specification and nothing else, so a test that asks
which chunks a written image holds does not ask the helper it checks.
"""
from __future__ import annotations

import struct

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def png_chunks(data: bytes) -> list[bytes]:
    """Every chunk of ``data``, each with its length, type, body and CRC."""
    assert data.startswith(PNG_SIGNATURE), "not a PNG file"
    chunks = []
    offset = len(PNG_SIGNATURE)
    while offset < len(data):
        (length,) = struct.unpack(">I", data[offset:offset + 4])
        end = offset + 12 + length
        chunks.append(data[offset:end])
        offset = end
    return chunks


def software_chunks(data: bytes) -> list[bytes]:
    """The ``tEXt`` chunks of ``data`` whose keyword is ``Software``."""
    return [
        chunk for chunk in png_chunks(data)
        if chunk[4:8] == b"tEXt"
        and chunk[8:].startswith(b"Software\x00")
    ]
