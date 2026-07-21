"""Score declarations shared by the GRR test-data builders.

The lowest layer of the builder DSL in :mod:`.builders`: the representation
of a single declared score column (:class:`ScoreSpec`), the pure functions
that add to and amend a tuple of them, and the renderer that turns them into
the ``scores:`` block of a ``genomic_resource.yaml``.

Every score builder -- position/np/allele/cnv, bigWig, VCF-info, gene --
declares its scores through this one representation; they differ only in the
base (non-score) columns their data tables require.  It lives in its own
module so the builder DSL can keep growing without either half of it turning
into an unreadable slab.

:class:`ResourceValidationError` is raised from here, so it is defined here
too -- the builders re-export it as part of the DSL's public surface.
"""
from __future__ import annotations

import copy
import dataclasses
from typing import Any, Literal, get_args

import yaml


class ResourceValidationError(ValueError):
    """Raised for a builder-owned validation error.

    Subclasses ``ValueError`` so existing ``pytest.raises(ValueError, ...)``
    call sites keep matching.  ``GRRBuilder.build_repo`` catches only this
    type when annotating an error with the resource id, so a genuine,
    non-validation ``ValueError`` surfacing from ``realize_into`` (e.g. a
    lower-level failure inside a ``setup_*`` helper) passes through
    un-relabeled instead of being silently recast as a validation error.
    """


@dataclasses.dataclass(frozen=True)
class ScoreSpec:
    """A single declared score column.

    The shared score-declaration representation used by BOTH the
    position-score and the gene-score builders: an ``id``, a ``column_name``
    (defaulting to the id), a value ``type``, an optional ``desc`` and an
    optional ``histogram`` block.  The two builders differ only in the base
    (non-score) columns their data tables require; the score declarations,
    their ``column_name`` defaulting, duplicate-id / duplicate-column_name
    validation and YAML rendering are all shared through this type.

    A score is addressed EITHER by ``column_name`` or by ``column_index``,
    never both.  When ``column_index`` is set, ``column_name`` is ``None``
    and the column the index points at is resolved from the data header at
    realize time (see :func:`_resolve_column_names`).
    """

    score_id: str
    value_type: str
    column_name: str | None
    column_index: int | None = None
    desc: str | None = None
    histogram: dict[str, Any] | None = None
    na_values: str | list[str] | None = None
    position_aggregator: str | None = None
    allele_aggregator: str | None = None


def append_score(
    scores: tuple[ScoreSpec, ...], score_id: str, value_type: str, *,
    column_name: str | None = None, column_index: int | None = None,
    desc: str | None = None,
) -> tuple[ScoreSpec, ...]:
    """Return ``scores`` with one more declared score appended.

    Shared by both builders' ``with_score``.  With neither addressing mode
    given, ``column_name`` defaults to ``score_id``; the two modes are
    mutually exclusive, matching the resource schema, which declares
    ``column_index`` as excluding ``name``/``column_name``/``index``.
    """
    if column_name is not None and column_index is not None:
        raise ResourceValidationError(
            f"score {score_id!r}: column_name and column_index are "
            f"mutually exclusive; address the column one way or the other")
    if column_index is not None and column_index < 0:
        raise ResourceValidationError(
            f"score {score_id!r}: column_index must be non-negative, "
            f"got {column_index}")
    if column_index is None and column_name is None:
        column_name = score_id
    spec = ScoreSpec(
        score_id=score_id,
        value_type=value_type,
        column_name=column_name,
        column_index=column_index,
        desc=desc,
    )
    return (*scores, spec)


def set_histogram(
    scores: tuple[ScoreSpec, ...], histogram: dict[str, Any], *,
    score_id: str | None = None,
) -> tuple[ScoreSpec, ...]:
    """Return ``scores`` with ``histogram`` set on one declared score.

    Shared by both builders' ``with_histogram``.  With ``score_id`` omitted
    the histogram is attached to the most-recently-declared score; passing
    ``score_id`` targets that specific score.  Declaring a histogram before
    any score, or for an unknown score id, is a validation error.
    """
    target_index = _target_index(scores, score_id, method="with_histogram")
    # Defensive copy: capture the histogram by value so a caller mutating
    # their dict afterward cannot leak into this immutable builder.
    histogram = copy.deepcopy(histogram)
    return tuple(
        dataclasses.replace(spec, histogram=histogram)
        if i == target_index else spec
        for i, spec in enumerate(scores)
    )


def set_na_values(
    scores: tuple[ScoreSpec, ...],
    na_values: str | list[str], *,
    score_id: str | None = None,
) -> tuple[ScoreSpec, ...]:
    """Return ``scores`` with ``na_values`` set on one declared score.

    Shared by the table-score builders' ``with_na_values``.  With ``score_id``
    omitted the sentinel(s) are attached to the most-recently-declared score;
    passing ``score_id`` targets that specific score.  Setting na_values before
    any score, or for an unknown score id, is a validation error.  The value is
    rendered verbatim under ``na_values:`` -- either a scalar (``na_values:
    "-1"``) or a list -- matching the resource schema's ``["string", "list"]``.
    """
    target_index = _target_index(scores, score_id, method="with_na_values")
    # Defensive copy of a list so a caller mutating theirs afterward cannot
    # leak into this immutable builder.
    if isinstance(na_values, list):
        na_values = list(na_values)
    return tuple(
        dataclasses.replace(spec, na_values=na_values)
        if i == target_index else spec
        for i, spec in enumerate(scores)
    )


# The score-level aggregator fields a resource may configure, in the order
# they are rendered.  ``nucleotide_aggregator`` is deliberately absent: the
# resource schema still accepts it, but reading it is deprecated, so the DSL
# does not offer a way to author a new resource that uses it.
AggregatorField = Literal["position_aggregator", "allele_aggregator"]
AGGREGATOR_FIELDS: tuple[AggregatorField, ...] = get_args(AggregatorField)


def set_aggregator(
    scores: tuple[ScoreSpec, ...],
    field: AggregatorField, aggregator: str, *,
    score_id: str | None = None,
) -> tuple[ScoreSpec, ...]:
    """Return ``scores`` with one aggregator ``field`` set on one score.

    Shared by the table-score builders' ``with_position_aggregator`` /
    ``with_allele_aggregator``.  With ``score_id`` omitted the aggregator is
    attached to the most-recently-declared score; passing ``score_id`` targets
    that specific score.  The value is rendered verbatim, so a test can author
    an INVALID aggregator on purpose and watch the resource schema reject it.
    """
    target_index = _target_index(scores, score_id, method=f"with_{field}")
    return tuple(
        _replace_aggregator(spec, field, aggregator)
        if i == target_index else spec
        for i, spec in enumerate(scores)
    )


def _replace_aggregator(
    spec: ScoreSpec, field: AggregatorField, aggregator: str,
) -> ScoreSpec:
    """Return ``spec`` with the named aggregator field replaced."""
    if field == "position_aggregator":
        return dataclasses.replace(spec, position_aggregator=aggregator)
    return dataclasses.replace(spec, allele_aggregator=aggregator)


def _target_index(
    scores: tuple[ScoreSpec, ...], score_id: str | None, *, method: str,
) -> int:
    """Resolve which declared score a ``with_*`` amendment applies to.

    With ``score_id`` omitted the most-recently-declared score is targeted;
    passing ``score_id`` targets that score.  Amending before any score is
    declared, or naming an unknown score, is a validation error reported
    against ``method``.
    """
    if not scores:
        raise ResourceValidationError(
            f"{method} requires a declared score; call with_score first")
    if score_id is None:
        return len(scores) - 1
    indexes = [
        i for i, spec in enumerate(scores)
        if spec.score_id == score_id
    ]
    if not indexes:
        raise ResourceValidationError(
            f"{method}: no score {score_id!r} declared")
    return indexes[-1]


def render_score_specs_yaml(scores: tuple[ScoreSpec, ...]) -> str:
    """Render declared scores as a YAML ``scores:`` list body (0-indent).

    Optional ``desc``/``histogram`` are emitted only when set, so a score
    with neither renders exactly the three ``id``/``type``/``column_name``
    lines the position-score builder emitted before the shared base.
    """
    blocks: list[str] = []
    for spec in scores:
        addressing = (
            f"  column_index: {spec.column_index}"
            if spec.column_index is not None
            else f"  column_name: {spec.column_name}"
        )
        lines = [
            f"- id: {spec.score_id}",
            f"  type: {spec.value_type}",
            addressing,
        ]
        if spec.na_values is not None:
            # Emit through yaml so a scalar renders as ``na_values: '-1'`` and
            # a list as a block sequence, both indented at the score-entry
            # level -- the schema permits either (``["string", "list"]``).
            na_yaml = yaml.safe_dump(
                {"na_values": spec.na_values}, default_flow_style=False,
                sort_keys=False)
            lines.extend(
                f"  {na_line}" if na_line else ""
                for na_line in na_yaml.rstrip("\n").split("\n")
            )
        for field in AGGREGATOR_FIELDS:
            aggregator = getattr(spec, field)
            if aggregator is None:
                continue
            # Emit through yaml so a parametrized spelling whose separator
            # needs quoting -- ``join(, )``, with its trailing space -- stays
            # the string it was authored as.
            agg_yaml = yaml.safe_dump(
                {field: aggregator}, default_flow_style=False,
                sort_keys=False).rstrip("\n")
            lines.append(f"  {agg_yaml}")
        if spec.desc is not None:
            # Emit desc through yaml so a colon/special char stays valid.
            # A multi-line desc renders as several physical lines; indent
            # EVERY line at the 2-space score-entry level (like histogram),
            # not just the first, so continuation lines never land at col 0.
            desc_yaml = yaml.safe_dump(
                {"desc": spec.desc}, default_flow_style=False,
                sort_keys=False)
            lines.extend(
                # An otherwise-empty continuation line (a blank line inside a
                # multi-line desc) is emitted empty rather than as bare
                # indentation, so no line carries trailing whitespace; YAML
                # ignores the indentation of blank scalar-continuation lines,
                # so this still round-trips.
                f"  {desc_line}" if desc_line else ""
                for desc_line in desc_yaml.rstrip("\n").split("\n")
            )
        if spec.histogram is not None:
            lines.append("  histogram:")
            hist_yaml = yaml.safe_dump(
                spec.histogram, default_flow_style=False, sort_keys=False)
            lines.extend(
                f"    {hist_line}"
                for hist_line in hist_yaml.rstrip("\n").split("\n")
            )
        blocks.append("\n".join(lines))
    return "\n".join(blocks) + "\n"


def scores_or_default(
    scores: tuple[ScoreSpec, ...],
) -> tuple[ScoreSpec, ...]:
    """Return ``scores`` or, when empty, a single default ``float`` score.

    Shared fallback for every score builder (position/np/allele/gene): a bare
    builder with no declared score realizes one ``"score"`` float column.
    """
    if scores:
        return scores
    return (ScoreSpec("score", "float", "score"),)
