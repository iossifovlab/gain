"""Fluent, immutable test-data builders for table-backed score resources.

A sibling of :mod:`gain.genomic_resources.testing.builders`.  It holds the
position, allele and fragment score builders, their shared base
``_TableScoreBuilder`` and the tabix realize step.  The dependency runs
one way: this module imports the shared single-realize seam from
``builders``, and ``builders`` does not import back.  So
``a_position_score``, ``an_allele_score`` and ``a_fragment_score`` are
imported from here.
"""
from __future__ import annotations

import copy
import dataclasses
import pathlib
import shutil
from typing import Any, ClassVar, Self

import yaml

from gain.genomic_resources.repository import GenomicResource
from gain.genomic_resources.repository_ids import GR_CONF_FILE_NAME
from gain.genomic_resources.resource_types import (
    require_fragment_score_type,
)
from gain.genomic_resources.testing import (
    convert_to_tab_separated,
    setup_directories,
    setup_tabix,
)
from gain.genomic_resources.testing.builders import (
    _DATA_FILENAME,
    _build_single_resource,
)
from gain.genomic_resources.testing.extra_files import ExtraFilesMixin
from gain.genomic_resources.testing.resource_meta import (
    MetaMixin,
)
from gain.genomic_resources.testing.score_builder_helpers import (
    _comment_header,
    _parse_header,
    _strip_header,
    _validate_data_header,
    _validate_score_specs,
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

# The tabix table filename used when a table score is realized as tabix
# (``.txt.gz`` + ``.tbi``) instead of the plain ``.txt`` default.
_TABIX_FILENAME = "data.txt.gz"


_CHROM_MAPPING_FILENAME = "chrom_map.txt"


# The ``header_mode`` values a table score may declare.  ``"file"`` is the
# backend default and the builder's default realize path: the authored header
# line is written into the data file and read back from it.  ``"none"`` and
# ``"list"`` both realize a HEADERLESS data file and describe the columns in
# the config instead.
_HEADER_MODES = ("file", "none", "list")


# The base position columns :meth:`_TableScoreBuilder.with_position_column`
# may map explicitly onto a data column.
_POSITION_COLUMNS = ("chrom", "pos_begin", "pos_end")


@dataclasses.dataclass(frozen=True)
class _ColumnAddress:
    """An explicit data-column address: by name or by 0-based index."""

    column_name: str | None = None
    column_index: int | None = None

    def render(self) -> str:
        """The ``column_name:``/``column_index:`` line of a column entry."""
        if self.column_index is not None:
            return f"column_index: {self.column_index}"
        return f"column_name: {self.column_name}"


@dataclasses.dataclass(frozen=True)
class _TableScoreBuilder(ExtraFilesMixin, MetaMixin):
    """Immutable base for the tabular position/allele score builders.

    The three table-score resource types share nearly everything: score
    declaration (:class:`ScoreSpec`), header validation, YAML rendering,
    the ``with_data`` / typed ``with_score_line`` authoring modes and the
    plain-``.txt`` / tabix realize paths.  They differ only in a handful of
    class-level knobs supplied by each subclass:

    * ``SCORE_TYPE`` -- the ``type:`` config value.
    * ``TRAILING_COLUMNS`` -- extra required base columns after the position
      columns (``reference``/``alternative`` for an allele score; none for
      position).
    * ``TABLE_EXTRA_CONFIG`` -- extra lines spliced into the ``table:`` block
      (the ``reference``/``alternative`` name mapping for an allele
      score).  Under
      ``header_mode: none`` there is no header for a name to match, so those
      base columns are rendered by index instead -- see
      :meth:`with_header_mode`.
    * ``DEFAULT_DATA`` -- the bare-builder default data block.

    Every table builder can also map a position column explicitly
    (:meth:`with_position_column`) and declare a ``default_annotation:``
    list (:meth:`with_default_annotation`).
    """

    scores: tuple[ScoreSpec, ...] = ()
    data: str | None = None
    rows: tuple[tuple[tuple[str, str], ...], ...] = ()
    tabix: bool = False
    csi: bool = False  # only meaningful with ``tabix``
    # The index name to declare as ``index_filename:`` in the table config
    # and realize under -- conventional or not; only meaningful with
    # ``tabix``.
    index_filename: str | None = None
    # Leaves a copy of the index at its conventional name too; REQUIRES an
    # ``index_filename`` that is not itself the conventional name, since it
    # is the SECOND name that makes two indices possible.
    keep_conventional_index: bool = False
    chrom_mapping: dict[str, Any] | None = None
    # The ``chrom -> file_chrom`` rows of a mapping FILE, realized beside
    # the data as ``_CHROM_MAPPING_FILENAME``; see
    # :meth:`with_chrom_mapping_file`.
    chrom_mapping_rows: tuple[tuple[str, str], ...] | None = None
    zero_based: bool = False
    header_mode: str | None = None
    # Suppresses the ``header_mode:`` key while keeping everything else the
    # ``"none"`` mode realizes -- see :meth:`with_missing_header_mode`.
    omit_header_mode: bool = False
    # Overrides ``SCORE_TYPE``; see
    # :meth:`FragmentScoreBuilder.with_resource_type`.
    resource_type: str | None = None
    # Trailing key columns to leave OUT of the table entirely; see
    # :meth:`without_key_columns`.
    dropped_key_columns: frozenset[str] = frozenset()
    # Explicit ``chrom``/``pos_begin``/``pos_end`` addresses, in declaration
    # order; see :meth:`with_position_column`.
    position_columns: tuple[tuple[str, _ColumnAddress], ...] = ()
    # The top-level ``default_annotation:`` list; ``None`` emits no key.  See
    # :meth:`with_default_annotation`.
    default_annotation: tuple[dict[str, Any], ...] | None = None

    # Subclass-provided knobs.
    SCORE_TYPE: ClassVar[str] = ""
    LEADING_COLUMNS: ClassVar[tuple[str, ...]] = ("chrom", "pos_begin")
    TRAILING_COLUMNS: ClassVar[tuple[str, ...]] = ()
    # ``pos_end`` is an allowed optional column for EVERY table score type,
    # the allele score included: it realizes onto the same
    # genomic_position_table
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

    def without_key_columns(self, *columns: str) -> Self:
        """Build the table WITHOUT these trailing key columns.

        ``reference`` and ``alternative`` are independently optional in the
        allele-score schema, and a resource declaring neither -- or only one
        -- is a legal shape a reader has to answer for.  A bare
        :func:`an_allele_score` always renders both, so this is how a test
        reaches the shapes it otherwise cannot express.

        The dropped column disappears from the required data header, from the
        rendered ``table:`` block and from the index-addressed column
        mappings alike, so the resource is consistent rather than merely
        missing a config line.  Supply :meth:`with_data` to match: the
        default data block carries the columns this removes.

        Accumulates, so two calls drop two columns rather than the second
        forgetting the first.
        """
        unknown = set(columns) - set(self.TRAILING_COLUMNS)
        if unknown:
            raise ResourceValidationError(
                f"without_key_columns: {sorted(unknown)} are not key columns "
                f"of a {self.SCORE_TYPE}; it has "
                f"{list(self.TRAILING_COLUMNS)}")
        return dataclasses.replace(
            self, dropped_key_columns=self.dropped_key_columns | set(columns))

    def with_position_column(
        self, column: str, *,
        column_name: str | None = None, column_index: int | None = None,
    ) -> Self:
        """Map a base position column onto a data column explicitly.

        ``column`` is ``chrom``, ``pos_begin`` or ``pos_end``.  Address the
        data column by ``column_name`` (a header name other than the base
        name) or by its 0-based ``column_index`` in the authored header --
        exactly one of the two, as with :meth:`with_score`.  A later call
        for the same ``column`` replaces the earlier one.

        The mapping is a SECOND description of the columns, beside the
        authored header the builder otherwise derives everything from.
        That is acceptable because tests need column names and orders the
        base names cannot express, and it is kept from drifting by being
        the one declaration for its column: the rendered ``table:`` entry,
        the tabix ``seq_col``/``start_col``/``end_col`` and the header
        validation all come from it.  The header must carry the mapped
        name instead of the base one; an index-mapped column is not looked
        up by name at all.

        Under ``header_mode: none`` there is no header to resolve a name
        against, so a ``column_name`` mapping is refused at realize time;
        a ``column_index`` mapping replaces the index the header gives.
        :meth:`with_score_line` honours a name mapping (rows are keyed by
        the mapped name) and refuses an index mapping its synthesized
        header does not agree with.
        """
        if column not in _POSITION_COLUMNS:
            raise ResourceValidationError(
                f"with_position_column: {column!r} is not a position "
                f"column; expected one of {list(_POSITION_COLUMNS)}")
        if column_name is not None and column_index is not None:
            raise ResourceValidationError(
                f"position column {column!r}: column_name and column_index "
                f"are mutually exclusive; address the column one way or "
                f"the other")
        if column_name is None and column_index is None:
            raise ResourceValidationError(
                f"position column {column!r}: pass column_name or "
                f"column_index to address the data column")
        if column_index is not None and column_index < 0:
            raise ResourceValidationError(
                f"position column {column!r}: column_index must be "
                f"non-negative, got {column_index}")
        address = _ColumnAddress(
            column_name=column_name, column_index=column_index)
        return dataclasses.replace(
            self,
            position_columns=(
                *((col, addr) for col, addr in self.position_columns
                  if col != column),
                (column, address),
            ))

    def with_default_annotation(
        self, attributes: list[dict[str, Any]],
    ) -> Self:
        """Emit a top-level ``default_annotation:`` list.

        Each entry is an attribute mapping such as
        ``{"source": "phastCons", "name": "phast"}``, emitted verbatim; the
        score reads the list back through
        ``get_default_annotation_attributes()``.  An empty list is a legal
        value -- a score that annotates nothing by default.  Replaces any
        earlier list.
        """
        return dataclasses.replace(
            self, default_annotation=tuple(copy.deepcopy(attributes)))

    def _position_header_columns(
        self, header: list[str],
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """The header names the position columns occupy: required, optional.

        An unmapped column is looked up by its base name (``pos_end`` is
        optional); a name-mapped one by the mapped name; an index-mapped
        one occupies whatever name the header has at that index.
        """
        mapped = dict(self.position_columns)
        required: list[str] = []
        optional: list[str] = []
        for column in _POSITION_COLUMNS:
            address = mapped.get(column)
            if address is None:
                if column in self.LEADING_COLUMNS:
                    required.append(column)
                elif column in self.OPTIONAL_COLUMNS:
                    optional.append(column)
            elif address.column_name is not None:
                required.append(address.column_name)
            else:
                assert address.column_index is not None
                if address.column_index >= len(header):
                    raise ResourceValidationError(
                        f"position column {column!r}: column_index "
                        f"{address.column_index} is out of range for a "
                        f"{len(header)}-column header {header}")
                required.append(header[address.column_index])
        return tuple(required), tuple(optional)

    def _position_column_indexes(self, header: list[str]) -> dict[str, int]:
        """The header index of each position column the table carries."""
        mapped = dict(self.position_columns)
        indexes: dict[str, int] = {}
        for column in _POSITION_COLUMNS:
            address = mapped.get(column)
            if address is None:
                if column in header:
                    indexes[column] = header.index(column)
            elif address.column_index is not None:
                indexes[column] = address.column_index
            else:
                assert address.column_name is not None
                indexes[column] = header.index(address.column_name)
        return indexes

    @property
    def _effective_trailing_columns(self) -> tuple[str, ...]:
        """``TRAILING_COLUMNS`` less whatever was dropped."""
        return tuple(
            column for column in self.TRAILING_COLUMNS
            if column not in self.dropped_key_columns)

    def _effective_table_extra_config(self) -> str:
        """The ``table:`` extra block for the columns still present.

        Byte-identical to the ``TABLE_EXTRA_CONFIG`` knob while nothing is
        dropped -- the derived path exists only for the shapes
        :meth:`without_key_columns` reaches.
        """
        if not self.dropped_key_columns:
            return self.TABLE_EXTRA_CONFIG
        return "".join(
            f"    {column}:\n      name: {column}\n"
            for column in self._effective_trailing_columns)

    def with_chrom_mapping(self, **mapping: Any) -> Self:
        """Emit a ``chrom_mapping:`` block in the ``table:`` config.

        Keys are passed through verbatim, e.g.
        ``with_chrom_mapping(add_prefix="chr")``.  For a mapping shipped as
        a FILE use :meth:`with_chrom_mapping_file`, which writes it too.
        """
        return dataclasses.replace(self, chrom_mapping=dict(mapping))

    def with_chrom_mapping_file(self, **file_chrom_by_chrom: str) -> Self:
        """Ship a ``chrom -> file_chrom`` mapping file the config points at.

        ``with_chrom_mapping_file(kept="chr1", empty="chr99")`` realizes a
        two-column ``chrom\\tfile_chrom`` file beside the data and emits
        ``chrom_mapping: {filename: ...}`` for it.  A chromosome mapped onto
        a file contig the data does not carry is the way to author a
        listed-but-empty contig -- the shape the in-memory backend answers
        ``ContigExtent.EMPTY`` for (gain#509).
        """
        return dataclasses.replace(
            self,
            chrom_mapping={"filename": _CHROM_MAPPING_FILENAME},
            chrom_mapping_rows=tuple(file_chrom_by_chrom.items()))

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
        even in the modes where it is not written into the file.  A position
        column mapped by :meth:`with_position_column` takes its index from
        that mapping instead.

        With ``"none"`` there is no header to resolve a name against, so
        every declared score must address its column by ``column_index``
        (:meth:`with_score`), and a position column may be mapped by
        ``column_index`` only; a name-addressed one is rejected at realize
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

    def with_aggregator(
        self, aggregator: str, *, score_id: str | None = None,
    ) -> Self:
        """Declare the score's default aggregator.

        Emitted under ``aggregator:`` -- the resource-level default a pipeline
        uses when an attribute does not name one of its own.  A score has one
        aggregator; which reduction it names is fixed by the resource type
        (over a region of positions, over the alleles at one, over
        overlapping fragments), so there is nothing to choose between here.
        With ``score_id`` omitted it attaches to the most-recently-declared
        score.  The value is
        rendered verbatim, so an invalid one can be authored on purpose to
        watch the resource schema reject it.
        """
        return dataclasses.replace(
            self,
            scores=set_aggregator(
                self.scores, aggregator, score_id=score_id),
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

    def with_tabix(
        self, *, csi: bool = False, index_filename: str | None = None,
        keep_conventional_index: bool = False,
    ) -> Self:
        """Realize the score table as tabix (``.txt.gz`` + ``.tbi``).

        The default is a plain ``.txt`` table; ``with_tabix`` switches the
        realize path to :func:`setup_tabix` and points ``table.filename`` at
        the ``.txt.gz`` with ``format: tabix``.  The resource reads back
        identically to the plain form.  With ``csi=True`` the index is a
        ``.csi`` -- the flavour a contig beyond tabix's ~512 Mbp limit
        requires -- rather than the default ``.tbi``.

        ``index_filename`` realizes the index under a name of the caller's
        choosing and declares it as ``index_filename:`` in the table
        config, instead of leaving it at the conventional
        ``<data-filename>.tbi`` / ``.csi``.  Pass a NON-adjacent name (e.g.
        ``data.custom.tbi``) whenever the test is about index resolution:
        on a local filesystem htslib auto-probes for an adjacent index, so
        an index left at its conventional name proves nothing about which
        name the code resolved.

        ``keep_conventional_index`` additionally leaves a copy of the index
        at the conventional name, so the resource carries BOTH.  That is the
        shape that tells "the configured name wins" apart from "the
        conventional probe happened to find the only index there was".  It
        REQUIRES a SECOND name to realize the configured index under, so it
        is rejected both without an ``index_filename`` and with one that
        names the conventional index itself -- neither can carry two
        indices, and quietly realizing one instead is what makes a test
        assert the two-index shape while exercising the one-index shape.

        An ``index_filename`` that IS the conventional name, without the
        flag, is accepted and yields that single index.

        Precondition: the authored rows (via :meth:`with_data` or
        :meth:`with_score_line`) must be position-sorted -- ascending by
        chrom then pos_begin -- when ``with_tabix`` is used, because
        ``pysam.tabix_index`` requires sorted input and otherwise fails
        loudly with an ``OSError`` un-annotated by the resource id.
        """
        return dataclasses.replace(
            self, tabix=True, csi=csi, index_filename=index_filename,
            keep_conventional_index=keep_conventional_index)

    def realize_into(self, resource_dir: pathlib.Path) -> None:
        """Write this table-score resource into ``resource_dir``.

        Raises a ``ResourceValidationError`` on invalid content;
        ``GRRBuilder`` annotates it with the resource id.
        """
        scores = scores_or_default(self.scores)
        data = self._effective_data(scores)
        _validate_score_specs(scores)
        header = _parse_header(data)
        position_required, position_optional = (
            self._position_header_columns(header))
        _validate_data_header(
            data, scores,
            base_required=(
                position_required + self._effective_trailing_columns),
            base_optional=position_optional)
        self._validate_header_mode(scores)
        self._validate_distinct_position_columns(header)
        self._validate_index_options()
        # The authored header -- plus any explicit position mapping -- is
        # the single column declaration; the modes that do not write the
        # header into the file still resolve their indices from it.
        write_header = self._effective_header_mode() == "file"
        sidecars = self._render_chrom_mapping_file()
        if self.tabix:
            setup_directories(resource_dir, {
                GR_CONF_FILE_NAME: self._render_config(
                    scores, _TABIX_FILENAME, data),
                **sidecars,
            })
            _realize_tabix_table(
                resource_dir / _TABIX_FILENAME, data,
                self._position_column_indexes(header),
                write_header=write_header, csi=self.csi,
                index_filename=self.index_filename,
                keep_conventional_index=self.keep_conventional_index)
        else:
            file_data = data if write_header else _strip_header(data)
            setup_directories(resource_dir, {
                GR_CONF_FILE_NAME: self._render_config(
                    scores, _DATA_FILENAME, data),
                _DATA_FILENAME: convert_to_tab_separated(file_data),
                **sidecars,
            })
        self.realize_files_into(resource_dir)

    def _render_chrom_mapping_file(self) -> dict[str, str]:
        """The mapping file :meth:`with_chrom_mapping_file` ships, if any."""
        if self.chrom_mapping_rows is None:
            return {}
        lines = ["chrom\tfile_chrom"]
        lines.extend(
            f"{chrom}\t{file_chrom}"
            for chrom, file_chrom in self.chrom_mapping_rows)
        return {_CHROM_MAPPING_FILENAME: "\n".join(lines) + "\n"}

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
        named_positions = [
            column for column, address in self.position_columns
            if address.column_name is not None
        ]
        if named_positions:
            raise ResourceValidationError(
                f"header_mode 'none' leaves no header to resolve a column "
                f"name against; position column(s) {sorted(named_positions)} "
                f"must be mapped with column_index")

    def _validate_distinct_position_columns(self, header: list[str]) -> None:
        """Reject two position or key columns resolving to one data column.

        The key columns (an allele score's ``reference`` and
        ``alternative``) take part too: a position column may share a data
        column with a score, never with a key.  The set-based header check
        cannot see the collision -- the shared name simply appears once --
        so without this the builder would realize a config and a tabix
        index in which both columns read the same field.
        """
        by_index: dict[int, list[str]] = {}
        for column, index in self._position_column_indexes(header).items():
            by_index.setdefault(index, []).append(column)
        keys = self._effective_trailing_columns
        for column in keys:
            by_index.setdefault(header.index(column), []).append(column)
        for index, columns in sorted(by_index.items()):
            if len(columns) > 1:
                kind = (
                    "position and key columns"
                    if any(column in keys for column in columns)
                    else "position columns")
                raise ResourceValidationError(
                    f"{kind} {columns} resolve to the same data "
                    f"column, index {index} ({header[index]!r}); map each "
                    f"to a distinct column")

    def _validate_index_options(self) -> None:
        """Reject a keep-the-conventional-index request with no second name.

        ``keep_conventional_index`` asks for BOTH index names to be present,
        which needs a second name -- ``index_filename`` -- to realize the
        configured index under.  Alone the flag has nowhere to put that
        second index, so honouring it would silently realize the one-index
        shape and leave a test asserting the two-index shape passing for
        the wrong reason.

        The companion case -- an ``index_filename`` that names the
        conventional index itself, which is a name but not a second one --
        is caught during realize, where the name pysam actually wrote is
        known and the two can be compared by identity rather than spelling.
        """
        if self.keep_conventional_index and self.index_filename is None:
            raise ResourceValidationError(
                "keep_conventional_index asks for the index at BOTH the "
                "configured and the conventional name, so it requires an "
                "index_filename to realize the configured one under; "
                "pass index_filename= or drop the flag")

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
        header = self._synthesize_header(scores, row_dicts)
        return self._render_rows(header, row_dicts)

    def _synthesize_header(
        self, scores: tuple[ScoreSpec, ...], row_dicts: list[dict[str, str]],
    ) -> list[str]:
        """Lay out the header of the rows of :meth:`with_score_line`."""
        # A name-mapped position column is keyed -- and synthesized -- by
        # its mapped name.
        header_name = {
            column: address.column_name
            for column, address in self.position_columns
            if address.column_name is not None
        }
        pos_end = header_name.get("pos_end", "pos_end")
        uses_pos_end = any(pos_end in rd for rd in row_dicts)
        if uses_pos_end and not all(pos_end in rd for rd in row_dicts):
            raise ResourceValidationError(
                f"with_score_line: {pos_end!r} must be given on every row "
                f"or on none")
        header = [
            header_name.get(column, column) for column in self.LEADING_COLUMNS
        ]
        if uses_pos_end:
            header.append(pos_end)
        header.extend(self._effective_trailing_columns)
        header.extend(
            spec.column_name for spec in scores
            if spec.column_name is not None
        )
        self._check_index_mappings_agree(header)
        return header

    @staticmethod
    def _render_rows(
        header: list[str], row_dicts: list[dict[str, str]],
    ) -> str:
        """Render ``header`` and the rows as a whitespace data block."""
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

    def _check_index_mappings_agree(self, header: list[str]) -> None:
        """Refuse an index mapping the synthesized ``header`` contradicts.

        :meth:`with_score_line` lays the columns out itself, so an explicit
        ``column_index`` either names the position the column landed at or
        disagrees with it -- and a header that disagrees with the config is
        never realized.
        """
        for column, address in self.position_columns:
            if address.column_index is None:
                continue
            landed_at = header.index(column) if column in header else None
            if landed_at == address.column_index:
                continue
            landed = (
                f"at index {landed_at}" if landed_at is not None
                else "nowhere (no row carries it)")
            raise ResourceValidationError(
                f"with_score_line cannot synthesize a header that agrees "
                f"with position column {column!r} mapped to column_index "
                f"{address.column_index}: it lays the column out {landed} "
                f"in {header}; use with_data instead")

    def _render_config(
        self, scores: tuple[ScoreSpec, ...], filename: str, data: str,
    ) -> str:
        header = _parse_header(data)
        lines = self._render_config_head(filename, header)
        lines.extend(self._render_config_tail())
        config = "\n".join(lines) + "\n"
        if self._effective_header_mode() == "none":
            # No header to match a column name against, so the base columns
            # are addressed by index -- resolved from the same authored
            # header the tabix index columns come from.
            config += self._render_column_indexes(header)
        else:
            config += self._render_position_columns()
            config += self._effective_table_extra_config()
        config += "scores:\n" + render_score_specs_yaml(scores)
        if self.default_annotation is not None:
            config += yaml.safe_dump(
                {"default_annotation": list(self.default_annotation)},
                default_flow_style=False, sort_keys=False)
        return config + self.render_meta()

    def _render_config_head(
        self, filename: str, header: list[str],
    ) -> list[str]:
        """Render the config lines from ``type:`` through ``header:``."""
        lines = [
            f"type: {self.resource_type or self.SCORE_TYPE}",
            "table:",
            f"    filename: {filename}",
        ]
        if self.tabix:
            lines.append("    format: tabix")
        if self.index_filename is not None:
            lines.append(f"    index_filename: {self.index_filename}")
        if self.header_mode is not None and not self.omit_header_mode:
            lines.append(f"    header_mode: {self.header_mode}")
        if self.header_mode == "list":
            lines.append("    header:")
            lines.extend(f"    - {column}" for column in header)
        return lines

    def _render_config_tail(self) -> list[str]:
        """Render the ``zero_based`` and the ``chrom_mapping`` lines."""
        lines: list[str] = []
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
        return lines

    def _render_position_columns(self) -> str:
        """Render the explicit position mappings as ``table:`` entries."""
        return "".join(
            f"    {column}:\n        {address.render()}\n"
            for column, address in self.position_columns)

    def _render_column_indexes(self, header: list[str]) -> str:
        """Render the base columns as explicit ``column_index:`` mappings.

        A position column mapped by :meth:`with_position_column` renders
        its explicit index; every other base column the index the authored
        header gives it.
        """
        indexes = {
            **self._position_column_indexes(header),
            **{
                column: header.index(column)
                for column in self._effective_trailing_columns
            },
        }
        lines: list[str] = []
        for column, index in indexes.items():
            lines.extend((
                f"    {column}:",
                f"        column_index: {index}",
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
# block of an allele score config; it locates its ref/alt columns
# by name (matching the ``reference``/``alternative`` data columns).
_REF_ALT_TABLE_CONFIG = (
    "    reference:\n"
    "      name: reference\n"
    "    alternative:\n"
    "      name: alternative\n"
)


# allele default data: chrom/pos_begin plus the ref/alt columns and one
# default float score.
_ALLELE_DEFAULT_DATA = """
    chrom  pos_begin  reference  alternative  score
    1      10         A          G            0.1
    1      10         A          C            0.2
    1      16         C          T            0.3
"""


@dataclasses.dataclass(frozen=True)
class AlleleScoreBuilder(_TableScoreBuilder):
    """Immutable builder for a single ``allele_score`` resource.

    Requires ``reference``/``alternative`` columns and reads back through
    ``AlleleScore``.

    The resource it emits declares no ``allele_score_mode`` and the
    builder has no knob for it, so it always reads in the default
    ``alleles`` mode.  The allele score annotator routes on that mode --
    on a ``substitutions`` resource any allele other than a substitution
    is region-folded rather than matched exactly -- so a fixture that
    needs a ``substitutions`` resource cannot come from this builder;
    write its ``genomic_resource.yaml`` by hand.
    """

    # The top-level ``allele_multiplicity:`` value; ``None`` emits no key.
    allele_multiplicity: Any = None

    SCORE_TYPE: ClassVar[str] = "allele_score"
    TRAILING_COLUMNS: ClassVar[tuple[str, ...]] = ("reference", "alternative")
    TABLE_EXTRA_CONFIG: ClassVar[str] = _REF_ALT_TABLE_CONFIG
    DEFAULT_DATA: ClassVar[str] = _ALLELE_DEFAULT_DATA

    def with_allele_multiplicity(self, multiplicity: Any) -> Self:
        """Emit a top-level ``allele_multiplicity:`` (gain#1750).

        Rendered through ``yaml.safe_dump``, so a non-string value --
        ``True``, ``2`` -- arrives as that YAML type, which is how the
        schema's string-only rule is watched refusing one.
        """
        return dataclasses.replace(
            self, allele_multiplicity=multiplicity)

    def _render_config(
        self, scores: tuple[ScoreSpec, ...], filename: str, data: str,
    ) -> str:
        config = super()._render_config(scores, filename, data)
        if self.allele_multiplicity is None:
            return config
        return config + yaml.safe_dump(
            {"allele_multiplicity": self.allele_multiplicity},
            default_flow_style=False)


@dataclasses.dataclass(frozen=True)
class FragmentScoreBuilder(_TableScoreBuilder):
    """Immutable builder for a single fragment score resource.

    Shares the tabular-score machinery with the position/allele
    builders, differing only in the type value.  A fragment is a region
    rather than a point, so the default data carries the optional
    ``pos_end`` column.  Reads back through ``FragmentScore``, which
    weights every record 1 however long it is.
    """

    SCORE_TYPE: ClassVar[str] = "fragment_score"
    DEFAULT_DATA: ClassVar[str] = """
        chrom  pos_begin  pos_end  score
        1      10         19       0.1
        1      20         200      0.2
    """

    def with_resource_type(self, resource_type: str) -> Self:
        """Render ``fragment_score`` or ``cnv_collection`` as the ``type:``.

        A bare builder already renders ``fragment_score``, the preferred
        spelling; reach for this to pin the legacy ``cnv_collection``.
        Raises if ``resource_type`` names no fragment score.  Only a
        fragment score has two spellings, hence not on the base.
        """
        return dataclasses.replace(
            self, resource_type=require_fragment_score_type(resource_type))


def _realize_tabix_table(
    tabix_path: pathlib.Path, data: str,
    position_indexes: dict[str, int], *,
    write_header: bool = True, csi: bool = False,
    index_filename: str | None = None,
    keep_conventional_index: bool = False) -> None:
    """Realize ``data`` as a tabix table (``.txt.gz`` + its index).

    With ``write_header`` (the default, the ``header_mode: file`` path) the
    header line is emitted as a ``#`` comment so the tabix table reads its
    column names from the file; ``_render_config`` then emits no
    ``header_mode`` key, relying on the tabix backend's default.  Without
    it (``header_mode`` ``none``/``list``) the header line is dropped and
    the realized file carries data rows only.

    Either way the seq/start/end columns are ``position_indexes`` -- the
    builder's resolution of the SAME authored header (and explicit position
    mappings) the config is rendered from -- so an arbitrary column order
    or naming still indexes correctly and the two cannot drift.

    With ``index_filename`` the index pysam wrote at its conventional name
    is MOVED to that name, so the resource carries the index only under the
    non-conventional name -- no adjacent sidecar is left behind for
    htslib's auto-probe to find.  ``keep_conventional_index`` copies it
    instead, leaving the conventional sidecar in place as well -- and is
    refused when ``index_filename`` names that very index, because one file
    cannot be two.  Without the flag such a name is simply already where it
    was asked to be, so nothing is moved.
    """
    chrom_col = position_indexes["chrom"]
    start_col = position_indexes["pos_begin"]
    end_col = position_indexes.get("pos_end", start_col)
    content = _comment_header(data) if write_header else _strip_header(data)
    _, written_index = setup_tabix(
        tabix_path, content,
        seq_col=chrom_col, start_col=start_col, end_col=end_col, csi=csi)
    if index_filename is not None:
        written = pathlib.Path(written_index)
        target = tabix_path.parent / index_filename
        # Compare by identity, not by spelling: ``./data.txt.gz.tbi`` and
        # ``../<dir>/data.txt.gz.tbi`` name the file pysam already wrote
        # while comparing unequal to it, and acting on one path as both
        # source and destination is a no-op for rename but a
        # SameFileError for copy.
        same_file = target == written or (
            target.exists() and target.samefile(written))
        if same_file and keep_conventional_index:
            raise ResourceValidationError(
                f"keep_conventional_index needs a SECOND name to realize "
                f"the configured index under, but index_filename "
                f"{index_filename!r} already names the conventional index "
                f"{written.name!r}; pass a non-conventional "
                f"index_filename or drop the flag")
        if same_file:
            # Already exactly where it was asked to be.
            return
        if keep_conventional_index:
            shutil.copyfile(written, target)
        else:
            written.rename(target)


def a_position_score() -> PositionScoreBuilder:
    """Return an immutable position-score builder."""
    return PositionScoreBuilder()


def an_allele_score() -> AlleleScoreBuilder:
    """Return an immutable allele-score builder."""
    return AlleleScoreBuilder()


def a_fragment_score() -> FragmentScoreBuilder:
    """Return an immutable fragment-score builder."""
    return FragmentScoreBuilder()
