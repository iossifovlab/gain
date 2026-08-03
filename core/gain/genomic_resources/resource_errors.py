"""How a resource is refused, and why the refusal is a `ValueError`.

`MalformedResourceError` places the blame on the *resource*: its records or
its configuration do not hold to what its kind can mean. It subclasses
`ValueError` so it falls inside `cli_errors.RESOURCE_ERRORS` by construction,
which is what makes `grr_manage` report it as one attributed line rather than
as an unexpected internal error carrying a traceback (ADR 0008).

The module is a leaf -- it imports nothing from GAIn -- so the score layer,
the table layer and the CLI tier can raise and catch the same exception
without any of them acquiring a dependency on another.
"""
from __future__ import annotations

from collections.abc import Sequence


class MalformedResourceError(ValueError):
    """A resource refused because of its own records or configuration.

    Named for the state the resource is in rather than for any one rule, so
    that every refusal a reader could act on the same way -- by fixing the
    resource -- arrives under one name.
    """


def overlapping_records_error(
    resource_id: str, chrom: str, pos: int, prev_end: int,
) -> MalformedResourceError:
    """Refuse a position score whose record claims a position already taken.

    Built here rather than at either raise site because two paths detect this
    one rule -- the per-record region read and the vectorized statistics scan
    -- and a reader who meets the message from one of them must not have to
    wonder whether the other words it differently.
    """
    return MalformedResourceError(
        f"<{resource_id}> is malformed: multiple values for positions "
        f"{chrom}:{pos}, which overlaps the preceding record ending at "
        f"{chrom}:{prev_end}; a position score allows at most one record "
        f"per position")


def index_column_mismatch_error(
    resource_id: str,
    index_filename: str,
    mismatches: Sequence[tuple[str, int, int]],
    *,
    end_is_implied: bool = False,
) -> MalformedResourceError:
    """Refuse a tabix table whose index was built over other columns.

    Each entry of ``mismatches`` is a field, the column its index was built
    over, and the column its table *resolves* to -- resolves, not states: a
    coordinate no configuration and no header names is read through a
    hardcoded fallback, and a fallback the index disagrees with splits the
    read from the filter exactly as a contradictory config entry does.  Both
    columns are named because the remedy is a one-line edit and a reader
    cannot write it from either number alone.  ``end_is_implied`` says the
    index records no end column at all, so its end column is its begin column
    by definition rather than by anyone's choice.

    Built here rather than at the raise site for the same reason
    :func:`overlapping_records_error` is: the rule is one rule, and it must
    read identically wherever it is reported.
    """
    fields = "; ".join(
        f"{field} is indexed on column {indexed} but the configuration "
        f"resolves it to column {configured}"
        for field, indexed, configured in mismatches
    )
    implied_note = (
        " The index records no end column, so every record ends where it "
        "begins as far as the index is concerned."
    ) if end_is_implied else ""
    return MalformedResourceError(
        f"<{resource_id}> is malformed: its table is configured over columns "
        f"its index {index_filename} was not built from: {fields}."
        f"{implied_note} A region query is filtered by the indexed columns "
        f"and read through the configured ones, so a record the index returns "
        f"is dropped without a trace, and a record the configured span "
        f"reaches over is never fetched at all. Rebuild the index over the "
        f"configured columns, or configure the table with the indexed ones.")
