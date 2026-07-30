"""What a genomic score DEFINITION is, and how it reads a value.

The bottom of the score layer: a score's definition, the vocabulary it parses
with (value types, NA sentinels, column addressing), and the read that turns a
record's cell into a value.  Nothing here knows about a ``GenomicScore``, a
resource or a fetch -- which is what lets ``vcf_scores`` sit above this module
and ``genomic_scores`` above both, with no cycle between them.

Split out of ``genomic_scores`` so that the VCF-specific score code could be
gathered into one module: ``vcf_scores`` constructs ``GenomicScoreDef`` at
runtime, and ``genomic_scores`` imports what ``vcf_scores`` builds, so the two
could not both live in one file without importing each other.
"""
from __future__ import annotations

import contextlib
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from gain import logging
from gain.genomic_resources.genomic_position_table.record import (
    PAYLOAD,
    Record,
)
from gain.genomic_resources.score_resource import ScoreDef

logger = logging.getLogger(__name__)

ScoreValue = str | int | float | bool | None

SCORE_TYPE_PARSERS = {
    "str": str,
    "float": float,
    "int": int,
    "bool": bool,
}

_DEFAULT_NA_VALUES: dict[str, tuple[str, ...]] = {
    "str": (),
    "float": ("", "nan", ".", "NA"),
    "int": ("", "nan", ".", "NA"),
    "bool": (),
}

# Value types whose text sentinels are also coerced to the parsed representation
# so a numeric raw payload (e.g. a bigWig ``float``) matches by value, not text.
_NA_COERCIBLE_TYPES = ("int", "float")

# Value types ``GenomicScoreDef.parse_array`` defines a column parse for, and
# so the ones a bulk column read can serve.  ``bool`` is absent because no
# column consumer asks for it.
BULK_PARSEABLE_VALUE_TYPES = ("float", "int", "str")


def normalize_na_values(na_values: Any, value_type: str) -> set[Any]:
    """Normalize a configured ``na_values`` into a type-aware sentinel set.

    The resource schema permits ``na_values`` as a bare scalar
    (``na_values: "-1"``) or a list.  A bare ``str`` left un-normalized turns
    the NA membership test in :func:`_extract_vcf_value` into a
    SUBSTRING test (``"1" in "-1"`` is ``True``) and raises ``TypeError`` when
    matched against a non-string raw payload (bigWig floats).  This wraps a
    scalar into a one-element collection and returns a set that carries, for
    every configured sentinel, both its text form (matched against string
    backends) and -- for numeric score types -- its parsed form (matched
    against a ``float``/``int`` raw payload).  So a sentinel is matched against
    whichever representation the incoming raw value presents, never by
    substring.

    ``na_values`` of ``None`` selects the per-value-type default set verbatim:
    the defaults are non-numeric tokens (``""``, ``"nan"``, ``"."``, ``"NA"``)
    that a numeric backend never presents as a raw value, so they are left as a
    pure-text set -- coercing them would only add a spurious parsed ``nan`` and
    change the default behaviour.

    A ``set`` input is treated as ALREADY normalized and returned as a copy
    without re-coercion, so normalization is idempotent (a fixed point).  This
    is what the VCF ``scores``-block merge path relies on: it rebuilds a
    ``GenomicScoreDef`` from an already-normalized ``na_values`` set, whose
    ``__post_init__`` re-runs this function -- a second coercion pass would
    otherwise grow the set (e.g. parsing the default ``"nan"`` text token into a
    ``float('nan')``) and silently change the statistics hash.  Config-supplied
    ``na_values`` never arrive as a ``set`` (the schema permits only ``None``,
    ``str`` or ``list``), so a ``set`` can only be a prior normalization result.
    """
    if na_values is None:
        return set(_DEFAULT_NA_VALUES.get(value_type, ()))
    if isinstance(na_values, set):
        return set(na_values)
    if isinstance(na_values, (list, tuple)):
        raw_sentinels: tuple[Any, ...] = tuple(na_values)
    else:
        # Any bare scalar -- a str ("-1"), or a non-iterable numeric sentinel
        # built in code -- is wrapped into a one-element collection, as the
        # docstring promises; iterating it directly would raise TypeError.
        raw_sentinels = (na_values,)

    sentinels: set[Any] = set()
    parser = SCORE_TYPE_PARSERS.get(value_type) \
        if value_type in _NA_COERCIBLE_TYPES else None
    for sentinel in raw_sentinels:
        text = str(sentinel)
        sentinels.add(text)
        if parser is not None:
            with contextlib.suppress(ValueError, TypeError):
                sentinels.add(parser(text))
    return sentinels


def _parse_column_address(
    score_conf: dict[str, Any],
) -> tuple[str | None, int | None]:
    """Read a score's configured column address as ``(name, index)``.

    A score is addressed either by column NAME or by column INDEX, never both
    -- the resource schema declares the two mutually exclusive, and each has a
    modern spelling (``column_name`` / ``column_index``) plus a legacy alias
    (``name`` / ``index``) that is still accepted.  Exactly one of the returned
    pair is non-``None`` for a well-formed config.

    **Index 0 is why this is a function.**  The obvious spelling of "modern key
    or legacy alias, converted" is

    .. code-block:: python

        col_index_str = conf.get("column_index") or conf.get("index")
        col_index = int(col_index_str) if col_index_str else None

    and it silently discards a legitimate ``column_index: 0``, twice over:
    ``0`` is falsy, so the ``or`` falls through to the legacy key (usually
    absent, giving ``None``), and the ternary would drop it even when reached
    directly.  Both tests have to be ``is None`` / ``is not None``, because the
    value being looked for is itself falsy.

    That was a real defect, not a hypothetical: with both keys discarded the
    score def carried ``col_index=None`` AND ``col_name=None``, so ``open()``
    took its by-name branch and died on a message-less assertion naming
    neither the resource nor the score.  Column 0 is a legal address -- the
    validator explicitly permits ``0 <= column_index`` -- so any resource
    whose score sits in the first column could not be opened at all.

    This lives at module level, not on a class, because ``FragmentScore``
    overrides ``_parse_scoredef_config`` with its own near-copy; parsed in one
    place, the two cannot drift, and the bug above cannot be fixed in one of
    them only.  (Pinned by test_column_index_zero_is_a_real_address.)
    """
    col_name = score_conf.get("column_name")
    if col_name is None:
        col_name = score_conf.get("name")

    col_index_raw = score_conf.get("column_index")
    if col_index_raw is None:
        col_index_raw = score_conf.get("index")
    col_index = int(col_index_raw) if col_index_raw is not None else None

    return col_name, col_index


@dataclass
class GenomicScoreDef(ScoreDef):
    """A genomic score definition. Includes backend loading internals.

    Extends the shared :class:`ScoreDef` (score id, value type, description
    and histogram config) with the concerns that are genomic-only: the
    per-position and per-allele default aggregators, and the internal column
    addressing / parsing state used when reading a value off a table backend.
    """

    # pylint: disable=too-many-instance-attributes
    # How a caller that reads SEVERAL values for one annotatable reduces them
    # to one -- a region of positions, the alleles at a position, the
    # fragments overlapping a span.  A valid aggregator type, or ``None`` until
    # ``GenomicScore._build_scoredefs`` fills the resource type's default.
    #
    # There were two of these, ``pos_aggregator`` and ``allele_aggregator``,
    # and every def carried both because the definition cannot know which
    # kind of score it belongs to.  Only ever ONE was read: a position score
    # is only read by the position annotator, an allele or np score only by
    # the allele annotator, a cnv_collection only by the fragment score
    # annotator.  So the second field was dead on every def, and the config
    # surface offered
    # a key that did nothing (``position_aggregator`` on an allele score was
    # accepted by the schema and consulted by nothing).
    aggregator: str | None

    col_name: str | None                       # internal
    col_index: int | None                      # internal

    value_parser: Any                             # internal
    na_values: Any                                # internal
    # The resolved payload column this score is read from, filled in by
    # ``GenomicScore.open`` -- the single form the read path uses, as against
    # ``col_name``/``col_index``, which are the two forms a config may state.
    #
    # ``init=False`` with no default, so the attribute does not EXIST until
    # open() resolves it.  That is the honest encoding of "not resolved yet":
    # reading it early raises ``AttributeError`` naming the attribute, where a
    # sentinel would have to be a real int -- and every candidate is a valid
    # index into a payload (``-1`` most treacherously, since it would quietly
    # read the last column instead of failing).
    #
    # Only column-addressed backends set it.  A VCF score is addressed by INFO
    # *name*, which is ``col_name``, and ``_extract_vcf_value`` reads that
    # directly;
    # a VCF score def therefore never has this attribute at all.  Nothing else
    # reads it: ``fetch_region_value_arrays`` does, but the VCF backend does
    # not serve that call (``supports_region_value_arrays``).
    score_index: int = field(init=False)        # internal

    def __post_init__(self) -> None:
        # The aggregator default is deliberately NOT resolved here.  It
        # depends on how the score is aggregated -- ``mean`` over a region of
        # positions, ``max`` over the alleles at one -- which is a property of
        # the resource TYPE, and a definition does not know its own.  Filling
        # it here is what forced two fields; ``GenomicScore._build_scoredefs``
        # fills the one field from its class's table instead.
        if self.value_type is None:
            return
        self.na_values = normalize_na_values(
            self.na_values, self.value_type)

    def parse_value(self, value: str | int | float | None) -> ScoreValue:
        """Turn one raw cell into this score's value.

        ``None`` for a null raw value (an absent VCF INFO key), for a
        configured NA sentinel, and for a cell that fails to parse -- a bad
        cell is logged and skipped rather than aborting a whole scan.

        The scalar half of this definition's parsing contract; the column half
        is :meth:`parse_array`.  Both live here, on the object that owns the
        two inputs they need (``value_parser`` and ``na_values``), so neither
        can be changed against a config the other did not see.
        """
        if value is None or value in self.na_values:
            return None
        if self.value_parser is None:
            return value
        # pylint: disable=broad-except
        try:  # Temporary workaround for GRR generation
            parsed: ScoreValue = self.value_parser(value)
        except Exception:
            logger.exception(
                "unable to parse value %s for score %s",
                value, self.score_id)
            return None
        return parsed

    def _na_mask(self, cells: np.ndarray) -> np.ndarray:
        """Which cells are configured NA sentinels, vectorized.

        The array form of ``value in self.na_values``, and it has to be built
        by hand because ``np.isin(cells, list(self.na_values))`` is NOT that
        test.  ``na_values`` deliberately holds BOTH representations of each
        sentinel (``na_values: "-1"`` normalizes to ``{"-1", -1.0}``, so that a
        sentinel matches whichever form the backend presents), and handing that
        mixed list to numpy makes it coerce the lot to one dtype -- which broke
        both branches in opposite directions:

        * text cells: the float ``-1.0`` was stringified to ``"-1.0"``, so that
          spelling became an NA token the scalar test never treats as one, and
          real values were dropped;
        * float cells: every sentinel became a string, so ``isin`` compared
          float64 against ``<U32`` and was ALWAYS False -- the NA config simply
          did not apply, and a declared non-value was binned as real data.

        So each sentinel is matched against the representation the cells
        actually carry, which is what ``normalize_na_values`` stores both
        forms for in the first place.  (``pd.Series.isin``, which this replaced
        in gain#385, is hash-based and had neither problem; the coercion came
        in with the switch to numpy.)
        """
        if cells.dtype.kind == "f":
            numeric = np.array(
                [value for value in self.na_values
                 if not isinstance(value, str)],
                dtype=np.float64)
            return np.isin(cells, numeric)
        text = np.array(
            [value for value in self.na_values if isinstance(value, str)],
            dtype=object)
        return np.isin(cells, text)

    def parse_array(self, cells: np.ndarray) -> np.ndarray:
        """Turn a whole column of raw cells into values, vectorized.

        The column half of this definition's parsing contract, and the reason
        the bulk statistics scan is worth having.  Equivalent to
        ``[parse_value(c) for c in cells]``, with the "no value" that the
        scalar contract spells ``None`` rendered in whatever form the returned
        array can carry.  That equivalence is not an aspiration:
        test_parse_array_agrees_with_parse_value_fuzz asserts it token by
        token, per value type, over several ``na_values`` configs and several
        array widths.

        The returned array is one of two shapes, chosen by ``value_type``:

        * ``float`` and ``int`` -- a ``float64`` array whose ``nan`` is the
          non-value.  A float64 array has no ``None``, and for every consumer
          of a numeric column a non-value and a nan are the same skip.
        * ``str`` -- an ``object`` array of ``str``, whose ``None`` is the
          non-value, exactly as :meth:`parse_value` returns it.

        Any other value type is refused: ``bool`` has no column consumer, and
        an unset ``value_type`` is not a parse this can define.

        **Parsed with numpy, deliberately NOT with ``pd.to_numeric``**, which
        is not correctly rounded -- it returns 9.999999999999999e-26 for
        ``1e-25`` and truncates ``0.00000071009127180852`` to ten significant
        digits.  ``ndarray.astype`` agrees with ``float()`` on every token
        tested, including the PEP-515 underscores and Unicode digits pandas
        rejects outright.
        """
        if self.value_type == "float":
            return self._parse_float_array(cells)
        if self.value_type == "int":
            return self._parse_int_array(cells)
        if self.value_type == "str":
            return self._parse_text_array(cells)
        raise TypeError(
            f"parse_array does not serve {self.value_type!r} scores; "
            f"score {self.score_id}. Ask "
            f"GenomicScore.supports_region_value_arrays before calling.")

    def _parse_float_array(self, cells: np.ndarray) -> np.ndarray:
        """A ``float`` score's column, as float64 with nan for no value."""
        if cells.dtype.kind == "f":
            # Already numeric (a bigWig payload): nothing to parse, and the
            # per-record path does not parse it either.
            values = cells.astype(np.float64, copy=True)
            values[self._na_mask(cells)] = np.nan
            return values

        raw = np.asarray(cells, dtype=object)
        na_mask = self._na_mask(raw)
        work = raw.copy()
        # Substitute a parseable stand-in for each NA cell.  This one line
        # does both jobs: it is what makes an NA cell come out as nan, AND it
        # keeps a single "." sentinel from making the bulk astype raise and
        # sending the whole batch down the per-cell path below.  There used to
        # be a second ``values[na_mask] = np.nan`` after the parse as well; it
        # could never change an outcome, and two rounds of mutation testing
        # caught the comment here describing the pair's division of labour
        # wrongly, so it is gone rather than explained a third time.
        work[na_mask] = "nan"
        try:
            values = work.astype(np.float64)
        except (TypeError, ValueError):
            values = np.empty(work.shape, dtype=np.float64)
            failed = 0
            for idx, cell in enumerate(work):
                try:
                    values[idx] = float(cell)
                except (TypeError, ValueError):
                    values[idx] = np.nan
                    failed += 1
            if failed:
                # Once per batch, with a count.  The per-record path logs a
                # traceback per bad cell, which on a corrupt column means one
                # per row; saying it once keeps the signal that the bulk path
                # used to drop entirely without reproducing that flood.
                logger.warning(
                    "unable to parse %s of %s values for score %s",
                    failed, values.size, self.score_id)
        return values

    def _parse_int_array(self, cells: np.ndarray) -> np.ndarray:
        """An ``int`` score's column, as float64 with nan for no value.

        ``int()`` semantics, not ``float()`` ones: ``"3.5"``, ``"1e3"`` and
        ``"0x10"`` are parse failures here and become nan, exactly as
        :meth:`parse_value` logs them and returns ``None``.  The fast path is
        ``astype(np.int64)``, which converts an object cell by calling
        ``int()`` on it, so it agrees with the scalar parse by construction --
        including the PEP-515 underscores.  It is all-or-nothing, so one bad
        cell sends the batch to the per-cell loop, which is what a mixed column
        needs anyway.

        The result is float64 rather than int64 because the array's non-value
        has to live somewhere, and int64 has no nan.  Values are therefore
        exact up to 2**53 and correctly rounded above it, where the per-record
        path keeps an arbitrary-precision Python ``int``; the divergence is
        visible only in a ``min_max`` extremum past 2**53, since a number
        histogram widens to float for its bin arithmetic either way.

        Past float64's range entirely -- a token of some 309 digits or more --
        the array cannot carry the value at all, and it becomes a non-value
        here where :meth:`parse_value` returns the exact ``int``.  That is the
        one place the two parses part company on whether a cell HAS a value,
        and it is counted into the batch's parse-failure warning rather than
        raised, because this runs inside a generator, outside the scan's
        per-score nullify handler: an escaping exception takes down a whole
        resource's statistics over one cell.
        """
        if cells.dtype.kind == "f":
            # A numeric payload (bigWig): the scalar parse applies int() to
            # the raw float, which truncates toward zero.  A nan or an inf is
            # not an int at all -- int() raises on both, so parse_value logs
            # and returns None -- and both must come out as the non-value
            # rather than as a bin.
            values = np.trunc(cells.astype(np.float64, copy=True))
            values[~np.isfinite(values)] = np.nan
            values[self._na_mask(cells)] = np.nan
            return values

        raw = np.asarray(cells, dtype=object)
        na_mask = self._na_mask(raw)
        values = np.full(raw.shape, np.nan, dtype=np.float64)
        work = raw[~na_mask]
        try:
            values[~na_mask] = work.astype(np.int64)
        except (TypeError, ValueError, OverflowError):
            failed = 0
            for idx, cell in zip(np.flatnonzero(~na_mask), work, strict=True):
                try:
                    # A Python int too wide for int64 still parses here and is
                    # widened on assignment, so an out-of-int64 token is a
                    # value.  Past float64's range the assignment itself
                    # raises OverflowError -- there is no float to widen it to
                    # -- which is counted as a parse failure rather than
                    # allowed to escape the scan.
                    values[idx] = int(cell)
                except (TypeError, ValueError, OverflowError):
                    values[idx] = np.nan
                    failed += 1
            if failed:
                logger.warning(
                    "unable to parse %s of %s values for score %s",
                    failed, values.size, self.score_id)
        return values

    def _parse_text_array(self, cells: np.ndarray) -> np.ndarray:
        """A ``str`` score's column, as an object array with None for no value.

        There is no vectorized ``str()``: an object array of text is already
        what the scalar parse would return for every cell, so the pass below
        coerces only what is not text yet (a numeric payload) and is otherwise
        a copy.  The win for a categorical score is not this parse -- it is
        counting a whole batch at once instead of building a ``Record`` per
        row.

        A ``str`` score's default ``na_values`` is empty, so ``""`` is a value
        here unless a config says otherwise; that is the scalar parse's rule
        too, kept by asking the same :meth:`_na_mask`.
        """
        raw = np.asarray(cells, dtype=object)
        na_mask = self._na_mask(raw)
        return np.array(
            [None if is_na else (cell if isinstance(cell, str) else str(cell))
             for is_na, cell in zip(na_mask, raw, strict=True)],
            dtype=object)


def extract_column_value(
    record: Record, score_def: GenomicScoreDef,
) -> ScoreValue:
    """Read one score off a record whose PAYLOAD is a raw row.

    The tabular backends and bigWig: a score is a CELL of the payload,
    addressed by the integer column ``GenomicScore.open`` resolved into
    ``score_index``.  Turning that cell into a value is the definition's job
    (:meth:`GenomicScoreDef.parse_value`), so that this read and the bulk
    column read cannot drift apart.

    A pure function of ``(record, score_def)`` -- it holds no state and needs
    none, which is what let the per-line score-line objects go.  There is no
    "score_index not resolved yet" check: the attribute does not exist until
    ``open()`` sets it, so an unopened def raises ``AttributeError`` naming it.
    """
    return score_def.parse_value(record[PAYLOAD][score_def.score_index])


# How a score's value is read off a record: one of these is bound per
# opened score by ``GenomicScore.open``, from the table's type, and
# called per value.
ValueExtractor = Callable[[Record, GenomicScoreDef], ScoreValue]
