# pylint: disable=C0114,C0116
import io
import struct

import matplotlib.image as mpimg
from gain.genomic_resources.statistics.png_bytes import (
    without_software_chunk,
)
from matplotlib.figure import Figure

from tests.small.genomic_resources.png_chunks import (
    PNG_SIGNATURE,
    png_chunks,
    software_chunks,
)


def _plain_savefig_png() -> bytes:
    figure = Figure(figsize=(2, 2))
    figure.subplots().plot([0, 1, 2], [2, 0, 1])
    buffer = io.BytesIO()
    figure.savefig(buffer, format="png")
    return buffer.getvalue()


def test_removes_the_software_chunk_and_keeps_every_other_chunk() -> None:
    original = _plain_savefig_png()
    assert len(software_chunks(original)) == 1
    expected = [
        chunk for chunk in png_chunks(original)
        if chunk not in software_chunks(original)
    ]
    assert b"IDAT" in [chunk[4:8] for chunk in expected]

    stripped = without_software_chunk(original)

    assert stripped.startswith(PNG_SIGNATURE)
    assert png_chunks(stripped) == expected
    assert mpimg.imread(io.BytesIO(stripped), format="png").shape[:2] == \
        mpimg.imread(io.BytesIO(original), format="png").shape[:2]


def test_returns_a_png_without_a_software_chunk_unchanged() -> None:
    already_clean = without_software_chunk(_plain_savefig_png())
    assert software_chunks(already_clean) == []

    assert without_software_chunk(already_clean) == already_clean


def test_returns_bytes_that_are_not_a_png_unchanged() -> None:
    # Eight bytes that are not the signature, then a well-formed Software
    # chunk: the helper would cut the chunk out if it parsed past the
    # first eight bytes without checking them.
    software = b"Software\x00Matplotlib"
    not_a_png = (
        b"NOT A PNG"[:8]
        + struct.pack(">I", len(software)) + b"tEXt" + software
        + b"\x00\x00\x00\x00")

    assert without_software_chunk(not_a_png) == not_a_png
