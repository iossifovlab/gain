"""Header and score-spec checks shared by the score builders.

The table, VCF INFO and gene score builders parse an authored data header
and check it against their declared scores with these helpers.  The
dependency runs one way: the builder modules import this module, and this
module imports none of them.
"""
from __future__ import annotations

from gain.genomic_resources.testing.score_specs import (
    ResourceValidationError,
    ScoreSpec,
)


def _strip_header(data: str) -> str:
    """Return ``data`` without its header line."""
    lines = data.split("\n")
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        return "\n".join(lines[:index] + lines[index + 1:])
    return data


def _comment_header(data: str) -> str:
    """Return ``data`` with a ``#`` prefixed onto its first header line."""
    lines = data.split("\n")
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = line[:len(line) - len(line.lstrip())]
        lines[index] = f"{indent}#{line.lstrip()}"
        break
    return "\n".join(lines)


def _parse_header(data: str) -> list[str]:
    """Return the column tokens of the first non-empty data line."""
    for line in data.split("\n"):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        return stripped.split()
    return []


def _validate_score_specs(
    scores: tuple[ScoreSpec, ...],
) -> None:
    """Validate the declared scores for duplicate ids or column names.

    A set-based check silently collapses duplicates, so two scores that
    share a ``score_id`` or a ``column_name`` would validate cleanly and
    read the same value.  Reject both explicitly.
    """
    seen_ids: set[str] = set()
    for spec in scores:
        if spec.score_id in seen_ids:
            raise ResourceValidationError(
                f"duplicate score id "
                f"{spec.score_id!r} declared more than once")
        seen_ids.add(spec.score_id)

    seen_columns: set[str] = set()
    for spec in scores:
        if spec.column_name is None:
            continue
        if spec.column_name in seen_columns:
            raise ResourceValidationError(
                f"duplicate column_name "
                f"{spec.column_name!r} shared by more than one score")
        seen_columns.add(spec.column_name)

    seen_indexes: set[int] = set()
    for spec in scores:
        if spec.column_index is None:
            continue
        if spec.column_index in seen_indexes:
            raise ResourceValidationError(
                f"duplicate column_index "
                f"{spec.column_index} shared by more than one score")
        seen_indexes.add(spec.column_index)


def _resolve_column_names(
    scores: tuple[ScoreSpec, ...], header: list[str],
) -> set[str]:
    """Return the data-header column each declared score reads.

    A name-addressed score contributes its ``column_name``; an
    index-addressed one contributes ``header[column_index]``, which also
    range-checks the index against the authored header.  An index pointing
    past the header would otherwise realize cleanly and fail much later,
    deep inside the score, with no mention of the builder.
    """
    resolved: set[str] = set()
    for spec in scores:
        if spec.column_index is None:
            assert spec.column_name is not None
            resolved.add(spec.column_name)
            continue
        if spec.column_index >= len(header):
            raise ResourceValidationError(
                f"score {spec.score_id!r}: column_index "
                f"{spec.column_index} is out of range for a "
                f"{len(header)}-column header {header}")
        resolved.add(header[spec.column_index])
    return resolved


def _validate_data_header(
    data: str, scores: tuple[ScoreSpec, ...], *,
    base_required: tuple[str, ...],
    base_optional: tuple[str, ...] = (),
) -> None:
    """Validate the data header against the declared scores.

    The header must contain the ``base_required`` columns (the position
    columns for a position score, or the gene column for a gene score) plus
    each declared score's ``column_name``.  A missing declared column or an
    undeclared extra column raises ``ResourceValidationError``.  Because the
    builder owns the data format, a conventional ``#``-prefixed header line is
    rejected explicitly rather than silently skipped.
    """
    for line in data.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            raise ResourceValidationError(
                f"the data header must not start "
                f"with '#'; write the column names as a plain "
                f"whitespace-separated line (got {stripped!r})")
        break

    header = _parse_header(data)
    header_set = set(header)

    declared = _resolve_column_names(scores, header)
    required = set(base_required) | declared
    allowed = required | set(base_optional)

    missing = required - header_set
    if missing:
        raise ResourceValidationError(
            f"data header is missing required "
            f"column(s) {sorted(missing)}; header has {header}")

    extra = header_set - allowed
    if extra:
        raise ResourceValidationError(
            f"data header has undeclared "
            f"column(s) {sorted(extra)}; declared scores are "
            f"{sorted(declared)}")
