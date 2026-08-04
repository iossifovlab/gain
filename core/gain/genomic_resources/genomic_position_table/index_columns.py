"""The columns a tabix index was built from, read off the index itself.

pysam does not expose them -- a ``pysam.TabixFile`` reports the index's
*filename* and nothing about its contents -- so the header is decoded here,
directly, from the first bytes of the (gzip-compressed) index file.

The layouts are the ones in the SAM/BCF specification.  A ``.tbi`` opens with
a fixed 36-byte header, of which the first 32 bytes are magic, reference
count, format, and the four coordinate fields.  A ``.csi`` opens with magic,
``min_shift``, ``depth`` and an auxiliary-data length; when the index was
written *by tabix* that auxiliary data begins with the same format and
coordinate fields, which is what lets one decoder answer for both flavours.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

# The bytes both layouts fit inside; the caller reads this much and no more,
# so validating an index costs one fixed-size read however large it is.
INDEX_HEADER_SIZE = 64

_TBI_MAGIC = b"TBI\x01"
_CSI_MAGIC = b"CSI\x01"

# magic, n_ref, format, col_seq, col_beg, col_end, meta, skip
_TBI_HEADER = struct.Struct("<4siiiiiii")
# magic, min_shift, depth, l_aux
_CSI_HEADER = struct.Struct("<4siii")
# format, col_seq, col_beg, col_end, meta, skip, l_nm
_CSI_AUX = struct.Struct("<iiiiiii")


@dataclass(frozen=True)
class IndexColumns:
    """The coordinate columns of a tabix index, as table column keys.

    **Zero-based**, unlike the index's own fields, so that they compare
    directly against the column keys a table resolves; the index stores them
    one-based with 0 meaning "absent".

    ``pos_end`` is never ``None``: an index that records no end column
    (``col_end == 0``) treats every record as ending where it begins, so its
    end column *is* its begin column.  ``end_is_implied`` says that is how
    ``pos_end`` came to hold that value, because a reader told only the number
    would not be able to tell it from an index built with the two columns
    deliberately pointed at the same place.
    """

    chrom: int
    pos_begin: int
    pos_end: int
    end_is_implied: bool


def parse_index_columns(header: bytes) -> IndexColumns | None:
    """Decode the coordinate columns out of an index's leading bytes.

    Returns ``None`` when the bytes carry no column configuration to decode:
    an unrecognised magic, a header cut short, or a ``.csi`` written by
    something other than tabix (its auxiliary data is then absent or too short
    to hold the tabix fields).  The caller decides what to do about it --
    which must not be to pass the resource silently.
    """
    if header[:4] == _TBI_MAGIC:
        if len(header) < _TBI_HEADER.size:
            return None
        _, _, _, col_seq, col_beg, col_end, _, _ = _TBI_HEADER.unpack(
            header[:_TBI_HEADER.size])
        return _build_index_columns(col_seq, col_beg, col_end)

    if header[:4] == _CSI_MAGIC:
        if len(header) < _CSI_HEADER.size:
            return None
        _, _, _, l_aux = _CSI_HEADER.unpack(header[:_CSI_HEADER.size])
        aux = header[_CSI_HEADER.size:_CSI_HEADER.size + l_aux]
        if len(aux) < _CSI_AUX.size:
            return None
        _, col_seq, col_beg, col_end, _, _, _ = _CSI_AUX.unpack(
            aux[:_CSI_AUX.size])
        return _build_index_columns(col_seq, col_beg, col_end)

    return None


def _build_index_columns(
    col_seq: int, col_beg: int, col_end: int,
) -> IndexColumns | None:
    """Turn the index's one-based fields into zero-based column keys."""
    if col_seq <= 0 or col_beg <= 0:
        # An index missing either of the two columns every record must have
        # is not one this decoder understands -- a coordinate the index does
        # not carry cannot be compared with the one a table resolves, and
        # inventing a column to compare against would be worse than declining.
        return None
    end_is_implied = col_end <= 0
    pos_end = col_beg if end_is_implied else col_end
    return IndexColumns(
        chrom=col_seq - 1,
        pos_begin=col_beg - 1,
        pos_end=pos_end - 1,
        end_is_implied=end_is_implied,
    )
