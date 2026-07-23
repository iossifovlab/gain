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
    setup_bigwig,
    setup_directories,
    setup_genome,
    setup_genome_bgz,
    setup_tabix,
    setup_vcf,
)
from gain.genomic_resources.testing.score_specs import (
    ResourceValidationError,
    ScoreSpec,
    append_score,
    render_score_specs_yaml,
    scores_or_default,
    set_aggregator,
    set_histogram,
    set_na_values,
)


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

# ``build_definition`` writes the GRR definition alongside, not inside, the
# directory it points at.
_GRR_DEFINITION_FILENAME = "grr.yaml"
_GRR_RESOURCES_DIRNAME = "grr"


# The tabix table filename used when a table score is realized as tabix
# (``.txt.gz`` + ``.tbi``) instead of the plain ``.txt`` default.
_TABIX_FILENAME = "data.txt.gz"

# The ``header_mode`` values a table score may declare.  ``"file"`` is the
# backend default and the builder's default realize path: the authored header
# line is written into the data file and read back from it.  ``"none"`` and
# ``"list"`` both realize a HEADERLESS data file and describe the columns in
# the config instead.
_HEADER_MODES = ("file", "none", "list")


@dataclasses.dataclass(frozen=True)
class _TableScoreBuilder:
    """Immutable base for the tabular position/np/allele score builders.

    The three table-score resource types share nearly everything: score
    declaration (:class:`ScoreSpec`), header validation, YAML rendering,
    the ``with_data`` / typed ``with_score_line`` authoring modes and the
    plain-``.txt`` / tabix realize paths.  They differ only in a handful of
    class-level knobs supplied by each subclass:

    * ``SCORE_TYPE`` -- the ``type:`` config value.
    * ``TRAILING_COLUMNS`` -- extra required base columns after the position
      columns (``reference``/``alternative`` for np/allele; none for
      position).
    * ``TABLE_EXTRA_CONFIG`` -- extra lines spliced into the ``table:`` block
      (the ``reference``/``alternative`` name mapping for np/allele).  Under
      ``header_mode: none`` there is no header for a name to match, so those
      base columns are rendered by index instead -- see
      :meth:`with_header_mode`.
    * ``DEFAULT_DATA`` -- the bare-builder default data block.
    """

    scores: tuple[ScoreSpec, ...] = ()
    data: str | None = None
    rows: tuple[tuple[tuple[str, str], ...], ...] = ()
    tabix: bool = False
    chrom_mapping: dict[str, Any] | None = None
    zero_based: bool = False
    header_mode: str | None = None
    # Suppresses the ``header_mode:`` key while keeping everything else the
    # ``"none"`` mode realizes -- see :meth:`with_missing_header_mode`.
    omit_header_mode: bool = False

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
        column_name: str | None = None, column_index: int | None = None,
        desc: str | None = None,
    ) -> Self:
        """Declare a score once; ``column_name`` defaults to ``score_id``.

        Pass ``column_index`` instead to address the column by its 0-based
        position in the data header.  The two modes are mutually exclusive.
        Index addressing requires :meth:`with_data`; the typed
        :meth:`with_score_line` synthesizes the header from the declared
        column names and so has no position to point at.
        """
        return dataclasses.replace(
            self,
            scores=append_score(
                self.scores, score_id, value_type,
                column_name=column_name, column_index=column_index,
                desc=desc),
        )

    def with_chrom_mapping(self, **mapping: Any) -> Self:
        """Emit a ``chrom_mapping:`` block in the ``table:`` config.

        Keys are passed through verbatim, e.g.
        ``with_chrom_mapping(add_prefix="chr")``.
        """
        return dataclasses.replace(self, chrom_mapping=dict(mapping))

    def with_zero_based(self) -> Self:
        """Emit ``zero_based: true`` in the ``table:`` config.

        Declares the authored positions as 0-based half-open intervals, the
        convention a ``.bed``-style source uses.  The backend shifts a
        record's ``pos_begin`` up by one on read (and a single-base record's
        ``pos_end`` with it), so a row authored at ``pos_begin`` is queried at
        ``pos_begin + 1``.  Applies to both the plain ``.txt`` and the tabix
        realize paths.
        """
        return dataclasses.replace(self, zero_based=True)

    def with_header_mode(self, header_mode: str) -> Self:
        """Declare how the table's column names are described.

        ``"file"`` -- the backend default and the builder's default -- keeps
        the authored header line in the realized data file (as a ``#``
        comment on the tabix path) and lets the backend read the columns
        from it.

        ``"none"`` and ``"list"`` realize a HEADERLESS data file: the file
        carries data rows only, and the config describes the columns
        instead -- explicit ``column_index:`` mappings for ``"none"``, a
        ``header:`` list for ``"list"``.

        This is one of the knobs that reintroduces a SECOND description of
        the columns, which is what the builders otherwise exist to prevent
        (see the module docstring of :mod:`.builders` and gain#318).  It is
        kept honest by deriving everything from ONE declaration: the header
        line authored in :meth:`with_data` stays the single source, feeding
        the rendered config's column indices, the tabix
        ``seq_col``/``start_col``/``end_col``, and the header validation --
        even in the modes where it is not written into the file.

        With ``"none"`` there is no header to resolve a name against, so
        every declared score must address its column by ``column_index``
        (:meth:`with_score`); a name-addressed score is rejected at realize
        time rather than left to fail inside the score implementation.
        """
        if header_mode not in _HEADER_MODES:
            raise ResourceValidationError(
                f"unsupported header_mode {header_mode!r}; "
                f"expected one of {list(_HEADER_MODES)}")
        return dataclasses.replace(
            self, header_mode=header_mode, omit_header_mode=False)

    def with_missing_header_mode(self) -> Self:
        """Realize the misconfiguration behind gain#364.

        Everything ``with_header_mode("none")`` realizes -- a headerless
        data file with index-addressed columns -- except the
        ``header_mode:`` key itself, which the config forgets.  The backend
        therefore falls back to its ``"file"`` default and looks for a
        header the file does not have.

        The resulting resource does NOT open; it exists so a test can watch
        that failure be reported.  Do not reach for it to build a working
        headerless resource -- that is :meth:`with_header_mode`.
        """
        return dataclasses.replace(
            self, header_mode="none", omit_header_mode=True)

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
            scores=set_histogram(
                self.scores, histogram, score_id=score_id),
        )

    def with_na_values(
        self, na_values: str | list[str], *, score_id: str | None = None,
    ) -> Self:
        """Declare the NA sentinel(s) for a score.

        With ``score_id`` omitted the sentinel(s) are attached to the
        most-recently-declared score; passing ``score_id`` targets that score.
        Accepts either a scalar (``"-1"``) or a list (``["-1", "-999"]``),
        emitted verbatim under ``na_values:`` -- the resource schema permits
        both forms.
        """
        return dataclasses.replace(
            self,
            scores=set_na_values(
                self.scores, na_values, score_id=score_id),
        )

    def with_position_aggregator(
        self, aggregator: str, *, score_id: str | None = None,
    ) -> Self:
        """Declare the score's default per-position aggregator.

        Emitted under ``position_aggregator:`` -- the resource-level default a
        pipeline uses when an attribute does not name one of its own.  With
        ``score_id`` omitted it attaches to the most-recently-declared score.
        The value is rendered verbatim, so an invalid one can be authored on
        purpose to watch the resource schema reject it.
        """
        return dataclasses.replace(
            self,
            scores=set_aggregator(
                self.scores, "position_aggregator", aggregator,
                score_id=score_id),
        )

    def with_allele_aggregator(
        self, aggregator: str, *, score_id: str | None = None,
    ) -> Self:
        """Declare the score's default per-allele aggregator.

        The allele-level counterpart of :meth:`with_position_aggregator`,
        emitted under ``allele_aggregator:``.
        """
        return dataclasses.replace(
            self,
            scores=set_aggregator(
                self.scores, "allele_aggregator", aggregator,
                score_id=score_id),
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

        Each value is stringified via ``str(value)`` to form the cell text, so
        pass values whose ``str()`` is the intended cell content (e.g. avoid
        relying on float ``repr`` for exotic values).

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
        scores = scores_or_default(self.scores)
        data = self._effective_data(scores)
        _validate_score_specs(scores)
        _validate_data_header(
            data, scores,
            base_required=self.LEADING_COLUMNS + self.TRAILING_COLUMNS,
            base_optional=self.OPTIONAL_COLUMNS)
        self._validate_header_mode(scores)
        # The authored header is the single column declaration; the modes
        # that do not write it into the file still resolve their indices
        # from it.
        write_header = self._effective_header_mode() == "file"
        if self.tabix:
            setup_directories(
                resource_dir,
                {GR_CONF_FILE_NAME: self._render_config(
                    scores, _TABIX_FILENAME, data)})
            _realize_tabix_table(
                resource_dir / _TABIX_FILENAME, data,
                write_header=write_header)
        else:
            file_data = data if write_header else _strip_header(data)
            setup_directories(resource_dir, {
                GR_CONF_FILE_NAME: self._render_config(
                    scores, _DATA_FILENAME, data),
                _DATA_FILENAME: convert_to_tab_separated(file_data),
            })

    def _effective_header_mode(self) -> str:
        """Return the header mode the realized DATA is authored for.

        ``with_missing_header_mode`` realizes the data of the ``"none"``
        mode without the config key that declares it, so this is not the
        mode the config states -- it is the one the file is written in.
        """
        return self.header_mode or "file"

    def _validate_header_mode(self, scores: tuple[ScoreSpec, ...]) -> None:
        """Reject a score addressing a column that will not be resolvable."""
        if self._effective_header_mode() != "none":
            return
        named = [
            spec.score_id for spec in scores if spec.column_index is None
        ]
        if named:
            raise ResourceValidationError(
                f"header_mode 'none' leaves no header to resolve a column "
                f"name against; score(s) {sorted(named)} must be declared "
                f"with column_index")

    def build_resource(
        self, tmp_path: pathlib.Path,
    ) -> GenomicResource:
        """Realize this single resource (repo id ``""``) into ``tmp_path``.

        Delegates to the GRR builder so there is a single realize path.
        """
        return _build_single_resource(self, tmp_path)

    def _effective_data(self, scores: tuple[ScoreSpec, ...]) -> str:
        if self.data is not None and self.rows:
            raise ResourceValidationError(
                "with_data and with_score_line are mutually exclusive; "
                "author the table with only one of them")
        if self.data is not None:
            return self.data
        if self.rows:
            return self._synthesize_rows(scores)
        return self.DEFAULT_DATA

    def _synthesize_rows(self, scores: tuple[ScoreSpec, ...]) -> str:
        """Render the accumulated typed rows as a whitespace data block."""
        indexed = [s.score_id for s in scores if s.column_index is not None]
        if indexed:
            raise ResourceValidationError(
                f"score(s) {sorted(indexed)} address a column by "
                f"column_index, which needs an authored header; use "
                f"with_data instead of with_score_line")
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
        header.extend(
            spec.column_name for spec in scores
            if spec.column_name is not None
        )

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
        self, scores: tuple[ScoreSpec, ...], filename: str, data: str,
    ) -> str:
        header = _parse_header(data)
        lines = [
            f"type: {self.SCORE_TYPE}",
            "table:",
            f"    filename: {filename}",
        ]
        if self.tabix:
            lines.append("    format: tabix")
        if self.header_mode is not None and not self.omit_header_mode:
            lines.append(f"    header_mode: {self.header_mode}")
        if self.header_mode == "list":
            lines.append("    header:")
            lines.extend(f"    - {column}" for column in header)
        if self.zero_based:
            lines.append("    zero_based: true")
        if self.chrom_mapping is not None:
            lines.append("    chrom_mapping:")
            mapping_yaml = yaml.safe_dump(
                self.chrom_mapping, default_flow_style=False, sort_keys=False)
            lines.extend(
                f"        {mapping_line}"
                for mapping_line in mapping_yaml.rstrip("\n").split("\n")
            )
        config = "\n".join(lines) + "\n"
        if self._effective_header_mode() == "none":
            # No header to match a column name against, so the base columns
            # are addressed by index -- resolved from the same authored
            # header the tabix index columns come from.
            config += self._render_column_indexes(header)
        else:
            config += self.TABLE_EXTRA_CONFIG
        config += "scores:\n"
        return config + render_score_specs_yaml(scores)

    def _render_column_indexes(self, header: list[str]) -> str:
        """Render the base columns as explicit ``column_index:`` mappings."""
        columns = [
            *self.LEADING_COLUMNS,
            *(column for column in self.OPTIONAL_COLUMNS if column in header),
            *self.TRAILING_COLUMNS,
        ]
        lines: list[str] = []
        for column in columns:
            lines.extend((
                f"    {column}:",
                f"        column_index: {header.index(column)}",
            ))
        return "\n".join(lines) + "\n"


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


@dataclasses.dataclass(frozen=True)
class CnvCollectionBuilder(_TableScoreBuilder):
    """Immutable builder for a single ``cnv_collection`` resource.

    Shares the tabular-score machinery with the position/np/allele
    builders, differing only in the type value.  A CNV is a region rather
    than a point, so the default data carries the optional ``pos_end``
    column.  Reads back through ``CnvCollection``, which weights every
    record 1 however long it is.
    """

    SCORE_TYPE: ClassVar[str] = "cnv_collection"
    DEFAULT_DATA: ClassVar[str] = """
        chrom  pos_begin  pos_end  score
        1      10         19       0.1
        1      20         200      0.2
    """


_BIGWIG_FILENAME = "data.bw"

# A bedGraph row is ``chrom start end value``, so the score column is
# always the fourth.  The bigWig table has no header to name it, hence
# positional ``index:`` addressing rather than ``column_name:``.
_BIGWIG_VALUE_INDEX = 3

_BIGWIG_DEFAULT_DATA = """
    chr1  0   10  0.1
    chr1  10  20  0.2
    chr2  0   30  0.3
"""
_BIGWIG_DEFAULT_CHROM_LENS = {"chr1": 1000, "chr2": 1000}


def _normalize_bedgraph(data: str) -> str:
    """Strip blank and whitespace-only lines from a bedGraph block.

    ``setup_bigwig`` splits on newlines and asserts four columns on every
    line, so a whitespace-only line -- exactly what an indented closing
    triple-quote leaves behind -- trips a bare ``AssertionError`` naming
    neither the builder nor the row.  Authoring a block with the closing
    quote indented is the normal case for the other builders, so normalize
    here rather than make indentation significant for this one.
    """
    return "\n".join(
        stripped
        for line in data.split("\n")
        if (stripped := line.strip())
    )


@dataclasses.dataclass(frozen=True)
class BigWigScoreBuilder:
    """Immutable builder for a bigWig-backed ``position_score``.

    Authored as bedGraph rows (``chrom start end value``), whose intervals
    are 0-based half-open -- 1-based position ``p`` reads the interval
    containing ``p - 1``.  Unlike the tabular builders this one declares
    exactly one score, because a bigWig carries a single value column.
    """

    score_id: str = "score"
    value_type: str = "float"
    data: str | None = None
    chrom_lens: dict[str, int] | None = None
    histogram: dict[str, Any] | None = None
    na_values: str | list[str] | None = None
    fetch_budgets: dict[str, int] | None = None
    zero_based: bool = False

    def with_fetch_budgets(
        self, *,
        direct_fetch_size: int | None = None,
        buffer_fetch_size: int | None = None,
        use_buffered_threshold: int | None = None,
    ) -> Self:
        """Emit the bigWig fetch-tuning keys in the ``table:`` config.

        The fetch sizes are budgets in *records per range query*, not base
        pairs; ``use_buffered_threshold`` is the region width above which
        the direct strategy gives way to the buffered one.  Only the keys
        passed are emitted, so a test can pin one without implying the
        others.
        """
        budgets = {
            key: value for key, value in (
                ("direct_fetch_size", direct_fetch_size),
                ("buffer_fetch_size", buffer_fetch_size),
                ("use_buffered_threshold", use_buffered_threshold),
            ) if value is not None
        }
        return dataclasses.replace(self, fetch_budgets=budgets)

    def with_score(self, score_id: str, value_type: str = "float") -> Self:
        """Name the single score this bigWig exposes."""
        return dataclasses.replace(
            self, score_id=score_id, value_type=value_type)

    def with_na_values(self, na_values: str | list[str]) -> Self:
        """Declare the NA sentinel(s) for the single bigWig score.

        A bigWig exposes exactly one score, so no ``score_id`` is needed.
        Accepts either a scalar (``"-1"``) or a list, emitted verbatim under
        ``na_values:`` in the resource config -- the schema permits both.
        """
        if isinstance(na_values, list):
            na_values = list(na_values)
        return dataclasses.replace(self, na_values=na_values)

    def with_histogram(self, histogram: dict[str, Any]) -> Self:
        """Attach a histogram block to the single bigWig score.

        A bigWig exposes exactly one score, so no ``score_id`` is needed.  The
        block is emitted verbatim under ``histogram:`` in the resource config;
        without it a numeric bigWig score relies on the resource's auto-built
        default histogram.
        """
        # Defensive copy so a caller mutating their dict afterward cannot
        # leak into this immutable builder.
        return dataclasses.replace(self, histogram=copy.deepcopy(histogram))

    def with_data(self, data: str) -> Self:
        """Author the bedGraph rows as a whitespace-separated block."""
        return dataclasses.replace(self, data=data)

    def with_chrom_lens(self, chrom_lens: dict[str, int]) -> Self:
        """Declare the chromosome lengths written into the bigWig header."""
        return dataclasses.replace(self, chrom_lens=dict(chrom_lens))

    def with_zero_based(self) -> Self:
        """Emit ``zero_based: true`` in the ``table:`` config.

        A bigWig hard-codes its 0-based-half-open to closed-1-based
        conversion and never consults the key -- authoring it here
        realizes, config-first, the misconfiguration the table-build
        warning is meant to surface, and lets a test carry the key
        through schema validation instead of injecting it into a table
        dict by hand.
        """
        return dataclasses.replace(self, zero_based=True)

    def realize_into(self, resource_dir: pathlib.Path) -> None:
        """Write the resource config and the bigWig into ``resource_dir``."""
        data = self.data if self.data is not None else _BIGWIG_DEFAULT_DATA
        chrom_lens = (
            self.chrom_lens if self.chrom_lens is not None
            else _BIGWIG_DEFAULT_CHROM_LENS
        )
        data = _normalize_bedgraph(data)
        self._validate(data, chrom_lens)
        setup_directories(
            resource_dir, {GR_CONF_FILE_NAME: self._render_config()})
        setup_bigwig(resource_dir / _BIGWIG_FILENAME, data, chrom_lens)

    def build_resource(self, tmp_path: pathlib.Path) -> GenomicResource:
        """Realize this single resource (repo id ``""``) into ``tmp_path``."""
        return _build_single_resource(self, tmp_path)

    @staticmethod
    def _validate(data: str, chrom_lens: dict[str, int]) -> None:
        """Reject a bedGraph block the bigWig writer would reject opaquely.

        ``setup_bigwig`` asserts on arity and ``pyBigWig`` raises on an
        unheadered contig; both surface without naming the builder or the
        offending row, so check here where the row is still in hand.
        """
        for line in data.split("\n"):
            stripped = line.strip()
            if not stripped:
                continue
            tokens = stripped.split()
            if len(tokens) != 4:
                raise ResourceValidationError(
                    f"bedGraph row must have 4 columns "
                    f"(chrom start end value), got {len(tokens)}: "
                    f"{stripped!r}")
            if tokens[0] not in chrom_lens:
                raise ResourceValidationError(
                    f"bedGraph row on contig {tokens[0]!r} which has no "
                    f"declared length; call with_chrom_lens for it")

    def _render_config(self) -> str:
        budget_lines = "".join(
            f"    {key}: {value}\n"
            for key, value in (self.fetch_budgets or {}).items()
        )
        zero_based_line = (
            "    zero_based: true\n" if self.zero_based else "")
        config = (
            "type: position_score\n"
            "table:\n"
            f"    filename: {_BIGWIG_FILENAME}\n"
            f"{zero_based_line}"
            f"{budget_lines}"
            "scores:\n"
            f"- id: {self.score_id}\n"
            f"  type: {self.value_type}\n"
            f"  index: {_BIGWIG_VALUE_INDEX}\n"
        )
        if self.na_values is not None:
            na_yaml = yaml.safe_dump(
                {"na_values": self.na_values}, default_flow_style=False,
                sort_keys=False)
            config += "".join(
                f"  {na_line}\n"
                for na_line in na_yaml.rstrip("\n").split("\n")
            )
        if self.histogram is not None:
            config += "  histogram:\n"
            hist_yaml = yaml.safe_dump(
                self.histogram, default_flow_style=False, sort_keys=False)
            config += "".join(
                f"    {hist_line}\n"
                for hist_line in hist_yaml.rstrip("\n").split("\n")
            )
        return config


_VCF_FILENAME = "data.vcf.gz"

_VCF_DEFAULT_DATA = """
##fileformat=VCFv4.1
##INFO=<ID=score,Number=1,Type=Float,Description="a float">
#CHROM POS ID REF ALT QUAL FILTER INFO
chr1   10  .  A   T   .    .      score=0.1
chr1   11  .  A   T   .    .      score=0.2
"""


@dataclasses.dataclass(frozen=True)
class VcfInfoScoreBuilder:
    """Immutable builder for a VCF-backed ``allele_score`` resource.

    The score definitions are derived by the resource from the VCF's
    ``##INFO`` header rather than declared in the config, so this builder
    has no ``with_score``: author the INFO metadata in the VCF text and the
    scores follow.  Reads back through ``AlleleScore`` on the ``vcf_info``
    table backend, which the ``.vcf.gz`` filename selects.
    """

    data: str | None = None
    zero_based: bool = False

    def with_data(self, data: str) -> Self:
        """Author the whole VCF, ``##`` header lines included."""
        return dataclasses.replace(self, data=data)

    def with_zero_based(self) -> Self:
        """Emit ``zero_based: true`` in the ``table:`` config.

        A VCF is always 1-based, so the ``vcf_info`` backend ignores the
        key -- authoring it here realizes, config-first, the
        misconfiguration the table-build warning is meant to surface, and
        lets a test carry the key through schema validation instead of
        injecting it into a table dict by hand.
        """
        return dataclasses.replace(self, zero_based=True)

    def realize_into(self, resource_dir: pathlib.Path) -> None:
        """Write the resource config and the bgzipped VCF + index."""
        data = self.data if self.data is not None else _VCF_DEFAULT_DATA
        self._validate(data)
        setup_directories(
            resource_dir, {GR_CONF_FILE_NAME: self._render_config()})
        setup_vcf(resource_dir / _VCF_FILENAME, data)

    def build_resource(self, tmp_path: pathlib.Path) -> GenomicResource:
        """Realize this single resource (repo id ``""``) into ``tmp_path``."""
        return _build_single_resource(self, tmp_path)

    @staticmethod
    def _validate(data: str) -> None:
        """Require the header lines the scores are derived from.

        Without an ``##INFO`` line the resource realizes with zero scores
        and every annotated value comes back empty -- a silent, confusing
        failure well downstream of the builder.
        """
        if "##fileformat" not in data:
            raise ResourceValidationError(
                "VCF data must carry a '##fileformat' header line")
        if "##INFO=" not in data:
            raise ResourceValidationError(
                "VCF data must declare at least one '##INFO=' field; the "
                "score definitions are derived from them")
        if "#CHROM" not in data:
            raise ResourceValidationError(
                "VCF data must carry a '#CHROM' column header line")

    def _render_config(self) -> str:
        zero_based_line = (
            "    zero_based: true\n" if self.zero_based else "")
        return (
            "type: allele_score\n"
            "table:\n"
            f"    filename: {_VCF_FILENAME}\n"
            f"{zero_based_line}"
        )


_GENE_DATA_FILENAME = "data.tsv"
_DEFAULT_GENE_COLUMN = "gene"


@dataclasses.dataclass(frozen=True)
class GeneScoreBuilder:
    """Immutable builder for a single ``gene_score`` resource.

    Built on the shared score-declaration base (:class:`ScoreSpec`): scores
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

    scores: tuple[ScoreSpec, ...] = ()
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
            scores=append_score(
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
            scores=set_histogram(
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
        self._realize_all(tmp_path)
        return build_filesystem_test_repository(tmp_path)

    def build_definition(
        self, root: pathlib.Path, *, grr_id: str = "test_grr",
    ) -> pathlib.Path:
        """Realize into ``root/grr`` and write a ``root/grr.yaml``.

        Returns the path of the written definition file.  A CLI tool such
        as ``annotate_tabular`` is given a ``--grr`` definition *file*, not
        a repository object, so ``build_repo`` alone cannot drive one.  The
        definition is written OUTSIDE the resources directory: a stray
        ``grr.yaml`` sitting among the resources would be walked as though
        it were one.
        """
        resources_dir = root / _GRR_RESOURCES_DIRNAME
        self._realize_all(resources_dir)
        definition_path = root / _GRR_DEFINITION_FILENAME
        setup_directories(definition_path, yaml.safe_dump(
            {"id": grr_id, "type": "dir", "directory": str(resources_dir)},
            default_flow_style=False, sort_keys=False))
        return definition_path

    def _realize_all(self, root: pathlib.Path) -> None:
        """Realize every attached resource into ``root/<resource_id>``."""
        for resource_id, builder in self.resources:
            resource_dir = root / resource_id
            try:
                builder.realize_into(resource_dir)
            except ResourceValidationError as exc:
                raise ResourceValidationError(
                    f"resource {resource_id!r}: {exc}") from exc


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
    tabix_path: pathlib.Path, data: str, *, write_header: bool = True,
) -> None:
    """Realize ``data`` as a tabix table (``.txt.gz`` + ``.tbi``).

    With ``write_header`` (the default, the ``header_mode: file`` path) the
    header line is emitted as a ``#`` comment so the tabix table reads its
    column names from the file; ``_render_config`` then emits no
    ``header_mode`` key, relying on the tabix backend's default.  Without
    it (``header_mode`` ``none``/``list``) the header line is dropped and
    the realized file carries data rows only.

    Either way the seq/start/end column indices are derived from the SAME
    authored header the config is rendered from, so an arbitrary column
    order still indexes correctly and the two cannot drift.
    """
    header = _parse_header(data)
    chrom_col = header.index("chrom")
    start_col = header.index("pos_begin")
    end_col = header.index("pos_end") if "pos_end" in header else start_col
    content = _comment_header(data) if write_header else _strip_header(data)
    setup_tabix(
        tabix_path, content,
        seq_col=chrom_col, start_col=start_col, end_col=end_col)


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
    scores = scores_or_default(builder.scores)
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
    config += "scores:\n" + render_score_specs_yaml(scores)
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


def a_cnv_collection() -> CnvCollectionBuilder:
    """Return an immutable cnv-collection builder."""
    return CnvCollectionBuilder()


def a_bigwig_score() -> BigWigScoreBuilder:
    """Return an immutable bigWig-backed position-score builder."""
    return BigWigScoreBuilder()


def a_vcf_info_score() -> VcfInfoScoreBuilder:
    """Return an immutable VCF-backed allele-score builder."""
    return VcfInfoScoreBuilder()


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
