"""Score aggregator classes and factory utilities."""

from __future__ import annotations

import abc
import functools
import math
import operator
import re
from collections import Counter
from collections.abc import Generator, Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar, cast

if TYPE_CHECKING:
    from gain.genomic_resources.score_def import ScoreValue


class Aggregator(abc.ABC):
    """Base class for score aggregators.

    **An accumulator is not shared.** An aggregator is mutable state, and
    every caller in gain builds a fresh one per fold: the folding reads
    build one per query per call, and the annotators that reduce their own
    values build one per attribute per call.  Nothing outlives the fold it
    was built for, and :meth:`build` is cheap enough for that to be the
    default -- a name resolves through a memo (gain#1157).

    :meth:`aggregate` still clears its state first, so an instance CAN be
    reused single-threaded.  It is not thread-safe either way: two threads
    folding through one accumulator would interleave their values.
    """

    def __init__(self) -> None:
        self.total_count = 0
        self.used_count = 0

    parametrized: ClassVar[bool] = False
    default_parameter: ClassVar[str | None] = None
    # Output value type produced by this aggregator, independent of the input
    # type. None means the output type matches the input type (e.g. max/min).
    output_value_type: ClassVar[str | None] = None

    @classmethod
    def preserves_domain(
        cls, *, value_type: str | None = None,  # ruff: ignore[unused-class-method-argument]
    ) -> bool:
        """Return True if output stays within the source value domain."""
        return False

    def __call__(self) -> Any:
        return self.get_final()

    def add(self, value: Any, count: int = 1) -> None:
        """Add a value to the aggregator, weighted by ``count``.

        ``count`` is the number of times the value is deemed to occur --
        the number of base pairs a position-score record spans, for
        instance.  ``GenomicScore.record_weight`` is where each kind
        states its own rule.  The weight is applied in closed form: adding
        a value with a weight of ``n`` produces the same result as adding
        it ``n`` times, without doing ``n`` units of work, which is what makes
        folding a region proportional to its records rather than to its
        length in base pairs.  The one exception is ``mean``, which is
        *more* accurate weighted than replicated: it rounds once per
        record rather than once per base.  See :meth:`_add_internal`.
        """
        self.total_count += count
        self._add_internal(value, count)

    def aggregate(self, values: list[Any] | None) -> Any:
        """Clear state, add all values, and return the final result."""
        self.clear()
        if values is None:
            return self.get_final()
        for value in values:
            self.add(value)
        return self.get_final()

    @abc.abstractmethod
    def _add_internal(self, value: Any, count: int) -> None:
        """Fold ``value``, occurring ``count`` times, into the state.

        Implementations must apply the weight in closed form -- never by
        looping ``count`` times -- so that aggregating a region costs one
        step per record rather than one per base pair.
        """
        raise NotImplementedError

    def clear(self) -> None:
        """Reset the aggregator to its initial state."""
        self.total_count = 0
        self.used_count = 0
        self._clear_internal()

    @abc.abstractmethod
    def _clear_internal(self) -> None:
        raise NotImplementedError

    def get_final(self) -> Any:
        """Return the aggregated result."""
        raise NotImplementedError

    def get_total_count(self) -> int:
        """Return the total weight seen, ``None`` values included."""
        return self.total_count

    def get_used_count(self) -> int:
        """Return the total weight of the non-``None`` values added.

        A weighted total, not a number of records: it is the denominator
        of the mean, so a value added with a weight of ``n`` contributes
        ``n`` to it.
        """
        return self.used_count

    def __eq__(self, obj: object) -> bool:
        return cast(bool, self.get_final() == obj)

    @staticmethod
    def build(source: AggregatorSource) -> Aggregator:
        """Build a FRESH aggregator from a definition, string, or dict.

        A string is the hot spelling: every aggregating read builds its
        accumulators anew per call, so the same few names arrive here
        millions of times per run, and parsing one was ~70% of building
        it (gain#1157).  A name is therefore resolved through
        :func:`_class_and_parameters`, which remembers what a string
        resolves TO -- a class and its parameters, nothing mutable -- and
        never the accumulator built from it.
        """
        aggregator_class, parameters = _resolve(source)
        return aggregator_class(*parameters)

    @staticmethod
    def resolve_class(source: AggregatorSource) -> type[Aggregator]:
        """The aggregator CLASS a definition, string, or dict names.

        For the callers that want what an aggregator WOULD answer rather
        than an accumulator to answer it with: :attr:`output_value_type`
        and :meth:`preserves_domain` are both class-level, so an attribute
        that only knows an aggregator's name can describe its output
        without building one (gain#1133).

        It resolves through the same :func:`_resolve` as :meth:`build`,
        for every spelling and not just the memoised one, so the two
        cannot disagree about what a source names and a source refused
        there is refused here, in the same words.
        """
        return _resolve(source)[0]


class MaxAggregator(Aggregator):
    """Maximum value aggregator for genomic scores."""

    output_value_type: ClassVar[str | None] = "float"

    @classmethod
    def preserves_domain(
        cls, *, value_type: str | None = None,  # ruff: ignore[unused-class-method-argument]
    ) -> bool:
        return True

    def __init__(self) -> None:
        super().__init__()
        self.current_max = None

    def _add_internal(self, value: Any, count: int) -> None:
        if value is None:
            return
        if self.current_max is not None:
            self.current_max = max(value, self.current_max)
        else:
            self.current_max = value

        self.used_count += count

    def _clear_internal(self) -> None:
        self.current_max = None

    def get_final(self) -> Any:
        return self.current_max


class MinAggregator(Aggregator):
    """Minimum value aggregator for genomic scores."""

    output_value_type: ClassVar[str | None] = "float"

    @classmethod
    def preserves_domain(
        cls, *, value_type: str | None = None,  # ruff: ignore[unused-class-method-argument]
    ) -> bool:
        return True

    def __init__(self) -> None:
        super().__init__()
        self.current_min = None

    def _add_internal(self, value: Any, count: int) -> None:
        if value is None:
            return
        if self.current_min is not None:
            self.current_min = min(self.current_min, value)
        else:
            self.current_min = value

        self.used_count += count

    def _clear_internal(self) -> None:
        self.current_min = None

    def get_final(self) -> Any:
        return self.current_min


class MeanAggregator(Aggregator):
    """Aggregator for genomic scores that calculates mean value."""

    output_value_type: ClassVar[str | None] = "float"

    @classmethod
    def preserves_domain(
        cls, *, value_type: str | None = None,  # ruff: ignore[unused-class-method-argument]
    ) -> bool:
        return True

    def __init__(self) -> None:
        super().__init__()
        self.sum = 0

    def _add_internal(self, value: Any, count: int) -> None:
        if value is None:
            return

        self.sum += value * count
        self.used_count += count

    def _clear_internal(self) -> None:
        self.sum = 0

    def get_final(self) -> Any:
        if self.used_count > 0:
            return self.sum / self.used_count
        return None


class CountAggregator(Aggregator):
    """Aggregator that counts values."""

    output_value_type: ClassVar[str | None] = "int"

    def __init__(self) -> None:
        super().__init__()
        self.count = 0

    def _add_internal(self, value: Any, count: int) -> None:
        if value is None:
            return

        self.count += count

    def _clear_internal(self) -> None:
        self.count = 0

    def get_final(self) -> Any:
        if self.count > 0:
            return self.count
        return None


class ConcatAggregator(Aggregator):
    """Aggregator that concatenates all passed values.

    One of the three aggregators whose output is genuinely proportional to
    the aggregated weight (see also ``join`` and ``list``).  The weight is
    kept run-length encoded during the scan and expanded only in
    :meth:`get_final`.
    """

    output_value_type: ClassVar[str | None] = "str"

    def __init__(self) -> None:
        super().__init__()
        self.values: list[tuple[str, int]] = []

    def _add_internal(self, value: Any, count: int) -> None:
        if value is not None:
            self.values.append((str(value), count))
            self.used_count += count

    def _clear_internal(self) -> None:
        self.values.clear()

    def get_final(self) -> Any:
        if not self.values:
            return None

        out = "".join(value * count for value, count in self.values)
        if out == "":
            return None

        return out


class MedianAggregator(Aggregator):
    """Aggregator for genomic scores that calculates median value."""

    output_value_type: ClassVar[str | None] = "float"

    @classmethod
    def preserves_domain(
        cls, *, value_type: str | None = None,  # ruff: ignore[unused-class-method-argument]
    ) -> bool:
        return True

    def __init__(self) -> None:
        super().__init__()
        # (value, weight) pairs -- one entry per record, whatever the
        # record's weight.  The median is selected from them by rank at
        # the end, which is the same element the expanded sequence would
        # have yielded.
        self.values: list[tuple[Any, int]] = []

    def _add_internal(self, value: Any, count: int) -> None:
        if value is not None:
            self.values.append((value, count))
            self.used_count += count

    def _clear_internal(self) -> None:
        self.values.clear()

    def _select(self, rank: int) -> Any:
        """Return the value at ``rank`` in the weight-expanded order."""
        seen = 0
        for value, weight in self.values:
            seen += weight
            if rank < seen:
                return value
        raise IndexError(f"rank {rank} is beyond the aggregated weight")

    def get_final(self) -> Any:
        if not self.values or self.used_count <= 0:
            return None
        self.values.sort(key=operator.itemgetter(0))
        if self.used_count % 2 == 1:
            return self._select(math.floor(self.used_count / 2))

        first = self._select(int(self.used_count / 2) - 1)
        second = self._select(int(self.used_count / 2))
        if isinstance(first, str):
            assert isinstance(second, str)
            return first + second

        return (first + second) / 2


class ModeAggregator(Aggregator):
    """Aggregator for genomic scores that calculates mode value."""

    @classmethod
    def preserves_domain(
        cls, *, value_type: str | None = None,  # ruff: ignore[unused-class-method-argument]
    ) -> bool:
        return True

    def __init__(self) -> None:
        super().__init__()
        self.value_counts: dict[Any, int] = {}

    def _add_internal(self, value: Any, count: int) -> None:
        if value is not None:
            if value not in self.value_counts:
                self.value_counts[value] = 0
            self.value_counts[value] += count
            self.used_count += count

    def _clear_internal(self) -> None:
        self.value_counts.clear()

    def get_final(self) -> Any:
        if not self.value_counts:
            return None
        count_values: dict[Any, Any] = {}
        current_max = None
        for value, count in self.value_counts.items():
            if count not in count_values:
                count_values[count] = []

            count_values[count].append(value)

            if current_max is None or current_max < count:
                current_max = count
        modes = count_values[current_max]
        modes.sort()
        return modes[0]


class JoinAggregator(Aggregator):
    """Aggregator that joins all passed values using a separator."""

    parametrized: ClassVar[bool] = True
    default_parameter: ClassVar[str | None] = ","
    output_value_type: ClassVar[str | None] = "str"

    def __init__(self, separator: str):
        super().__init__()
        self.values: list[tuple[str, int]] = []
        self.separator = separator

    def _add_internal(self, value: Any, count: int) -> None:
        if value is not None:
            self.values.append((str(value), count))
            self.used_count += count

    def _clear_internal(self) -> None:
        self.values.clear()

    def get_final(self) -> Any:
        if not self.values:
            return None
        return self.separator.join(
            value
            for value, count in self.values
            for _ in range(count)
        )


class ListAggregator(Aggregator):
    """Aggregator that builds a list of all passed values."""

    output_value_type: ClassVar[str | None] = "list"

    def __init__(self) -> None:
        super().__init__()
        self.values: list[tuple[Any, int]] = []

    def _flatten(self, items: Any) -> Generator[Any, None, None]:
        for item in items:
            if (
                isinstance(item, Iterable)
                and not isinstance(item, (str, bytes))
            ):
                yield from self._flatten(item)
            else:
                yield item

    def _add_internal(self, value: Any, count: int) -> None:
        if value is not None:
            self.values.append((value, count))
            self.used_count += count

    def _clear_internal(self) -> None:
        self.values.clear()

    def get_final(self) -> Any:
        return list(self._flatten(
            value
            for value, count in self.values
            for _ in range(count)
        ))


class BoolAggregator(Aggregator):
    """Aggregator that returns True if any non-None value was added."""

    output_value_type: ClassVar[str | None] = "bool"

    def __init__(self) -> None:
        super().__init__()
        self.has_values = False

    def _add_internal(self, value: Any, count: int) -> None:
        if value is not None:
            self.has_values = True
            self.used_count += count

    def _clear_internal(self) -> None:
        self.has_values = False

    def get_final(self) -> bool:
        return self.has_values


class CounterAggregator(Aggregator):
    """Aggregator that counts values."""

    output_value_type: ClassVar[str | None] = "object"

    def __init__(self) -> None:
        super().__init__()
        self.counter: Counter = Counter()

    def _add_internal(self, value: Any, count: int) -> None:
        if value is None:
            return

        if not isinstance(value, list):
            self.counter[value] += count
        else:
            for item in value:
                self.counter[item] += count

    def _clear_internal(self) -> None:
        self.counter.clear()

    def get_final(self) -> Any:
        return dict(self.counter)


AGGREGATOR_CLASS_DICT: dict[str, type[Aggregator]] = {
    "max": MaxAggregator,
    "min": MinAggregator,
    "mean": MeanAggregator,
    "median": MedianAggregator,
    "count": CountAggregator,
    "concatenate": ConcatAggregator,
    "mode": ModeAggregator,
    "join": JoinAggregator,
    "list": ListAggregator,
    "bool": BoolAggregator,
    "value_count": CounterAggregator,
}


def _build_aggregator_schema() -> dict[str, Any]:
    """Derive the resource-config aggregator schema from the registry.

    The cerberus fragment that validates a score's ``position_aggregator`` /
    ``allele_aggregator`` / ``nucleotide_aggregator`` in a
    ``genomic_resource.yaml``.  Generated from ``AGGREGATOR_CLASS_DICT`` --
    it was once a second, hand-maintained list of names, and it drifted
    (``count`` was registered, buildable and documented, yet rejected in a
    resource YAML).  Registering an aggregator is now the only edit needed.

    A parametrized aggregator (``join``) is configured as ``name(parameter)``:
    its class needs the parameter, so the bare name cannot be built and is not
    accepted.  An empty separator -- ``join()`` -- is accepted, matching the
    definition parser, which builds it as the ``concatenate`` equivalent.

    The resource level is string-only, deliberately.  The ``{aggregator_type:
    ..., parameters: [...]}`` dict form is an annotation-pipeline spelling; a
    resource-level aggregator flows straight into ``ScoreDef``'s ``str | None``
    fields, so a resource configures an aggregator by its string form.
    """
    return {
        "type": "string",
        "oneof": [
            {
                "regex": rf"^{re.escape(name)}\(.*\)$"
                if aggregator_class.parametrized
                else rf"^{re.escape(name)}$",
            }
            for name, aggregator_class in AGGREGATOR_CLASS_DICT.items()
        ],
    }


AGGREGATOR_SCHEMA = _build_aggregator_schema()


def get_aggregator_class(aggregator: str) -> type[Aggregator]:
    """Return the aggregator class for the given aggregator name."""
    return AGGREGATOR_CLASS_DICT[aggregator]


def _resolve(
    source: AggregatorSource,
) -> tuple[type[Aggregator], tuple[Any, ...]]:
    """What an aggregator source names: the class and its parameters.

    The one place the accepted spellings are told apart, so a fourth one
    -- or a fix to how an existing one parses -- is a single edit.  A
    string takes the memoised path; anything else goes through
    :meth:`AggregatorDefinition.coerce`, which holds that cascade.
    """
    if isinstance(source, str):
        return _class_and_parameters(source)
    definition = AggregatorDefinition.coerce(source)
    return (
        get_aggregator_class(definition.aggregator_type),
        tuple(definition.parameters),
    )


@functools.lru_cache(maxsize=256)
def _class_and_parameters(
    raw: str,
) -> tuple[type[Aggregator], tuple[Any, ...]]:
    """What a string spelling resolves to: the class and its parameters.

    The memo behind :meth:`Aggregator.build`.  It holds the RESOLUTION of
    a name, which is pure and immutable, and never an accumulator, which
    is neither -- so every build still constructs afresh, and two builds
    of ``join(,)`` share nothing.  An invalid name raises (a malformed one
    as ``ValueError`` from the parser, an unknown one as ``KeyError`` from
    the registry) and ``lru_cache`` does not remember a raise, so a bad
    name is refused every time it is asked, in the same words.

    Bounded because the key is caller-supplied text: the registered names
    and their parametrized forms number a few dozen in any real run.
    """
    definition = AggregatorDefinition.from_string(raw)
    return (
        get_aggregator_class(definition.aggregator_type),
        tuple(definition.parameters),
    )


@dataclass
class AggregatorDefinition:
    """Parsed representation of an aggregator type string."""
    aggregator_type: str
    parameters: list[Any] = field(default_factory=list)

    @classmethod
    def from_string(cls, raw: str) -> AggregatorDefinition:
        """Parse an aggregator definition from a string.

        Format: ``name`` or ``name(parameter)``.
        """
        match = re.match(r"^(\w+)(?:\(([^)]*)\))?$", raw)
        if match is None:
            raise ValueError(f"Invalid aggregator definition: {raw!r}")
        name, parameter = match.group(1), match.group(2)
        if parameter is None:
            return cls(aggregator_type=name)
        return cls(aggregator_type=name, parameters=[parameter])

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AggregatorDefinition:
        """Construct an aggregator definition from a dictionary."""
        return cls(
            aggregator_type=data["aggregator_type"],
            parameters=list(data.get("parameters", [])),
        )

    @classmethod
    def coerce(cls, source: AggregatorSource) -> AggregatorDefinition:
        """Whichever of the three spellings arrived, as a definition.

        An aggregator reaches this module written three ways -- a name, a
        ``{aggregator_type, parameters}`` mapping, or an already parsed
        definition -- and every consumer wants the last of those.  The
        cascade that gets there is stated once here so a fourth spelling,
        or a fix to the parsing rules, is one edit rather than a hunt for
        the copies.  (:meth:`Aggregator.build` takes the string arm
        through a memo of its own, but that memo parses with
        :meth:`from_string` too -- a parsing fix is still one edit.)
        """
        if isinstance(source, AggregatorDefinition):
            return source
        if isinstance(source, str):
            return cls.from_string(source)
        return cls.from_dict(source)

    def as_dict(self) -> dict[str, Any]:
        """Serialize to a dictionary."""
        d: dict[str, Any] = {"aggregator_type": self.aggregator_type}
        if self.parameters:
            d["parameters"] = self.parameters
        return d

    def __str__(self) -> str:
        if self.parameters:
            return f"{self.aggregator_type}({self.parameters[0]})"
        return self.aggregator_type


AggregatorSource = AggregatorDefinition | str | dict[str, Any]


def aggregator_name(aggregator: AggregatorSource) -> str:
    """The canonical string spelling of an aggregator, whatever its form.

    An annotation pipeline may write an attribute's aggregator as a name,
    as a ``{aggregator_type, parameters}`` mapping, or as an already
    parsed :class:`AggregatorDefinition`; a resource may only write the
    name.  Everything downstream of the config -- a ``ScoreDef``'s field,
    a :class:`ScoreAggregationQuery`'s -- holds the name alone, so the
    spellings collapse here, on the way in.

    A name is returned as it stands rather than parsed and printed again.
    The round trip is exact for every registered aggregator and for the
    parametrized forms (pinned by
    ``test_the_three_aggregator_spellings_collapse_to_one_name``), so this
    is not about the answer differing -- it is that a caller holding a
    malformed name should meet the complaint where its aggregator is
    BUILT, naming the score, rather than here while a config is being
    serialised.
    """
    if isinstance(aggregator, str):
        return aggregator
    return str(AggregatorDefinition.coerce(aggregator))


@dataclass(frozen=True)
class ScoreAggregationQuery:
    """One score's reduction request, in the terms every kind shares.

    Names a score and how to reduce it, and nothing else; ``aggregator``
    of ``None`` resolves to the score's own default from its definition.
    Kind-neutral because nothing about "reduce this score with this
    aggregator" depends on how a kind lays its records out, so a position
    score, a fragment score and an allele score all ask the same thing
    here.

    It deliberately carries no ``none_value_replacement``.  That field
    speaks for a locus NO record covers, which only a kind that reads a
    value at every position of a region even has -- see
    :class:`PositionScoreAggregationQuery`, which adds it.  Keeping it off
    the base is what makes the base kind-neutral at all.
    """

    score: str
    aggregator: str | None = None


@dataclass(frozen=True)
class PositionScoreAggregationQuery(ScoreAggregationQuery):
    """The same request over a position score's expansion (gain#727).

    Adds the one part of a position score's request that no other kind
    can ask.  ``none_value_replacement`` substitutes for every null the
    per-position expansion holds -- uncovered and covered-but-NA alike --
    before the aggregator sees it; unset, nulls stay inert for every
    aggregator, all of which already skip ``None``.

    A position score answers with a value at every position of the
    queried region, so a position no record covers is still a position,
    and a caller may need it to count as a zero rather than go missing.
    A kind whose records are either in the result or not -- a fragment, an
    allele -- has no such position to speak for, and only the
    covered-but-NA half of the field would ever apply to it.  That is why
    it lives here and not on the base.

    Declaring only this field preserves the field order the flat dataclass
    had (``score``, ``aggregator``, ``none_value_replacement``), so every
    positional call site keeps its meaning.
    """

    none_value_replacement: ScoreValue | None = None


NUMERIC_ONLY_AGGREGATORS = {"max", "min", "mean", "median"}


def validate_aggregator(
    aggregator: AggregatorSource, value_type: str | None = None,
) -> None:
    """Raise ValueError for invalid aggregator or value type combinations."""
    try:
        Aggregator.build(aggregator)
    except Exception as ex:
        raise ValueError(
            f"Incorrect aggregator '{aggregator}'", ex) from ex
    if value_type is not None:
        definition = AggregatorDefinition.coerce(aggregator)
        if definition.aggregator_type in NUMERIC_ONLY_AGGREGATORS \
                and value_type not in {"int", "float"}:
            raise ValueError(
                f"Aggregator '{aggregator}' requires a numeric value "
                f"type (int or float), but attribute has type '{value_type}'",
            )
