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
