"""Score aggregator classes and factory utilities."""

from __future__ import annotations

import abc
import math
import operator
import re
from collections import Counter
from collections.abc import Callable, Generator, Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any, ClassVar, cast


class WeightedValues:
    """A run-length encoded sequence of values: ``(value, weight)`` pairs.

    The contract between a score and an aggregator.  A score knows how
    many times each of its records counts -- a position-score record
    counts once per base pair of the queried region it covers, an allele
    line counts once, a CNV counts once however long it is -- and says so
    with a weight, rather than by handing over one copy of the value per
    occurrence.  The aggregator applies the weight in closed form.

    Holding a region as pairs is what makes aggregating it proportional
    to the number of records rather than to its length in base pairs.
    """

    __slots__ = ("pairs",)

    def __init__(self, pairs: Iterable[tuple[Any, int]] = ()) -> None:
        self.pairs: list[tuple[Any, int]] = list(pairs)

    def add(self, value: Any, weight: int = 1) -> None:
        """Append a value occurring ``weight`` times."""
        self.pairs.append((value, weight))

    def expand(self) -> list[Any]:
        """Return the values one copy per unit of weight, in order.

        The plain list this stands in for -- built only when something
        really does need every copy.
        """
        return [
            value
            for value, weight in self.pairs
            for _ in range(weight)
        ]

    def __iter__(self) -> Iterator[tuple[Any, int]]:
        return iter(self.pairs)

    def __len__(self) -> int:
        """Return the number of records, not the total weight."""
        return len(self.pairs)

    def __bool__(self) -> bool:
        return bool(self.pairs)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, WeightedValues):
            return self.pairs == other.pairs
        return NotImplemented

    __hash__ = None  # type: ignore[assignment]  # mutable, like a list

    def __repr__(self) -> str:
        return f"WeightedValues({self.pairs!r})"


class Aggregator(abc.ABC):
    """Base class for score aggregators.

    **Reuse contract.** An aggregator is a mutable accumulator, and an
    annotator builds exactly one instance per configured attribute and
    reuses it for every annotated variant: :meth:`aggregate` and
    :meth:`aggregate_weighted` clear the state at the start of each call
    rather than the caller building a fresh instance.  That is correct
    single-threaded and is **not** thread-safe -- two threads annotating
    through the same annotator would interleave their values into one
    accumulator.  Annotators are single-threaded by construction (a
    pipeline is used by one worker at a time); a caller that wants
    concurrency must give each thread its own pipeline.
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
        cls, *, value_type: str | None = None,  # noqa: ARG003
    ) -> bool:
        """Return True if output stays within the source value domain."""
        return False

    def __call__(self) -> Any:
        return self.get_final()

    def add(self, value: Any, count: int = 1) -> None:
        """Add a value to the aggregator, weighted by ``count``.

        ``count`` is the number of times the value is deemed to occur --
        the number of base pairs a position-score record spans, for
        instance.  It is applied in closed form: adding a value with a
        weight of ``n`` produces the same result as adding it ``n`` times,
        without doing ``n`` units of work.  The one exception is ``mean``,
        which is *more* accurate weighted than replicated: it rounds once
        per record rather than once per base.  See :meth:`_add_internal`.
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

    def aggregate_weighted(
        self, values: Iterable[tuple[Any, int]] | None,
    ) -> Any:
        """Clear state, add all weighted values, return the final result.

        ``values`` is a stream of ``(value, weight)`` pairs -- one pair per
        record, the weight being how many times that record's value counts.
        The result is what :meth:`aggregate` would return for the expanded
        sequence, except for ``mean``, which is more accurate here.
        """
        self.clear()
        if values is None:
            return self.get_final()
        for value, weight in values:
            self.add(value, weight)
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
        """Build an aggregator from a definition, string, or dict."""
        if isinstance(source, AggregatorDefinition):
            definition = source
        elif isinstance(source, str):
            definition = AggregatorDefinition.from_string(source)
        else:
            definition = AggregatorDefinition.from_dict(source)
        aggregator_class = get_aggregator_class(definition.aggregator_type)
        if definition.parameters:
            return aggregator_class(*definition.parameters)
        return aggregator_class()


class MaxAggregator(Aggregator):
    """Maximum value aggregator for genomic scores."""

    output_value_type: ClassVar[str | None] = "float"

    @classmethod
    def preserves_domain(
        cls, *, value_type: str | None = None,  # noqa: ARG003
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
        cls, *, value_type: str | None = None,  # noqa: ARG003
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
        cls, *, value_type: str | None = None,  # noqa: ARG003
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


class CoverageAggregator(Aggregator):
    """Total weight of the non-``None`` values -- how much data there was.

    For a position score queried over a region, a record's weight is the
    number of base pairs of the region it covers, so this is exactly the
    number of base pairs that carried a value: the region's *coverage*.

    Deliberately **not** registered in :data:`AGGREGATOR_CLASS_DICT`.  It
    is not an alternative way of summarising a score -- it summarises how
    much of the score there was -- so it is not something a resource or a
    pipeline names in a ``position_aggregator``; it backs the dedicated
    coverage attribute an annotator declares.  Unlike ``count`` it reports
    ``0`` rather than ``None`` for a region that carried nothing: a
    fully-uncovered region is a measurement, not a missing one.
    """

    output_value_type: ClassVar[str | None] = "int"

    def _add_internal(self, value: Any, count: int) -> None:
        if value is not None:
            self.used_count += count

    def _clear_internal(self) -> None:
        """Keep no state beyond ``used_count``, which the base clears."""

    def get_final(self) -> int:
        return self.used_count


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
        cls, *, value_type: str | None = None,  # noqa: ARG003
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
        cls, *, value_type: str | None = None,  # noqa: ARG003
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


def get_aggregator_class(aggregator: str) -> Callable[[], Aggregator]:
    """Return the aggregator class for the given aggregator name."""
    return AGGREGATOR_CLASS_DICT[aggregator]


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
        if isinstance(aggregator, AggregatorDefinition):
            definition = aggregator
        elif isinstance(aggregator, str):
            definition = AggregatorDefinition.from_string(aggregator)
        else:
            definition = AggregatorDefinition.from_dict(aggregator)
        if definition.aggregator_type in NUMERIC_ONLY_AGGREGATORS \
                and value_type not in {"int", "float"}:
            raise ValueError(
                f"Aggregator '{aggregator}' requires a numeric value "
                f"type (int or float), but attribute has type '{value_type}'",
            )
