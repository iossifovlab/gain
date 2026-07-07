"""Fluent, immutable test-data builders for GRR resources.

This module offers a small builder DSL for composing genomic resources
into a filesystem GRR that a test can open and read back.  Builders are
immutable (frozen dataclasses); every ``with_*`` method returns a NEW
builder, so a partly-configured builder can be shared across test
variations without leaking state.

The builders assemble a pure in-memory recipe with no side effects; the
``build_*`` methods delegate the actual file writing and repository
construction to the existing helpers in
:mod:`gain.genomic_resources.testing` (``setup_directories`` and
``build_filesystem_test_repository``).

Example::

    def test_it(tmp_path):
        repo = (
            a_grr()
            .with_resource(
                "scores/pos",
                a_position_score()
                .with_score("phastCons", "float")
                .with_data('''
                    chrom  pos_begin  phastCons
                    1      10         0.1
                    1      11         0.2
                '''),
            )
            .build_repo(tmp_path)
        )
        score = PositionScore(repo.get_resource("scores/pos")).open()
        assert score.fetch_scores("1", 10) == [0.1]
"""
from __future__ import annotations

import dataclasses
import pathlib
import textwrap
from typing import Any

from gain.genomic_resources.repository import (
    GR_CONF_FILE_NAME,
    GenomicResource,
    GenomicResourceProtocolRepo,
)
from gain.genomic_resources.testing import (
    build_filesystem_test_repository,
    convert_to_tab_separated,
    setup_directories,
)

_DATA_FILENAME = "data.txt"

# Position columns understood by a position-score table.  ``chrom`` and
# ``pos_begin`` are always required; ``pos_end`` is optional (present only
# for range rows).
_REQUIRED_POSITION_COLUMNS = ("chrom", "pos_begin")
_OPTIONAL_POSITION_COLUMNS = ("pos_end",)


@dataclasses.dataclass(frozen=True)
class _ScoreSpec:
    """A single declared score column."""

    score_id: str
    value_type: str
    column_name: str


@dataclasses.dataclass(frozen=True)
class PositionScoreBuilder:
    """Immutable builder for a single ``position_score`` resource."""

    scores: tuple[_ScoreSpec, ...] = ()
    data: str | None = None

    def with_score(
        self, score_id: str, value_type: str, *,
        column_name: str | None = None,
    ) -> PositionScoreBuilder:
        """Declare a score once; ``column_name`` defaults to ``score_id``."""
        spec = _ScoreSpec(
            score_id=score_id,
            value_type=value_type,
            column_name=column_name if column_name is not None else score_id,
        )
        return dataclasses.replace(self, scores=(*self.scores, spec))

    def with_data(self, data: str) -> PositionScoreBuilder:
        """Author the score table as a whitespace-separated block.

        The block is validated at the header level only: it must contain
        at least the declared columns (required position columns plus each
        score's ``column_name``). Row-level completeness is not checked, so
        a header-only block validates and realizes -- reading it back then
        surfaces a lower-level ``PositionScore`` error, not a builder one.
        """
        return dataclasses.replace(self, data=data)

    def build_resource(
        self, tmp_path: pathlib.Path,
    ) -> GenomicResource:
        """Realize this single resource (repo id ``""``) into ``tmp_path``.

        Delegates to the GRR builder so there is a single realize path.
        """
        return (
            a_grr()
            .with_resource("", self)
            .build_repo(tmp_path)
            .get_resource("")
        )


@dataclasses.dataclass(frozen=True)
class GRRBuilder:
    """Immutable builder composing resources into a filesystem GRR."""

    # The position-score-specific typing here is intentional (YAGNI).  It
    # will be generalized to a shared resource-builder Protocol/base when
    # the gene-score slice lands (iossifovlab/gain#193).
    resources: tuple[tuple[str, PositionScoreBuilder], ...] = ()

    def with_resource(
        self, resource_id: str, resource_builder: PositionScoreBuilder,
    ) -> GRRBuilder:
        """Attach a resource, assigning its repo id here."""
        return dataclasses.replace(
            self,
            resources=(*self.resources, (resource_id, resource_builder)),
        )

    def build_repo(
        self, tmp_path: pathlib.Path,
    ) -> GenomicResourceProtocolRepo:
        """Realize a filesystem GRR into ``tmp_path``."""
        content: dict[str, Any] = {
            resource_id: _build_resource_content(builder, resource_id)
            for resource_id, builder in self.resources
        }
        setup_directories(tmp_path, content)
        return build_filesystem_test_repository(tmp_path)


def _effective_scores(
    builder: PositionScoreBuilder,
) -> tuple[_ScoreSpec, ...]:
    if builder.scores:
        return builder.scores
    return (_ScoreSpec("score", "float", "score"),)


def _effective_data(builder: PositionScoreBuilder) -> str:
    if builder.data is not None:
        return builder.data
    return """
        chrom  pos_begin  score
        1      10         0.1
        1      11         0.2
        1      15         0.3
    """


def _build_resource_content(
    builder: PositionScoreBuilder, resource_id: str,
) -> dict[str, Any]:
    """Build the pure filesystem content dict for one resource."""
    scores = _effective_scores(builder)
    data = _effective_data(builder)
    _validate_scores(resource_id, scores)
    _validate_data_header(resource_id, data, scores)

    scores_yaml = "".join(
        f"                - id: {spec.score_id}\n"
        f"                  type: {spec.value_type}\n"
        f"                  column_name: {spec.column_name}\n"
        for spec in scores
    )
    config = textwrap.dedent(f"""\
        type: position_score
        table:
            filename: {_DATA_FILENAME}
        scores:
        """) + textwrap.dedent(scores_yaml)
    return {
        GR_CONF_FILE_NAME: config,
        _DATA_FILENAME: convert_to_tab_separated(data),
    }


def _parse_header(data: str) -> list[str]:
    """Return the column tokens of the first non-empty data line."""
    for line in data.split("\n"):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        return stripped.split()
    return []


def _validate_scores(
    resource_id: str, scores: tuple[_ScoreSpec, ...],
) -> None:
    """Validate the declared scores for duplicate ids or column names.

    A set-based check silently collapses duplicates, so two scores that
    share a ``score_id`` or a ``column_name`` would validate cleanly and
    read the same value.  Reject both explicitly.
    """
    seen_ids: set[str] = set()
    for spec in scores:
        if spec.score_id in seen_ids:
            raise ValueError(
                f"resource {resource_id!r}: duplicate score id "
                f"{spec.score_id!r} declared more than once")
        seen_ids.add(spec.score_id)

    seen_columns: set[str] = set()
    for spec in scores:
        if spec.column_name in seen_columns:
            raise ValueError(
                f"resource {resource_id!r}: duplicate column_name "
                f"{spec.column_name!r} shared by more than one score")
        seen_columns.add(spec.column_name)


def _validate_data_header(
    resource_id: str, data: str, scores: tuple[_ScoreSpec, ...],
) -> None:
    """Validate the data header against the declared scores.

    The header must contain the required position columns plus each
    declared score's ``column_name``.  A missing declared column or an
    undeclared extra column raises ``ValueError``.  Because the builder
    owns the data format, a conventional ``#``-prefixed header line is
    rejected explicitly rather than silently skipped.
    """
    for line in data.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            raise ValueError(
                f"resource {resource_id!r}: the data header must not start "
                f"with '#'; write the column names as a plain "
                f"whitespace-separated line (got {stripped!r})")
        break

    header = _parse_header(data)
    header_set = set(header)

    declared = {spec.column_name for spec in scores}
    required = set(_REQUIRED_POSITION_COLUMNS) | declared
    allowed = required | set(_OPTIONAL_POSITION_COLUMNS)

    missing = required - header_set
    if missing:
        raise ValueError(
            f"resource {resource_id!r}: data header is missing required "
            f"column(s) {sorted(missing)}; header has {header}")

    extra = header_set - allowed
    if extra:
        raise ValueError(
            f"resource {resource_id!r}: data header has undeclared "
            f"column(s) {sorted(extra)}; declared scores are "
            f"{sorted(declared)}")


def a_position_score() -> PositionScoreBuilder:
    """Return an immutable position-score builder."""
    return PositionScoreBuilder()


def a_grr() -> GRRBuilder:
    """Return an immutable GRR-composition builder."""
    return GRRBuilder()
