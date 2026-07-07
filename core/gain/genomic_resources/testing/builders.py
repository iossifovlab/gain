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

import contextlib
import copy
import dataclasses
import gzip
import pathlib
import tempfile
import textwrap
from collections.abc import Generator
from typing import Any, ClassVar, Protocol, Self, runtime_checkable

import yaml

from gain.genomic_resources.repository import (
    GR_CONF_FILE_NAME,
    GenomicResource,
    GenomicResourceProtocolRepo,
)
from gain.genomic_resources.testing import (
    build_filesystem_test_repository,
    convert_to_tab_separated,
    setup_directories,
    setup_genome,
    setup_genome_bgz,
    setup_tabix,
)


class ResourceValidationError(ValueError):
    """Raised for a builder-owned validation error.

    Subclasses ``ValueError`` so existing ``pytest.raises(ValueError, ...)``
    call sites keep matching.  ``GRRBuilder.build_repo`` catches only this
    type when annotating an error with the resource id, so a genuine,
    non-validation ``ValueError`` surfacing from ``realize_into`` (e.g. a
    lower-level failure inside a ``setup_*`` helper) passes through
    un-relabeled instead of being silently recast as a validation error.
    """


@runtime_checkable
class ResourceBuilder(Protocol):
    """Structural interface for a single-resource test builder.

    Every resource builder knows how to realize exactly one resource --
    its config plus data/index files -- into a directory.  ``GRRBuilder``
    composes heterogeneous builders through this one seam; each
    implementation delegates to the appropriate ``setup_*`` helper from
    :mod:`gain.genomic_resources.testing`.
    """

    def realize_into(self, resource_dir: pathlib.Path) -> None:
        """Write this resource's directory into ``resource_dir``."""
        ...


_DATA_FILENAME = "data.txt"


@dataclasses.dataclass(frozen=True)
class _ScoreSpec:
    """A single declared score column.

    The shared score-declaration representation used by BOTH the
    position-score and the gene-score builders: an ``id``, a ``column_name``
    (defaulting to the id), a value ``type``, an optional ``desc`` and an
    optional ``histogram`` block.  The two builders differ only in the base
    (non-score) columns their data tables require; the score declarations,
    their ``column_name`` defaulting, duplicate-id / duplicate-column_name
    validation and YAML rendering are all shared through this type.
    """

    score_id: str
    value_type: str
    column_name: str
    desc: str | None = None
    histogram: dict[str, Any] | None = None


def _append_score(
    scores: tuple[_ScoreSpec, ...], score_id: str, value_type: str, *,
    column_name: str | None = None, desc: str | None = None,
) -> tuple[_ScoreSpec, ...]:
    """Return ``scores`` with one more declared score appended.

    Shared by both builders' ``with_score``; ``column_name`` defaults to
    ``score_id``.
    """
    spec = _ScoreSpec(
        score_id=score_id,
        value_type=value_type,
        column_name=column_name if column_name is not None else score_id,
        desc=desc,
    )
    return (*scores, spec)


def _set_histogram(
    scores: tuple[_ScoreSpec, ...], histogram: dict[str, Any], *,
    score_id: str | None = None,
) -> tuple[_ScoreSpec, ...]:
    """Return ``scores`` with ``histogram`` set on one declared score.

    Shared by both builders' ``with_histogram``.  With ``score_id`` omitted
    the histogram is attached to the most-recently-declared score; passing
    ``score_id`` targets that specific score.  Declaring a histogram before
    any score, or for an unknown score id, is a validation error.
    """
    if not scores:
        raise ResourceValidationError(
            "with_histogram requires a declared score; "
            "call with_score first")
    # Defensive copy: capture the histogram by value so a caller mutating
    # their dict afterward cannot leak into this immutable builder.
    histogram = copy.deepcopy(histogram)
    if score_id is None:
        target_index = len(scores) - 1
    else:
        indexes = [
            i for i, spec in enumerate(scores)
            if spec.score_id == score_id
        ]
        if not indexes:
            raise ResourceValidationError(
                f"with_histogram: no score {score_id!r} declared")
        target_index = indexes[-1]
    return tuple(
        dataclasses.replace(spec, histogram=histogram)
        if i == target_index else spec
        for i, spec in enumerate(scores)
    )


def _render_score_specs_yaml(scores: tuple[_ScoreSpec, ...]) -> str:
    """Render declared scores as a YAML ``scores:`` list body (0-indent).

    Optional ``desc``/``histogram`` are emitted only when set, so a score
    with neither renders exactly the three ``id``/``type``/``column_name``
    lines the position-score builder emitted before the shared base.
    """
    blocks: list[str] = []
    for spec in scores:
        lines = [
            f"- id: {spec.score_id}",
            f"  type: {spec.value_type}",
            f"  column_name: {spec.column_name}",
        ]
        if spec.desc is not None:
            # Emit desc through yaml so a colon/special char stays valid.
            # A multi-line desc renders as several physical lines; indent
            # EVERY line at the 2-space score-entry level (like histogram),
            # not just the first, so continuation lines never land at col 0.
            desc_yaml = yaml.safe_dump(
                {"desc": spec.desc}, default_flow_style=False,
                sort_keys=False)
            lines.extend(
                f"  {desc_line}"
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


# The tabix table filename used when a table score is realized as tabix
# (``.txt.gz`` + ``.tbi``) instead of the plain ``.txt`` default.
_TABIX_FILENAME = "data.txt.gz"


def _scores_or_default(
    scores: tuple[_ScoreSpec, ...],
) -> tuple[_ScoreSpec, ...]:
    """Return ``scores`` or, when empty, a single default ``float`` score.

    Shared fallback for every score builder (position/np/allele/gene): a bare
    builder with no declared score realizes one ``"score"`` float column.
    """
    if scores:
        return scores
    return (_ScoreSpec("score", "float", "score"),)


@dataclasses.dataclass(frozen=True)
class _TableScoreBuilder:
    """Immutable base for the tabular position/np/allele score builders.

    The three table-score resource types share nearly everything: score
    declaration (:class:`_ScoreSpec`), header validation, YAML rendering,
    the ``with_data`` / typed ``with_score_line`` authoring modes and the
    plain-``.txt`` / tabix realize paths.  They differ only in a handful of
    class-level knobs supplied by each subclass:

    * ``SCORE_TYPE`` -- the ``type:`` config value.
    * ``TRAILING_COLUMNS`` -- extra required base columns after the position
      columns (``reference``/``alternative`` for np/allele; none for
      position).
    * ``TABLE_EXTRA_CONFIG`` -- extra lines spliced into the ``table:`` block
      (the ``reference``/``alternative`` name mapping for np/allele).
    * ``DEFAULT_DATA`` -- the bare-builder default data block.
    """

    scores: tuple[_ScoreSpec, ...] = ()
    data: str | None = None
    rows: tuple[tuple[tuple[str, str], ...], ...] = ()
    tabix: bool = False

    # Subclass-provided knobs.
    SCORE_TYPE: ClassVar[str] = ""
    LEADING_COLUMNS: ClassVar[tuple[str, ...]] = ("chrom", "pos_begin")
    TRAILING_COLUMNS: ClassVar[tuple[str, ...]] = ()
    # ``pos_end`` is an allowed optional column for EVERY table score type,
    # np/allele included: they realize onto the same genomic_position_table
    # backend, which derives ``pos_end_key`` from the header and range-matches
    # a query position inside a record's [pos_begin, pos_end] span.  Shared,
    # not position-only -- so it stays on the base and is inherited.
    OPTIONAL_COLUMNS: ClassVar[tuple[str, ...]] = ("pos_end",)
    TABLE_EXTRA_CONFIG: ClassVar[str] = ""
    DEFAULT_DATA: ClassVar[str] = ""

    def with_score(
        self, score_id: str, value_type: str, *,
        column_name: str | None = None, desc: str | None = None,
    ) -> Self:
        """Declare a score once; ``column_name`` defaults to ``score_id``."""
        return dataclasses.replace(
            self,
            scores=_append_score(
                self.scores, score_id, value_type,
                column_name=column_name, desc=desc),
        )

    def with_histogram(
        self, histogram: dict[str, Any], *, score_id: str | None = None,
    ) -> Self:
        """Attach a histogram block to a declared score.

        With ``score_id`` omitted the histogram is attached to the
        most-recently-declared score; passing ``score_id`` targets that
        score.  The block is emitted verbatim under ``histogram:`` in the
        resource config.
        """
        return dataclasses.replace(
            self,
            scores=_set_histogram(
                self.scores, histogram, score_id=score_id),
        )

    def with_data(self, data: str) -> Self:
        """Author the score table as a whitespace-separated block.

        The block is validated at the header level only: it must contain
        at least the declared columns (required position columns plus each
        score's ``column_name``). Row-level completeness is not checked, so
        a header-only block validates and realizes -- reading it back then
        surfaces a lower-level score error, not a builder one.

        Mutually exclusive with :meth:`with_score_line`; setting both raises
        ``ResourceValidationError`` when the resource is realized.
        """
        return dataclasses.replace(self, data=data)

    def with_score_line(self, **columns: Any) -> Self:
        """Append one typed data row; multiple calls accumulate.

        A typed alternative to the free-form :meth:`with_data` string: each
        call contributes one row as ``column=value`` keyword pairs (position
        columns plus each declared score's ``column_name``).  The builder
        synthesizes the SAME table -- header from the declared columns, rows
        from the accumulated calls -- that the equivalent ``with_data`` string
        would produce, so both authoring modes read back identically.

        Mutually exclusive with :meth:`with_data`.
        """
        row = tuple((str(key), str(value)) for key, value in columns.items())
        return dataclasses.replace(self, rows=(*self.rows, row))

    def with_tabix(self) -> Self:
        """Realize the score table as tabix (``.txt.gz`` + ``.tbi``).

        The default is a plain ``.txt`` table; ``with_tabix`` switches the
        realize path to :func:`setup_tabix` and points ``table.filename`` at
        the ``.txt.gz`` with ``format: tabix``.  The resource reads back
        identically to the plain form.

        Precondition: the authored rows (via :meth:`with_data` or
        :meth:`with_score_line`) must be position-sorted -- ascending by
        chrom then pos_begin -- when ``with_tabix`` is used, because
        ``pysam.tabix_index`` requires sorted input and otherwise fails
        loudly with an ``OSError`` un-annotated by the resource id.
        """
        return dataclasses.replace(self, tabix=True)

    def realize_into(self, resource_dir: pathlib.Path) -> None:
        """Write this table-score resource into ``resource_dir``.

        Raises a ``ResourceValidationError`` on invalid content;
        ``GRRBuilder`` annotates it with the resource id.
        """
        scores = _scores_or_default(self.scores)
        data = self._effective_data(scores)
        _validate_score_specs(scores)
        _validate_data_header(
            data, scores,
            base_required=self.LEADING_COLUMNS + self.TRAILING_COLUMNS,
            base_optional=self.OPTIONAL_COLUMNS)
        if self.tabix:
            setup_directories(
                resource_dir,
                {GR_CONF_FILE_NAME: self._render_config(
                    scores, _TABIX_FILENAME)})
            _realize_tabix_table(resource_dir / _TABIX_FILENAME, data)
        else:
            setup_directories(resource_dir, {
                GR_CONF_FILE_NAME: self._render_config(scores, _DATA_FILENAME),
                _DATA_FILENAME: convert_to_tab_separated(data),
            })

    def build_resource(
        self, tmp_path: pathlib.Path,
    ) -> GenomicResource:
        """Realize this single resource (repo id ``""``) into ``tmp_path``.

        Delegates to the GRR builder so there is a single realize path.
        """
        return _build_single_resource(self, tmp_path)

    def _effective_data(self, scores: tuple[_ScoreSpec, ...]) -> str:
        if self.data is not None and self.rows:
            raise ResourceValidationError(
                "with_data and with_score_line are mutually exclusive; "
                "author the table with only one of them")
        if self.data is not None:
            return self.data
        if self.rows:
            return self._synthesize_rows(scores)
        return self.DEFAULT_DATA

    def _synthesize_rows(self, scores: tuple[_ScoreSpec, ...]) -> str:
        """Render the accumulated typed rows as a whitespace data block."""
        row_dicts = [dict(row) for row in self.rows]
        uses_pos_end = any("pos_end" in rd for rd in row_dicts)
        if uses_pos_end and not all("pos_end" in rd for rd in row_dicts):
            raise ResourceValidationError(
                "with_score_line: 'pos_end' must be given on every row "
                "or on none")
        header = list(self.LEADING_COLUMNS)
        if uses_pos_end:
            header.append("pos_end")
        header.extend(self.TRAILING_COLUMNS)
        header.extend(spec.column_name for spec in scores)

        lines = ["  ".join(header)]
        header_set = set(header)
        for rd in row_dicts:
            missing = header_set - set(rd)
            if missing:
                raise ResourceValidationError(
                    f"with_score_line row is missing column(s) "
                    f"{sorted(missing)}; expected {header}")
            extra = set(rd) - header_set
            if extra:
                raise ResourceValidationError(
                    f"with_score_line row has unexpected column(s) "
                    f"{sorted(extra)}; expected {header}")
            lines.append("  ".join(rd[col] for col in header))
        return "\n".join(lines) + "\n"

    def _render_config(
        self, scores: tuple[_ScoreSpec, ...], filename: str,
    ) -> str:
        lines = [
            f"type: {self.SCORE_TYPE}",
            "table:",
            f"    filename: {filename}",
        ]
        if self.tabix:
            lines.append("    format: tabix")
        config = "\n".join(lines) + "\n"
        config += self.TABLE_EXTRA_CONFIG
        config += "scores:\n"
        return config + _render_score_specs_yaml(scores)


@dataclasses.dataclass(frozen=True)
class PositionScoreBuilder(_TableScoreBuilder):
    """Immutable builder for a single ``position_score`` resource."""

    SCORE_TYPE: ClassVar[str] = "position_score"
    DEFAULT_DATA: ClassVar[str] = """
        chrom  pos_begin  score
        1      10         0.1
        1      11         0.2
        1      15         0.3
    """


# The ``reference``/``alternative`` column mapping spliced into the ``table:``
# block of an np/allele score config; both types locate their ref/alt columns
# by name (matching the ``reference``/``alternative`` data columns).
_REF_ALT_TABLE_CONFIG = (
    "    reference:\n"
    "      name: reference\n"
    "    alternative:\n"
    "      name: alternative\n"
)

# np/allele default data: chrom/pos_begin plus the ref/alt columns and one
# default float score.
_NP_ALLELE_DEFAULT_DATA = """
    chrom  pos_begin  reference  alternative  score
    1      10         A          G            0.1
    1      10         A          C            0.2
    1      16         C          T            0.3
"""


@dataclasses.dataclass(frozen=True)
class NPScoreBuilder(_TableScoreBuilder):
    """Immutable builder for a single ``np_score`` resource.

    Shares the whole tabular-score machinery with the position and allele
    builders (see :class:`_TableScoreBuilder`); it differs only in the
    ``np_score`` type, the required ``reference``/``alternative`` base columns
    and the ``table:`` ref/alt name mapping.  Reads back through
    ``AlleleScore``.
    """

    SCORE_TYPE: ClassVar[str] = "np_score"
    TRAILING_COLUMNS: ClassVar[tuple[str, ...]] = ("reference", "alternative")
    TABLE_EXTRA_CONFIG: ClassVar[str] = _REF_ALT_TABLE_CONFIG
    DEFAULT_DATA: ClassVar[str] = _NP_ALLELE_DEFAULT_DATA


@dataclasses.dataclass(frozen=True)
class AlleleScoreBuilder(_TableScoreBuilder):
    """Immutable builder for a single ``allele_score`` resource.

    Identical to :class:`NPScoreBuilder` apart from the ``allele_score``
    type value; both require ``reference``/``alternative`` columns and read
    back through ``AlleleScore``.
    """

    SCORE_TYPE: ClassVar[str] = "allele_score"
    TRAILING_COLUMNS: ClassVar[tuple[str, ...]] = ("reference", "alternative")
    TABLE_EXTRA_CONFIG: ClassVar[str] = _REF_ALT_TABLE_CONFIG
    DEFAULT_DATA: ClassVar[str] = _NP_ALLELE_DEFAULT_DATA


_GENE_DATA_FILENAME = "data.tsv"
_DEFAULT_GENE_COLUMN = "gene"


@dataclasses.dataclass(frozen=True)
class GeneScoreBuilder:
    """Immutable builder for a single ``gene_score`` resource.

    Built on the shared score-declaration base (:class:`_ScoreSpec`): scores
    are declared with :meth:`with_score` (``column_name`` defaults to the
    score id) and validated for duplicate ids / column names exactly like a
    position score.  The gene→value table is authored with :meth:`with_data`
    as a whitespace block whose header must be ``{gene_column}`` plus each
    declared score's ``column_name``.

    Realizes as a PLAIN (non-gzipped) tab-separated ``data.tsv`` table with a
    top-level ``filename:`` config -- mirroring the simplest working
    ``gene_score`` fixture.  A bare builder realizes a valid minimal readable
    gene score: one ``float`` score and a few gene rows, with NO histogram
    (the numeric default histogram is auto-built when the score is read).
    """

    scores: tuple[_ScoreSpec, ...] = ()
    data: str | None = None
    gene_column: str = _DEFAULT_GENE_COLUMN
    gzipped: bool = False

    def with_score(
        self, score_id: str, value_type: str = "float", *,
        column_name: str | None = None, desc: str | None = None,
    ) -> GeneScoreBuilder:
        """Declare a gene score; ``column_name`` defaults to ``score_id``."""
        return dataclasses.replace(
            self,
            scores=_append_score(
                self.scores, score_id, value_type,
                column_name=column_name, desc=desc),
        )

    def with_histogram(
        self, histogram: dict[str, Any], *, score_id: str | None = None,
    ) -> GeneScoreBuilder:
        """Attach a histogram block to a declared score.

        With ``score_id`` omitted the histogram is attached to the
        most-recently-declared score; passing ``score_id`` targets that
        score.  Omitted by default: a numeric score relies on the resource's
        auto-built default histogram, so no ``histogram:`` block is emitted
        unless one is declared here.
        """
        return dataclasses.replace(
            self,
            scores=_set_histogram(
                self.scores, histogram, score_id=score_id),
        )

    def with_gene_column(self, name: str) -> GeneScoreBuilder:
        """Set the gene-id column name (default ``"gene"``)."""
        return dataclasses.replace(self, gene_column=name)

    def with_gzip(self) -> GeneScoreBuilder:
        """Realize the gene table gzipped (``.tsv.gz``) instead of plain.

        The default is a plain ``.tsv`` table; ``with_gzip`` gzips the TSV to
        ``data.tsv.gz`` and points ``filename:`` at it.  The resource reads
        back identically to the plain form.
        """
        return dataclasses.replace(self, gzipped=True)

    def with_data(self, data: str) -> GeneScoreBuilder:
        """Author the gene→value table as a whitespace-separated block.

        Validated at the header level only: it must contain the gene column
        plus each declared score's ``column_name``; a missing declared column
        or an undeclared extra column raises ``ResourceValidationError``.
        """
        return dataclasses.replace(self, data=data)

    def realize_into(self, resource_dir: pathlib.Path) -> None:
        """Write this gene-score resource into ``resource_dir``.

        Raises a ``ResourceValidationError`` on invalid content;
        ``GRRBuilder`` annotates it with the resource id.
        """
        setup_directories(resource_dir, _build_gene_score_content(self))

    def build_resource(
        self, tmp_path: pathlib.Path,
    ) -> GenomicResource:
        """Realize this single resource (repo id ``""``) into ``tmp_path``."""
        return _build_single_resource(self, tmp_path)


@dataclasses.dataclass(frozen=True)
class GRRBuilder:
    """Immutable builder composing resources into a filesystem GRR.

    Resources are held behind the shared :class:`ResourceBuilder` seam, so
    a single GRR can compose heterogeneous resource types (e.g. a genome
    plus a position score).  ``build_repo`` realizes each builder into its
    own ``root / resource_id`` directory; the id is known here, so any
    ``ValueError`` a builder raises is annotated with it centrally.
    """

    resources: tuple[tuple[str, ResourceBuilder], ...] = ()

    def with_resource(
        self, resource_id: str, resource_builder: ResourceBuilder,
    ) -> GRRBuilder:
        """Attach a resource, assigning its repo id here.

        Rejects a duplicate id fast at the call site: two resources sharing
        an id would realize into the same directory with the second
        silently winning.
        """
        if any(rid == resource_id for rid, _ in self.resources):
            raise ResourceValidationError(
                f"duplicate resource id {resource_id!r} declared "
                f"more than once")
        return dataclasses.replace(
            self,
            resources=(*self.resources, (resource_id, resource_builder)),
        )

    def build_repo(
        self, tmp_path: pathlib.Path,
    ) -> GenomicResourceProtocolRepo:
        """Realize a filesystem GRR into ``tmp_path``."""
        for resource_id, builder in self.resources:
            resource_dir = tmp_path / resource_id
            try:
                builder.realize_into(resource_dir)
            except ResourceValidationError as exc:
                raise ResourceValidationError(
                    f"resource {resource_id!r}: {exc}") from exc
        return build_filesystem_test_repository(tmp_path)


def _build_single_resource(
    builder: ResourceBuilder, tmp_path: pathlib.Path,
) -> GenomicResource:
    """Realize one builder as the sole resource (repo id ``""``).

    Shared single-realize path for every ``ResourceBuilder.build_resource``,
    so all resource types route through the same GRR-builder seam.
    """
    return (
        a_grr()
        .with_resource("", builder)
        .build_repo(tmp_path)
        .get_resource("")
    )


def _realize_tabix_table(
    tabix_path: pathlib.Path, data: str,
) -> None:
    """Realize ``data`` as a tabix table (``.txt.gz`` + ``.tbi``).

    The header line is emitted as a ``#`` comment so the tabix table reads
    its column names from the file.  ``_render_config`` does not emit a
    ``header_mode`` key, so this relies on the tabix backend's default
    ``header_mode`` rather than setting it explicitly; the seq/start/end
    column indices are derived from the header so an arbitrary column order
    still indexes correctly.
    """
    header = _parse_header(data)
    chrom_col = header.index("chrom")
    start_col = header.index("pos_begin")
    end_col = header.index("pos_end") if "pos_end" in header else start_col
    setup_tabix(
        tabix_path, _comment_header(data),
        seq_col=chrom_col, start_col=start_col, end_col=end_col)


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
    scores: tuple[_ScoreSpec, ...],
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
        if spec.column_name in seen_columns:
            raise ResourceValidationError(
                f"duplicate column_name "
                f"{spec.column_name!r} shared by more than one score")
        seen_columns.add(spec.column_name)


def _validate_data_header(
    data: str, scores: tuple[_ScoreSpec, ...], *,
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

    declared = {spec.column_name for spec in scores}
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


def _effective_gene_data(builder: GeneScoreBuilder) -> str:
    if builder.data is not None:
        return builder.data
    return """
        gene  score
        G1    0.1
        G2    0.2
        G3    0.3
    """


def _build_gene_score_content(
    builder: GeneScoreBuilder,
) -> dict[str, Any]:
    """Build the pure filesystem content dict for one gene-score resource.

    Validation raises a ``ResourceValidationError``; the caller
    (``GRRBuilder``) annotates it with the resource id, so messages here
    stay id-free.
    """
    scores = _scores_or_default(builder.scores)
    data = _effective_gene_data(builder)
    _validate_score_specs(scores)
    colliding = [
        spec.score_id for spec in scores
        if spec.column_name == builder.gene_column
    ]
    if colliding:
        raise ResourceValidationError(
            f"score(s) {sorted(colliding)} declare column_name "
            f"{builder.gene_column!r}, which is the gene column; a score "
            f"cannot read from the gene column -- give it a distinct "
            f"column_name")
    _validate_data_header(
        data, scores, base_required=(builder.gene_column,))

    filename = (
        f"{_GENE_DATA_FILENAME}.gz" if builder.gzipped
        else _GENE_DATA_FILENAME)
    config = textwrap.dedent(f"""\
        type: gene_score
        filename: {filename}
        """)
    if builder.gene_column != _DEFAULT_GENE_COLUMN:
        config += f"gene_column: {builder.gene_column}\n"
    config += "scores:\n" + _render_score_specs_yaml(scores)
    tsv = convert_to_tab_separated(data)
    table_content: str | bytes = (
        gzip.compress(tsv.encode()) if builder.gzipped else tsv)
    return {
        GR_CONF_FILE_NAME: config,
        filename: table_content,
    }


_GENOME_BASENAME = "chr"

# A deterministic, valid minimal sequence for a bare genome (24 bases).
_MINIMAL_GENOME_SEQUENCE = "ACGTACGTACGTACGTACGTACGT"


@dataclasses.dataclass(frozen=True)
class ReferenceGenomeBuilder:
    """Immutable builder for a single ``genome`` resource.

    Two authoring modes:

    * ``with_fasta(raw)`` -- author the FASTA text.  The content is
      normalized via ``convert_to_tab_separated`` (leading indentation and
      blank lines are stripped, internal whitespace within a line becomes a
      TAB), so write single-token headers and put each chromosome's
      sequence on its own line(s) with no internal spaces.
    * ``with_chromosome(id, seq)`` -- accumulate chromosomes; the FASTA is
      synthesized (``>id`` header + the sequence wrapped at
      ``with_line_width``).

    The two modes are mutually exclusive: setting both raises when the
    genome is realized.  A bare builder (neither set) realizes a valid
    minimal genome -- one chromosome ``"1"`` with a short deterministic
    sequence.

    Realization is bgzipped by default (``.fa.gz`` + ``.fai`` + ``.gzi``);
    ``as_plain()`` switches to a plain ``.fa`` + ``.fai``.
    """

    fasta: str | None = None
    chromosomes: tuple[tuple[str, str], ...] = ()
    line_width: int = 60
    bgzip: bool = True

    def with_fasta(self, raw: str) -> ReferenceGenomeBuilder:
        """Author the genome as FASTA text (primary mode).

        The content is not byte-exact: it is normalized via
        ``convert_to_tab_separated`` (leading indentation and blank lines
        are stripped; internal whitespace within a line becomes a TAB).
        Write single-token headers (``>1``, not ``>1 description``) and put
        each chromosome's sequence on its own line(s) with no internal
        spaces.

        Rejects empty or whitespace-only content fast at the call site (a
        pysam ``SamtoolsError`` otherwise surfaces with no resource context
        deep inside faidx), mirroring the ``with_chromosome`` guard.
        """
        if not raw.strip():
            raise ResourceValidationError(
                "reference genome: FASTA content must be non-empty")
        return dataclasses.replace(self, fasta=raw)

    def with_chromosome(
        self, chrom_id: str, sequence: str,
    ) -> ReferenceGenomeBuilder:
        """Accumulate one chromosome; FASTA is synthesized on realize.

        Rejects an empty or whitespace-only sequence fast at the call site
        (a pysam ``SamtoolsError`` otherwise surfaces with no resource
        context deep inside faidx).
        """
        if not sequence.strip():
            raise ResourceValidationError(
                f"chromosome {chrom_id!r}: sequence must be non-empty")
        return dataclasses.replace(
            self, chromosomes=(*self.chromosomes, (chrom_id, sequence)))

    def with_line_width(self, n: int) -> ReferenceGenomeBuilder:
        """Set the FASTA wrapping width for the synthesized-FASTA path."""
        if n <= 0:
            raise ResourceValidationError(
                f"line width must be positive, got {n}")
        return dataclasses.replace(self, line_width=n)

    def as_plain(self) -> ReferenceGenomeBuilder:
        """Realize a plain (uncompressed) ``.fa`` genome instead of bgz."""
        return dataclasses.replace(self, bgzip=False)

    def realize_into(self, resource_dir: pathlib.Path) -> None:
        """Write this genome resource into ``resource_dir``.

        Delegates compression/indexing and the ``genomic_resource.yaml``
        to the existing ``setup_genome``/``setup_genome_bgz`` helpers.
        """
        content = self._effective_fasta()
        if self.bgzip:
            setup_genome_bgz(
                resource_dir / f"{_GENOME_BASENAME}.fa.gz", content)
        else:
            setup_genome(resource_dir / f"{_GENOME_BASENAME}.fa", content)

    def build_resource(
        self, tmp_path: pathlib.Path,
    ) -> GenomicResource:
        """Realize this single resource (repo id ``""``) into ``tmp_path``."""
        return _build_single_resource(self, tmp_path)

    def _effective_fasta(self) -> str:
        if self.fasta is not None and self.chromosomes:
            raise ResourceValidationError(
                "reference genome: with_fasta and with_chromosome are "
                "mutually exclusive; set only one authoring mode")
        if self.fasta is not None:
            return self.fasta
        chromosomes = self.chromosomes or (("1", _MINIMAL_GENOME_SEQUENCE),)
        return _synthesize_fasta(chromosomes, self.line_width)


def _synthesize_fasta(
    chromosomes: tuple[tuple[str, str], ...], line_width: int,
) -> str:
    """Render chromosomes as FASTA, wrapping each sequence at line_width."""
    lines: list[str] = []
    for chrom_id, sequence in chromosomes:
        lines.append(f">{chrom_id}")
        lines.extend(
            sequence[i:i + line_width]
            for i in range(0, len(sequence), line_width))
    return "\n".join(lines)


def a_reference_genome() -> ReferenceGenomeBuilder:
    """Return an immutable reference-genome builder."""
    return ReferenceGenomeBuilder()


def a_position_score() -> PositionScoreBuilder:
    """Return an immutable position-score builder."""
    return PositionScoreBuilder()


def a_np_score() -> NPScoreBuilder:
    """Return an immutable np-score builder."""
    return NPScoreBuilder()


def an_allele_score() -> AlleleScoreBuilder:
    """Return an immutable allele-score builder."""
    return AlleleScoreBuilder()


def a_gene_score() -> GeneScoreBuilder:
    """Return an immutable gene-score builder."""
    return GeneScoreBuilder()


def a_grr() -> GRRBuilder:
    """Return an immutable GRR-composition builder."""
    return GRRBuilder()


@contextlib.contextmanager
def build_repo_tempdir(
    grr_builder: GRRBuilder,
) -> Generator[GenomicResourceProtocolRepo, None, None]:
    """Realize ``grr_builder`` into a self-managed temporary directory.

    A ``tmp_path``-free realize form for non-pytest callers: the GRR is
    realized into a fresh :class:`tempfile.TemporaryDirectory`, yielded as an
    open repository, and the directory is removed on exit.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        yield grr_builder.build_repo(pathlib.Path(tmp_dir))


@contextlib.contextmanager
def build_resource_tempdir(
    builder: ResourceBuilder,
) -> Generator[GenomicResource, None, None]:
    """Realize one ``builder`` into a self-managed temporary directory.

    The single-resource counterpart of :func:`build_repo_tempdir`: yields the
    sole realized resource and cleans the temporary directory up on exit.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        yield _build_single_resource(builder, pathlib.Path(tmp_dir))
